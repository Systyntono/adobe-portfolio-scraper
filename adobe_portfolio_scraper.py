#!/usr/bin/env python3
"""Download full-resolution project images from an Adobe Portfolio website.

The scraper starts at a site's home page, visits every page it can reach
(project covers first, then navigation links, then the sitemap), and saves the
largest available copy of each project image into a single folder.

Usage:
    adobe-portfolio-scraper https://yourname.myportfolio.com -o portfolio-images
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import re
import sys
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import ParseResult, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

__version__ = "1.0.0"

USER_AGENT = (
    f"adobe-portfolio-scraper/{__version__} "
    "(+https://github.com/Systyntono/adobe-portfolio-scraper)"
)

# Adobe Portfolio stores each upload under a UUID. Resized copies append a
# suffix such as "_rw_1920" (resize) or "_rwc_0x0x1200x1200x4096" (crop); the
# original upload has no suffix. Every URL carries a signed "?h=" token, so URLs
# are always used exactly as the page provides them, never rebuilt.
ASSET_RE = re.compile(
    r"/(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"(?P<suffix>_[A-Za-z0-9_]+)?\.(?P<ext>[A-Za-z0-9]+)$"
)
URL_RE = re.compile(r"https?://[^\s\"'<>,]+")
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "tif", "tiff"}

# Elements that own exactly one image: standalone image modules (whose lightbox
# links the original upload) and the items of grid galleries.
IMAGE_CONTAINERS = ".js-lightbox[data-src], .js-grid-item-container"
PROJECT_COVER = "a.project-cover"


@dataclass
class ImageRef:
    """One image found on a page."""

    url: str
    asset_id: str
    ext: str
    kind: str  # "image", "grid" or "cover"
    name_hint: str | None = None


@dataclass
class Page:
    """A crawled page and the images that were first seen on it."""

    url: str
    title: str
    images: list[ImageRef] = field(default_factory=list)


@dataclass
class Download:
    image: ImageRef
    page: Page
    path: Path


@dataclass
class DownloadStats:
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    bytes: int = 0
    status: dict[Path, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# URLs and names
# --------------------------------------------------------------------------- #


def normalize_start_url(url: str) -> str:
    """Add a scheme if missing and drop any query string or fragment."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not a valid website address: {url}")
    path = parts.path.rstrip("/") or "/"
    return urlunparse((parts.scheme.lower(), parts.netloc, path, "", "", ""))


def internal_link(base_url: str, href: str, root: ParseResult) -> str | None:
    """Resolve ``href`` and return it if it is another page on the same site."""
    parts = urlparse(urljoin(base_url, href.strip()))
    if parts.scheme not in ("http", "https") or parts.netloc.lower() != root.netloc.lower():
        return None
    path = parts.path.rstrip("/") or "/"
    last_segment = path.rsplit("/", 1)[-1]
    if "." in last_segment or path.startswith("/dist/"):
        return None  # a stylesheet, script, sitemap or other file, not a page
    return urlunparse((root.scheme, root.netloc, path, "", "", ""))


