"""Tests for adobe_portfolio_scraper. Run with: python -m unittest discover -s tests -v"""

import math
import unittest
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import adobe_portfolio_scraper as aps

SITE = "https://jane.myportfolio.com"
CDN = "https://cdn.myportfolio.com/aaaaaaaa-0000-4000-8000-000000000000"


def uid(n):
    return f"{n:08d}-0000-4000-8000-{n:012d}"


def lightbox(n, ext="jpg"):
    """A standalone image module, marked up the way Adobe Portfolio renders it."""
    a = uid(n)
    return f"""
    <div class="project-module module image project-module-image">
      <div class="js-lightbox" data-src="{CDN}/{a}.{ext}?h=orig{n}">
        <img class="js-lazy" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP"
             data-src="{CDN}/{a}_rw_3840.{ext}?h=big{n}"
             data-srcset="{CDN}/{a}_rw_600.{ext}?h=s{n} 600w,{CDN}/{a}_rw_3840.{ext}?h=big{n} 3840w,">
      </div>
    </div>"""


def grid_item(n, ext="png"):
    """A grid gallery item: the page shows 600px, the lightbox script holds 1200px."""
    a = uid(n)
    return f"""
    <div class="js-grid-item-container" data-width="1200">
      <script type="text/html" class="js-lightbox-slide-content">
        <div><img src="{CDN}/{a}_rw_1200.{ext}?h=w1200" srcset="{CDN}/{a}_rw_1200.{ext}?h=w1200 1200w,"></div>
      </script>
      <img class="grid__item-image js-lazy" src="data:image/gif;base64,R0lGOD"
           data-src="{CDN}/{a}_rw_600.{ext}?h=w600" data-srcset="{CDN}/{a}_rw_600.{ext}?h=w600 600w,">
    </div>"""


def cover(href, n):
    a = uid(n)
    return f"""
    <a class="project-cover js-project-cover-touch" href="{href}">
      <img class="cover__img js-lazy"
           data-srcset="{CDN}/{a}_rwc_0x0x1200x1200x600.jpg?h=c600 600w,{CDN}/{a}_rwc_0x0x1200x1200x1200.jpg?h=c1200 1200w,">
    </a>"""


def page(title, body):
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


class ExtractImagesTest(unittest.TestCase):
    def setUp(self):
        body = (
            cover("/other-project", 9)
            + lightbox(1)
            + '<div class="project-module module tree"><div class="tree-child-wrapper">'
            + lightbox(2, "png")
            + "</div></div>"
            + '<div class="project-module module media_collection"><div class="grid--main">'
            + grid_item(3)
            + "</div></div>"
            + '<div class="project-module module image">'
            + f'<img data-srcset="{CDN}/{uid(4)}_rw_600.jpg?h=e1 600w,{CDN}/{uid(4)}_rw_1920.jpg?h=e2 1920w,">'
            + "</div>"
            + '<div class="project-module module text"><p>No images here.</p></div>'
        )
        self.soup = BeautifulSoup(page("Jane Doe - Lamp", body), "html.parser")

    def test_finds_every_project_image_in_page_order(self):
        images = aps.extract_images(self.soup)
        self.assertEqual([i.asset_id for i in images], [uid(1), uid(2), uid(3), uid(4)])
        self.assertEqual([i.kind for i in images], ["image", "image", "grid", "image"])

    def test_prefers_original_upload_over_resized_copies(self):
        images = aps.extract_images(self.soup)
        self.assertEqual(images[0].url, f"{CDN}/{uid(1)}.jpg?h=orig1")
        self.assertEqual(images[1].url, f"{CDN}/{uid(2)}.png?h=orig2")

    def test_grid_uses_largest_copy_from_lightbox_markup(self):
        grid = aps.extract_images(self.soup)[2]
        self.assertEqual(grid.url, f"{CDN}/{uid(3)}_rw_1200.png?h=w1200")
        self.assertEqual(grid.ext, "png")

    def test_image_without_lightbox_uses_largest_srcset_entry(self):
        self.assertEqual(aps.extract_images(self.soup)[3].url, f"{CDN}/{uid(4)}_rw_1920.jpg?h=e2")

    def test_covers_are_opt_in(self):
        images = aps.extract_images(self.soup, include_covers=True)
        self.assertEqual(len(images), 5)
        self.assertEqual(images[0].kind, "cover")
        self.assertEqual(images[0].name_hint, "other-project")
        self.assertTrue(images[0].url.endswith("x1200.jpg?h=c1200"))


