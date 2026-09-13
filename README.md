# Adobe Portfolio Scraper

Download every project image from an Adobe Portfolio website at full resolution, sorted into a folder per project, with one command.

> [!IMPORTANT]
> Only use this tool on portfolios **you own** or have **explicit permission** to download from. Read the [Terms of Use](TERMS_OF_USE.md) before using it. This project is not affiliated with, endorsed by, or sponsored by Adobe Inc.

Good for backing up your own portfolio, moving your work to another website, or keeping an offline archive of your projects.

## Features

- **Finds every project for you.** Give it the home page. It follows the project covers, the navigation menu, and the site's sitemap.
- **Sorts images by project**, into its own subfolder named after the project: `Lamp Study/`, `Chair/`, …
- **Saves the original upload.** Where the page links the original file, that's what gets saved. A 4800 × 2700 photo is saved at 4800 × 2700, not as the smaller copy shown on the page.
- **Handles every image layout:** single images, side-by-side rows, and grid galleries.
- **No duplicates.** An image that appears on more than one page (for example, a "PDF portfolio" page that reuses project photos) is downloaded once and kept with the project it came from.
- **Readable filenames**, numbered in page order: `01.jpg`, `02.jpg`, …
- **Safe to re-run.** Files already in the folder are skipped. Interrupted downloads never leave broken images behind.
- **Polite and reliable:** a delay between page requests, retries with backoff, and parallel image downloads.

## Requirements

- Python 3.10 or newer
- An internet connection

## Installation

```bash
git clone https://github.com/Systyntono/adobe-portfolio-scraper.git
cd adobe-portfolio-scraper
pip install .
```

This installs the `adobe-portfolio-scraper` command. If you'd rather not install it, you can also run the script directly:

```bash
pip install -r requirements.txt
python adobe_portfolio_scraper.py https://yourname.myportfolio.com
```

## Usage

```bash
adobe-portfolio-scraper https://yourname.myportfolio.com -o portfolio-images
```

On Windows, wrap paths that contain spaces in quotes:

```powershell
adobe-portfolio-scraper https://yourname.myportfolio.com -o "C:\Users\you\Pictures\My Portfolio"
```

Custom domains work too. Pass whatever address your portfolio lives at.

Example output:

```text
Crawling https://yourname.myportfolio.com/
     0 images   home (/)
    12 images   Lamp Study (/lamp-study)
    27 images   Chair (/chair)
    31 images   PDF Portfolio (/pdf-portfolio), 18 repeated from earlier pages
     0 images   contact (/contact)

Found 70 images on 3 pages.
Saving to C:\Users\you\Pictures\My Portfolio

  [ 1/70] Lamp Study/01.jpg  2.3 MB
  [ 2/70] Lamp Study/02.jpg  1.8 MB
  ...

Done in 41.3s: 70 downloaded (148.2 MB), 0 already present, 0 failed.
```

### Options

| Option | Default | Description |
| --- | --- | --- |
| `url` | *(required)* | Home page of the portfolio. |
| `-o`, `--output DIR` | `portfolio-images` | Folder to save images into. Created if it doesn't exist. |
| `--flat` | off | Save all images directly in the output folder instead of a subfolder per project. |
| `--dry-run` | off | List what would be downloaded without saving anything. |
| `--include-covers` | off | Also save the cropped cover thumbnails shown on gallery pages. |
| `--overwrite` | off | Download again even if a file with the same name already exists. |
| `--manifest` | off | Write `manifest.csv` recording each file's source page and image URL. |
| `--workers N` | `4` | Number of images to download at the same time. |
| `--delay SEC` | `0.5` | Pause between page requests. |
| `--timeout SEC` | `60` | Network timeout for each request. |
| `--max-pages N` | `500` | Stop crawling after this many pages. |
| `--version` | | Print the version and exit. |

## How it works

1. **Crawl.** Starting at the URL you give it, the scraper visits every page on the same website. Project cover links go first, then other internal links (navigation, footer), then any extra pages listed in `sitemap.xml`. It never follows links to other websites.
2. **Find images.** On each page it looks at the project content:

   | Layout on the page | What gets saved |
   | --- | --- |
   | Single image (including side-by-side rows) | The original upload the lightbox opens |
   | Grid gallery | The largest copy linked on the page |
   | Image without a lightbox | The largest entry in its `srcset` |
   | Project cover (`--include-covers`) | The largest cropped cover |

   The site logo, the "more projects" strip, and text blocks are ignored.
3. **Download.** Each image is streamed to a temporary `.part` file, checked, and then renamed to its final name.

Adobe Portfolio signs every image URL with an `?h=` token. The scraper always uses URLs exactly as the page provides them and never guesses or rewrites them.

## Output

Each project gets its own subfolder, named after the project's page title, with its images numbered in page order:

```text
portfolio-images/
├── Lamp Study/
│   ├── 01.jpg
│   └── 02.jpg
├── Chair/
│   └── 01.png
└── PDF Portfolio/
    └── 01.jpg
```

When two projects have the same title, the second folder gets a suffix (`Lamp Study (2)/`). Pass `--flat` to save everything directly in the output folder instead, with the project name folded into each filename (`lamp-study-01.jpg`, `lamp-study-2-01.jpg`, …).

With `--manifest`, a `manifest.csv` in the output folder records each file's path, download status, page title, page URL, image URL, and asset ID.

## Troubleshooting

**"No project images found."** Make sure the address is an Adobe Portfolio site and that the projects are public. Password-protected pages are not accessed.

**Some downloads fail with HTTP 429 or time out.** The server is asking you to slow down. Try `--workers 2 --delay 2`, then run the same command again. Files that already downloaded are skipped.

**Filenames changed after I edited my site.** Numbering follows the current page order. Run again with `--overwrite` into an empty folder for a clean set.

## Limitations

- Only public pages are downloaded. Password-protected or unpublished pages are not accessed.
- Videos, embedded media (YouTube, Vimeo, Behance and similar), PDFs, and CSS background images are not downloaded.
- The scraper depends on how Adobe Portfolio currently builds its pages. If Adobe changes its markup, image detection may stop working until the scraper is updated. Please [open an issue](https://github.com/Systyntono/adobe-portfolio-scraper/issues) if that happens.

## Development

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The tests use saved HTML fixtures and don't need network access. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License and legal

- Released under the [MIT License](LICENSE).
- Use is subject to the [Terms of Use](TERMS_OF_USE.md).
- The software is provided **"as is", without warranty of any kind**. The author and contributors accept **no liability** for any damages, claims, or other consequences arising from its use. You alone are responsible for how you use it and what you download.
- "Adobe" and "Adobe Portfolio" are trademarks of Adobe Inc. They are used here only to describe what websites this tool works with.