def slugify(text: str, max_length: int = 60) -> str:
    """Turn a page title into a filename-safe slug: "Lamp × Chair" -> "lamp-chair"."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:max_length].rstrip("-")


def path_slug(url: str) -> str:
    segment = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return slugify(segment) or "home"


def human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


# --------------------------------------------------------------------------- #
# Finding images on a page
# --------------------------------------------------------------------------- #


def asset_info(url: str) -> tuple[str, str, str] | None:
    """Return ``(asset_id, size_suffix, extension)`` for a portfolio image URL."""
    match = ASSET_RE.search(urlparse(url).path)
    if not match or match["ext"].lower() not in IMAGE_EXTENSIONS:
        return None
    return match["id"], match["suffix"] or "", match["ext"].lower()


def rendition_size(url: str) -> float:
    """Rank an image URL by resolution: the original upload beats any resized copy."""
    info = asset_info(url)
    if info is None:
        return -1
    suffix = info[1]
    if not suffix:
        return math.inf
    # The output width is always the last number: "_rw_1920" or the crop
    # "_rwc_{x}x{y}x{w}x{h}x{width}", whose box size must not outrank it.
    numbers = re.findall(r"\d+", suffix)
    return int(numbers[-1]) if numbers else 0


def candidate_urls(tag: Tag) -> list[str]:
    """Every URL referenced by ``tag`` or its descendants.

    Grid galleries keep their lightbox markup inside ``<script type="text/html">``,
    so script bodies are searched as text too.
    """
    urls: list[str] = []
    for node in [tag, *tag.find_all(True)]:
        for attr in ("data-src", "src", "data-srcset", "srcset"):
            value = node.get(attr)
            if isinstance(value, str):
                urls.extend(URL_RE.findall(value))
        if node.name == "script" and node.string:
            urls.extend(URL_RE.findall(html.unescape(node.string)))
    return urls


def best_image(tag: Tag, kind: str, name_hint: str | None = None) -> ImageRef | None:
    ranked = [(rendition_size(url), url) for url in candidate_urls(tag)]
    ranked = [item for item in ranked if item[0] >= 0]
    if not ranked:
        return None
    url = max(ranked, key=lambda item: item[0])[1]
    asset_id, _, ext = asset_info(url)  # type: ignore[misc]
    return ImageRef(url=url, asset_id=asset_id, ext=ext, kind=kind, name_hint=name_hint)


def extract_images(soup: BeautifulSoup, include_covers: bool = False) -> list[ImageRef]:
    """Find the project images on a page, in the order they appear."""
    owners = soup.select(f"{IMAGE_CONTAINERS}, {PROJECT_COVER}")
    owner_ids = {id(tag) for tag in owners}
    images: list[ImageRef] = []

    for tag in soup.select(f"{IMAGE_CONTAINERS}, {PROJECT_COVER}, .project-module img"):
        classes = tag.get("class") or []
        name_hint = None
        if tag.name == "img":
            # Images inside a lightbox, grid item or cover are handled by that owner.
            if any(id(parent) in owner_ids for parent in tag.parents):
                continue
            kind = "image"
        elif tag.name == "a" and "project-cover" in classes:
            if not include_covers:
                continue
            kind, name_hint = "cover", path_slug(str(tag.get("href", "")))
        elif "js-grid-item-container" in classes:
            kind = "grid"
        else:
            kind = "image"

        image = best_image(tag, kind, name_hint)
        if image:
            images.append(image)
    return images


# --------------------------------------------------------------------------- #
# Crawling
# --------------------------------------------------------------------------- #


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def sitemap_urls(session: requests.Session, root: ParseResult, timeout: float) -> list[str]:
    url = urlunparse((root.scheme, root.netloc, "/sitemap.xml", "", "", ""))
    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        tree = ET.fromstring(response.content)
    except (requests.RequestException, ET.ParseError):
        return []
    return [el.text.strip() for el in tree.iter() if el.tag.endswith("loc") and el.text]


def page_title(raw_title: str, site_name: str) -> str:
    """Strip the "Site Name - " prefix Adobe Portfolio adds to every page title."""
    prefix = f"{site_name} - "
    if site_name and raw_title.startswith(prefix):
        return raw_title[len(prefix):].strip()
    return "" if raw_title == site_name else raw_title


def crawl(
    session: requests.Session,
    start_url: str,
    *,
    include_covers: bool = False,
    delay: float = 0.5,
    timeout: float = 60.0,
    max_pages: int = 500,
    log: Callable[[str], None] = print,
) -> list[Page]:
    """Visit every reachable page on the site and collect its images.

    Project covers are followed before other links, so an image that also
    appears on a later page (a "PDF portfolio" page, say) stays credited to
    its project. Pages without any new images are left out of the result.
    """
    start_url = normalize_start_url(start_url)
    root = urlparse(start_url)
    covers: deque[str] = deque([start_url])
    links: deque[str] = deque()
    fallback: deque[str] = deque(
        link
        for loc in sitemap_urls(session, root, timeout)
        if (link := internal_link(start_url, loc, root))
    )

    seen_pages: set[str] = set()
    seen_assets: set[str] = set()
    site_name: str | None = None
    pages: list[Page] = []

    while covers or links or fallback:
        url = (covers or links or fallback).popleft()
        if url in seen_pages:
            continue
        if len(seen_pages) >= max_pages:
            log(f"  Stopped after {max_pages} pages (raise --max-pages to crawl more).")
            break
        if seen_pages and delay:
            time.sleep(delay)
        seen_pages.add(url)

        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            log(f"  ! could not load {url}: {exc}")
            continue
        content_type = response.headers.get("Content-Type", "")
        if content_type and "html" not in content_type:
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        raw_title = soup.title.get_text(" ", strip=True) if soup.title else ""
        if site_name is None:
            site_name = raw_title

        for anchor in soup.find_all("a", href=True):
            link = internal_link(url, anchor["href"], root)
            if link and link not in seen_pages:
                is_cover = "project-cover" in (anchor.get("class") or [])
                (covers if is_cover else links).append(link)

        images: list[ImageRef] = []
        repeated = 0
        for image in extract_images(soup, include_covers):
            key = f"cover:{image.asset_id}" if image.kind == "cover" else image.asset_id
            if key in seen_assets:
                repeated += 1
                continue
            seen_assets.add(key)
            images.append(image)

        title = page_title(raw_title, site_name) or path_slug(url)
        note = f", {repeated} repeated from earlier pages" if repeated else ""
        log(f"  {len(images):>4} images   {title} ({urlparse(url).path}){note}")
        if images:
            pages.append(Page(url=url, title=title, images=images))

    return pages


# --------------------------------------------------------------------------- #
# Downloading
# --------------------------------------------------------------------------- #


def _unique(base: str, used: set[str]) -> str:
    candidate, n = base, 1
    while candidate in used:
        n += 1
        candidate = f"{base}-{n}"
    used.add(candidate)
    return candidate


def plan_downloads(pages: list[Page], output_dir: Path) -> list[Download]:
    """Give every image a readable, unique filename such as ``lamp-study-03.jpg``."""
    used_prefixes: set[str] = set()
    used_stems: set[str] = set()
    downloads: list[Download] = []

    for page in pages:
        prefix = _unique(slugify(page.title) or path_slug(page.url), used_prefixes)
        width = max(2, len(str(sum(image.kind != "cover" for image in page.images))))
        number = 0
        for image in page.images:
            if image.kind == "cover":
                stem = f"{image.name_hint or prefix}-cover"
            else:
                number += 1
                stem = f"{prefix}-{number:0{width}d}"
            stem = _unique(stem, used_stems)
            downloads.append(Download(image, page, output_dir / f"{stem}.{image.ext}"))
    return downloads


_thread_state = threading.local()


def _thread_session() -> requests.Session:
    session = getattr(_thread_state, "session", None)
    if session is None:
        session = _thread_state.session = make_session()
    return session


def fetch_file(url: str, dest: Path, timeout: float) -> int:
    """Stream ``url`` into ``dest`` through a temporary file; return the byte count."""
    partial = dest.with_name(dest.name + ".part")
    try:
        with _thread_session().get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if content_type and not content_type.startswith("image/"):
                raise ValueError(f"expected an image but got {content_type}")
            size = 0
            with partial.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    fh.write(chunk)
                    size += len(chunk)
            expected = response.headers.get("Content-Length")
            if expected and "Content-Encoding" not in response.headers and int(expected) != size:
                raise OSError(f"incomplete download ({size} of {expected} bytes)")
        partial.replace(dest)
        return size
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def download_all(
    downloads: list[Download],
    *,
    workers: int = 4,
    overwrite: bool = False,
    timeout: float = 60.0,
    log: Callable[[str], None] = print,
) -> DownloadStats:
    stats = DownloadStats()
    pending: list[Download] = []
    for item in downloads:
        if item.path.exists() and not overwrite:
            stats.skipped += 1
            stats.status[item.path] = "skipped"
        else:
            pending.append(item)
    if stats.skipped:
        log(f"Skipping {stats.skipped} file(s) already in the folder (use --overwrite to replace them).")

    width = len(str(len(pending)))
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(fetch_file, d.image.url, d.path, timeout): d for d in pending}
        for done, future in enumerate(as_completed(futures), start=1):
            item = futures[future]
            label = f"  [{done:>{width}}/{len(pending)}] {item.path.name}"
            try:
                size = future.result()
            except Exception as exc:
                stats.failed += 1
                stats.status[item.path] = f"failed: {exc}"
                log(f"{label}  FAILED: {exc}")
            else:
                stats.downloaded += 1
                stats.bytes += size
                stats.status[item.path] = "downloaded"
                log(f"{label}  {human_size(size)}")
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return stats


def write_manifest(path: Path, downloads: list[Download], stats: DownloadStats) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "status", "kind", "page_title", "page_url", "image_url", "asset_id"])
        for item in downloads:
            writer.writerow([
                item.path.name,
                stats.status.get(item.path, ""),
                item.image.kind,
                item.page.title,
                item.page.url,
                item.image.url,
                item.image.asset_id,
            ])


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _non_negative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("cannot be negative")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adobe-portfolio-scraper",
        description="Download full-resolution project images from an Adobe Portfolio website into one folder.",
        epilog="Only use this on portfolios you own or have permission to download. See TERMS_OF_USE.md.",
    )
    parser.add_argument("url", help="home page of the portfolio, e.g. https://yourname.myportfolio.com")
    parser.add_argument("-o", "--output", default="portfolio-images", metavar="DIR",
                        help="folder to save images into (default: %(default)s)")
    parser.add_argument("--include-covers", action="store_true",
                        help="also save the cropped project cover thumbnails")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be downloaded without saving anything")
    parser.add_argument("--overwrite", action="store_true",
                        help="download again even if a file already exists in the output folder")
    parser.add_argument("--manifest", action="store_true",
                        help="write manifest.csv recording where each file came from")
    parser.add_argument("--workers", type=_positive_int, default=4, metavar="N",
                        help="number of parallel downloads (default: %(default)s)")
    parser.add_argument("--delay", type=_non_negative_float, default=0.5, metavar="SEC",
                        help="pause between page requests (default: %(default)s)")
    parser.add_argument("--timeout", type=_positive_float, default=60.0, metavar="SEC",
                        help="network timeout per request (default: %(default)s)")
    parser.add_argument("--max-pages", type=_positive_int, default=500, metavar="N",
                        help="stop crawling after this many pages (default: %(default)s)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    try:
        start_url = normalize_start_url(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    output_dir = Path(args.output).expanduser()
    started = time.monotonic()

    try:
        print(f"Crawling {start_url}")
        pages = crawl(
            make_session(),
            start_url,
            include_covers=args.include_covers,
            delay=args.delay,
            timeout=args.timeout,
            max_pages=args.max_pages,
        )
        downloads = plan_downloads(pages, output_dir)
        if not downloads:
            print("\nNo project images found. Check that this is an Adobe Portfolio site "
                  "and that its projects are public (not password-protected).")
            return 1

        print(f"\nFound {len(downloads)} images on {len(pages)} pages.")
        if args.dry_run:
            for item in downloads:
                print(f"  {item.path.name:<44} from {urlparse(item.page.url).path}")
            return 0

        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Saving to {output_dir.resolve()}\n")
        stats = download_all(downloads, workers=args.workers, overwrite=args.overwrite, timeout=args.timeout)
        if args.manifest:
            write_manifest(output_dir / "manifest.csv", downloads, stats)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    elapsed = time.monotonic() - started
    print(
        f"\nDone in {elapsed:.1f}s: {stats.downloaded} downloaded ({human_size(stats.bytes)}), "
        f"{stats.skipped} already present, {stats.failed} failed."
    )
    return 1 if stats.failed else 0


if __name__ == "__main__":
    sys.exit(main())