class HelperTest(unittest.TestCase):
    def test_rendition_size_ranks_originals_highest(self):
        self.assertEqual(aps.rendition_size(f"{CDN}/{uid(1)}.jpg?h=x"), math.inf)
        self.assertEqual(aps.rendition_size(f"{CDN}/{uid(1)}_rw_1920.jpg?h=x"), 1920)
        self.assertEqual(aps.rendition_size(f"{CDN}/{uid(1)}_rwc_0x0x1200x1200x4096.png?h=x"), 4096)
        self.assertEqual(aps.rendition_size("https://example.com/logo.svg"), -1)

    def test_internal_link_keeps_only_pages_on_the_same_site(self):
        root = urlparse(SITE + "/")
        base = SITE + "/work"
        self.assertEqual(aps.internal_link(base, "/lamp/", root), SITE + "/lamp")
        self.assertEqual(aps.internal_link(base, "lamp?ref=nav#top", root), SITE + "/lamp")
        self.assertIsNone(aps.internal_link(base, "https://www.instagram.com/jane", root))
        self.assertIsNone(aps.internal_link(base, "mailto:jane@example.com", root))
        self.assertIsNone(aps.internal_link(base, "/dist/css/main.css", root))

    def test_slugify(self):
        self.assertEqual(aps.slugify("Lamp × Chair"), "lamp-chair")
        self.assertEqual(aps.slugify("  Café Chair (2024) "), "cafe-chair-2024")
        self.assertEqual(aps.slugify("***"), "")

    def test_normalize_start_url(self):
        self.assertEqual(aps.normalize_start_url("jane.myportfolio.com/"), SITE + "/")
        with self.assertRaises(ValueError):
            aps.normalize_start_url("ftp://jane.myportfolio.com")

    def test_page_title_strips_site_name(self):
        self.assertEqual(aps.page_title("Jane Doe - Lamp - Final", "Jane Doe"), "Lamp - Final")
        self.assertEqual(aps.page_title("Jane Doe", "Jane Doe"), "")

    def test_folder_name_keeps_wording_but_strips_invalid_characters(self):
        self.assertEqual(aps.folder_name("iSoap 1"), "iSoap 1")
        self.assertEqual(aps.folder_name('Lamp: "Study" / Chair?'), "Lamp Study Chair")
        self.assertEqual(aps.folder_name("Trailing dot. "), "Trailing dot")
        self.assertEqual(aps.folder_name("***"), "untitled")

    def test_titleize_slug(self):
        self.assertEqual(aps.titleize_slug("goh-fukugo"), "Goh Fukugo")
        self.assertEqual(aps.titleize_slug("l1"), "L1")


class PlanDownloadsTest(unittest.TestCase):
    def _pages(self):
        def img(n, kind="image", hint=None):
            return aps.ImageRef(url=f"{CDN}/{uid(n)}.jpg?h=x", asset_id=uid(n), ext="jpg", kind=kind, name_hint=hint)

        return [
            aps.Page(SITE + "/lamp", "Lamp Study", [img(1), img(2)]),
            aps.Page(SITE + "/lamp-2", "Lamp Study", [img(3)]),
            aps.Page(SITE + "/iso", "iSoap 1", [img(4, "cover", "lamp")]),
        ], img

    def test_grouped_by_default_into_a_folder_per_page(self):
        pages, _ = self._pages()
        paths = [str(d.path.relative_to(Path("out"))) for d in aps.plan_downloads(pages, Path("out"))]
        self.assertEqual(paths, [
            str(Path("Lamp Study") / "01.jpg"),
            str(Path("Lamp Study") / "02.jpg"),
            str(Path("Lamp Study (2)") / "01.jpg"),
            str(Path("iSoap 1") / "cover.jpg"),
        ])

    def test_flat_mode_uses_prefixed_names_in_one_folder(self):
        pages, _ = self._pages()
        names = [d.path.name for d in aps.plan_downloads(pages, Path("out"), flat=True)]
        self.assertEqual(names, ["lamp-study-01.jpg", "lamp-study-02.jpg", "lamp-study-2-01.jpg", "lamp-cover.jpg"])


class FakeResponse:
    def __init__(self, url, text="", status=200):
        self.url = url
        self.text = text
        self.content = text.encode()
        self.status_code = status
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error for {self.url}")


class FakeSession:
    def __init__(self, site):
        self.site = site
        self.requested = []

    def get(self, url, timeout=None, **kwargs):
        self.requested.append(url)
        if url in self.site:
            return FakeResponse(url, self.site[url])
        return FakeResponse(url, status=404)


class CrawlTest(unittest.TestCase):
    def setUp(self):
        nav = '<nav><a href="/pdf-portfolio">PDF</a><a href="https://instagram.com/jane">IG</a></nav>'
        self.session = FakeSession({
            SITE + "/": page("Jane Doe", nav + cover("/lamp", 90) + cover("/chair", 91)),
            SITE + "/lamp": page("Jane Doe - Lamp", nav + lightbox(1) + cover("/chair", 91)),
            SITE + "/chair": page("Jane Doe - Chair", nav + lightbox(2)),
            SITE + "/pdf-portfolio": page("Jane Doe - PDF Portfolio", nav + lightbox(1) + lightbox(3)),
        })
        self.pages = aps.crawl(self.session, SITE, delay=0, log=lambda *_: None)

    def test_visits_projects_before_other_pages(self):
        self.assertEqual([p.title for p in self.pages], ["Lamp", "Chair", "PDF Portfolio"])

    def test_images_repeated_on_later_pages_are_skipped(self):
        self.assertEqual([i.asset_id for i in self.pages[2].images], [uid(3)])

    def test_never_leaves_the_site(self):
        self.assertTrue(all(url.startswith(SITE) for url in self.session.requested))


if __name__ == "__main__":
    unittest.main()
