# PyWebScrapr
![Python Version](https://img.shields.io/badge/python-3.13-blue.svg)
[![Code Size](https://img.shields.io/github/languages/code-size/infinitode/pywebscrapr)](https://github.com/infinitode/pywebscrapr)
![Downloads](https://pepy.tech/badge/pywebscrapr)
![License Compliance](https://img.shields.io/badge/license-compliance-brightgreen.svg)
![PyPI Version](https://img.shields.io/pypi/v/pywebscrapr)

An open-source Python library for web scraping tasks. Includes support for both text and image scraping.

## Changes in 0.1.8

0.1.8 is a security, reliability and correctness release. It changes some defaults and behaviour, so read **Breaking changes** before upgrading.

### Breaking changes
- **Python 3.9 or later is required** (the package previously claimed 3.6+). Pillow 10.3+ and requests 2.32.4+ are the new minimums, and the CI matrix tests 3.9, 3.12 and 3.13.
- **Failed pages and images are skipped and logged instead of raising.** A bad URL, timeout, DNS failure, HTTP error, oversized response or corrupt image no longer aborts the run or discards the output. Failures are logged as warnings and downloads as info messages on the `pywebscrapr` logger. Previously "Downloaded" and "Ignored" messages were printed; to see them again, use `logging.basicConfig(level=logging.INFO)`.
- **Text output changes.** Words are no longer glued together, nested containers no longer repeat their text, and `<head>`, `<title>`, `<style>`, `<template>` and comments are excluded. Expect different (cleaner) output from earlier versions.
- **Duplicate detection measures similarity against the larger of the two paragraphs.** The old measure was against the earlier paragraph, which let a short item such as "Home" delete unrelated content. Results for paragraphs of similar length are essentially unchanged.
- **`max_links_to_follow` counts child links only.** Seed links are always scraped. Previously seeds counted toward the limit and `0` meant "unlimited"; now `0` means "follow no child links".
- **Child links must stay on the same domain after redirects.** Seed links may still redirect anywhere.
- **Result URLs are final URLs.** The `URL` field in CSV and JSON output is the address after redirects.
- **Default User-Agent.** Requests now send `PyWebScrapr/0.1.8 (+https://github.com/infinitode/pywebscrapr)`. Pass `user_agent=None` to keep the requests default.
- **Invalid options raise `ValueError` straight away**, for example an unknown `elements_to_scrape`, `max_workers < 1`, or a non-positive timeout.
- **Image file extensions match the detected format** (for example `photo.jpeg` is saved as `photo.jpg` when the image really is a JPEG).
- **Links files are read as UTF-8** (a byte-order mark is tolerated). Blank lines and surrounding whitespace are ignored.
- **`output_file` can be `None`** to skip the plain-text file. The default is still `output.txt`.
- Private helpers `_download_image` and `_check_image_dimensions` were replaced. They were never documented, but they are no longer importable under those names.

### Security
- **Timeouts on every request.** The default is `timeout=(10, 30)` (connect, read seconds). Previously a server that never answered could hang the run forever. Pass `timeout=None` only if you accept that risk.
- **Response size limits.** `max_page_bytes` (default 10 MB) and `max_image_bytes` (default 20 MB) are enforced from `Content-Length` and again while the body streams. Previously the whole response was loaded into memory with no cap.
- **Redirects are checked hop by hop.** Each redirect target is validated for scheme and, when enabled, address. `max_redirects` (default 10) bounds the chain.
- **Only `http` and `https` are fetched.** `file:`, `ftp:`, `javascript:` and other schemes are refused.
- **New `block_private_addresses` flag** (default `False`, so private and internal hosts are allowed as before). Set it to `True` to refuse URLs that resolve to loopback, private, link-local, CGNAT or other non-public addresses. This protects against a crawled page pointing the scraper at internal services, such as an `<img src="http://169.254.169.254/...">`. Limitation: the check runs before the connection is made, so it does not defend against DNS rebinding; keep the flag on for untrusted inputs and run the scraper with network egress controls where that matters.
- **CSV formula injection is neutralised.** Cells beginning with `=`, `+`, `-`, `@`, tab or carriage return get a leading apostrophe, so spreadsheet programs will not evaluate scraped text as formulas.
- **Safe image file names.** Names are restricted to letters, digits, `.`, `_` and `-`; Windows reserved names are prefixed; empty names fall back to `image`. Two images that would share a name in the same run are stored under different names instead of overwriting each other. Files left over from an earlier run with the same name are still overwritten, as before.
- **Dependency floors.** `requests>=2.32.4` includes the fix for CVE-2024-47081 (a `.netrc` credential leak). `pillow>=10.3.0` includes the fix for CVE-2024-28219 (a buffer overflow in `_imagingcms`).
- **CI hardening.** Every GitHub Action is pinned to a commit SHA. Workflows default to `permissions: contents: read`, checkout does not persist credentials, and the PyPI token is only available to the publish job.

### Bug fixes
- **Text extraction** joins inline markup correctly: `Hello <b>world</b>` gives `Hello world`, not `Helloworld`.
- **Nested containers no longer duplicate text.** `<div><div><p>x</p></div></div>` yields `x` once, not three times.
- **Line breaks.** `<br>` produces a line break in text output.
- **`links` mode** now returns images, video, audio and `<source>` URLs as well as anchors (it previously returned only anchors, and the image and video branches never ran). URLs are returned absolute, not relative, and only `http`/`https` URLs are kept.
- **`unseen` mode** includes `<script>` data as documented. It previously read only `<meta>` tags.
- **Error pages are skipped.** 4xx and 5xx responses are no longer saved as page content.
- **Each page is fetched once.** With `follow_child_links`, a page was previously downloaded twice (once for its content and once to find its links).
- **Each image is downloaded once.** Images were previously requested twice (once to check the size and once to save). The check and the save could also see different responses.
- **Images without a `src` are skipped.** They previously resolved to the page's own URL and triggered extra requests.
- **Image names no longer crash the run.** A URL with an empty file name (such as `/?format=png`) used to raise `IsADirectoryError` and abort everything.
- **Decompression bombs and corrupt images are skipped.** A header declaring an enormous image previously raised `DecompressionBombError` outside the error handling and aborted the run.
- **Rate limit is global.** `rate_limit` is now the minimum spacing between requests across all threads. Previously each thread slept on its own, so `max_workers=5` could send five requests per interval.
- **Fragments no longer cause repeat crawls.** `page#one` and `page#two` are the same page. Host case and default ports are normalised too.
- **Output is deterministic.** Results come out in discovery order regardless of thread count, and duplicate detection runs after the crawl. Previously duplicate detection depended on thread timing and could let near-duplicates through.
- **Failures no longer discard output.** One unreachable host or malformed URL used to abort the crawl before anything was written. Output is now written for every page that succeeded.
- **Blank lines in links files** no longer crash the run.
- **Character encoding.** Pages are decoded from their declared charset (HTTP header or `<meta charset>`). Previously requests decoded `text/*` responses without a charset as ISO-8859-1, which mangled UTF-8 pages.
- **`<base href>`** is honoured when resolving relative links and images.
- **Non-text responses** (images, PDFs and similar) are no longer parsed as HTML when they are reached as child links.
- **`follow_child_links`** respects `max_depth`, which is new in this release.

### New options
- `scrape_text` and `scrape_images` (keyword-only): `timeout`, `max_page_bytes`, `max_depth`, `block_private_addresses`, `user_agent`, `max_redirects`.
- `scrape_images` (keyword-only): `max_image_bytes`.
- `scrape_text`: `output_file=None` skips the plain-text file.
- `pywebscrapr.__version__` exposes the package version.

### Packaging, CI and tests
- The version is defined once, in `pywebscrapr/__init__.py`, and `setup.py` reads it. The `{{VERSION_PLACEHOLDER}}` substitution is gone.
- `bs4` is replaced by `beautifulsoup4` in `requirements.txt` and `setup.py`. Build with `python -m build`.
- The workflow runs the offline test suite on Python 3.9, 3.12 and 3.13 for every push and pull request. Publishing to PyPI happens only for tags, only after the tests pass, and only if the tag matches `__version__`.
- `tests.py` is rewritten: 39 offline tests run against local fixture servers, so no internet access is needed. Set `PYWEBSCRAPR_LIVE_TESTS=1` to also run a live check against example.com.
- `.gitignore` covers `output/` and `*.egg-info/`.

## Changes in 0.1.7:
- Optimized `is_similar_regex` text similarity and duplicate checking by caching text normalization and word splitting, drastically improving duplicate text detection speed by up to 25x (~96% CPU time reduction).

## Changes in 0.1.6:
- Added progress indicators to both `scrape_images` and `scrape_text` to provide real-time feedback on scraping progress.
- Implemented multithreading to improve performance by scraping multiple pages concurrently.
- Added a `rate_limit` parameter to both scraping functions to control the request frequency and prevent server overload.
- Refactored the concurrency model to ensure that child links are also scraped concurrently.

## Changes in 0.1.5:
- Added new params to both `scrape_images` and `scrape_text` to allow for following child links, and setting a maximum allowed followed child links.
- Added a `json` export format for text scraping, with improvements to exporting.

> [!TIP]
> We recommend disabling `remove_duplicates` on large sites, to allow for faster text scraping (this can improve speed by 4x). It also may not work well with `follow_child_links` enabled, as it may remove similar content from scraped child links.

## Changes in 0.1.4:
- Added new parameters to `scrape_text` to allow automatic removal of duplicates or similar text, and another to specify the type of textual content to scrape (`text`, `content`, `unseen`, `links`).

## Changes in 0.1.3:
- Added support for handling of different types of images on websites. Also now checks for invalid images, with added error handling.

## Changes in 0.1.2

Changes in version 0.1.2:
- `min` and `max` width and height parameters can now be specified when working with image scraping, allowing you to quickly exclude smaller resolution images, or images that are extremely large and take up too much space.
- PyWebScrapr now uses BeautifulSoup4's `SoupStrainer`, making extracting content from webpages much faster.

## Installation

You can install PyWebScrapr using pip:

```bash
pip install pywebscrapr
```

## Supported Python Versions

PyWebScrapr supports Python 3.9 and later. Python 3.9, 3.12 and 3.13 are tested in CI.

## Features

- **Text Scraping**: Extract textual content from specified URLs.
- **Image Scraping**: Download images from specified URLs.

<sub>*for a full list check out the [PyWebScrapr official documentation](https://infinitode-docs.gitbook.io/documentation/package-documentation/pywebscrapr-package-documentation).</sub>

## Usage

### Text Scraping

```python
from pywebscrapr import scrape_text

# Specify links in a file or list
links_file = 'links.txt'
links_array = ['https://example.com/page1', 'https://example.com/page2']

# Scrape text and save to the 'output.txt' file
scrape_text(links_file=links_file, links_array=links_array, output_file='output.txt')
```

### Image Scraping

```python
from pywebscrapr import scrape_images

# Specify links in a file or list
links_file = 'image_links.txt'
links_array = ['https://example.com/image1.jpg', 'https://example.com/image2.png']

# Scrape images and save to the 'images' folder
scrape_images(links_file=links_file, links_array=links_array, save_folder='images')
```

### Options: timeouts, limits and safety

Every request has a timeout and a size cap by default. Override them with keyword arguments:

```python
from pywebscrapr import scrape_images, scrape_text

scrape_text(
    links_array=['https://example.com'],
    output_file='output.txt',
    follow_child_links=True,
    max_links_to_follow=20,      # child links only; seed links are always scraped
    max_depth=2,                 # child-link depth (0 = seed pages only)
    timeout=(5, 20),             # (connect, read) seconds; None disables timeouts
    max_page_bytes=5 * 1024**2,  # largest page accepted
    rate_limit=0.5,              # minimum seconds between requests, shared by all threads
)

scrape_images(
    links_array=['https://example.com/gallery'],
    save_folder='images',
    max_image_bytes=10 * 1024**2,
    block_private_addresses=True,  # refuse loopback, private and link-local targets
    user_agent='MyCrawler/1.0 (+https://example.com/bot)',
)
```

Skipped pages and images are logged. To see them, configure logging:

```python
import logging
logging.basicConfig(level=logging.INFO)  # logger name: 'pywebscrapr'
```

### Text scraping modes and output formats

`scrape_text(..., elements_to_scrape=...)` accepts four modes:

| Mode | What it returns |
|---|---|
| `'text'` (default) | Visible text, one line per block-level element. Inline markup is joined correctly (`Hello <b>world</b>` gives `Hello world`). `<br>` gives a line break. `<head>`, `<title>`, `<script>`, `<style>`, `<noscript>`, `<template>` and comments are excluded. |
| `'content'` | The `content` attribute of `<meta>` tags. |
| `'unseen'` | `<meta>` `content` values and `<script>` text. |
| `'links'` | Absolute `http`/`https` URLs from `<a href>`, `<img src>`, `<video src>`, `<audio src>` and `<source src>`. |

Output files:
- **Plain text** (`output_file`, default `output.txt`; pass `None` to skip): pages separated by a blank line.
- **CSV** (`csv_output_file`): columns `URL` and `Content`. Cells that start with `=`, `+`, `-`, `@`, tab or carriage return get a leading apostrophe, so spreadsheet programs do not run them as formulas.
- **JSON** (`json_output_file`): a list of `{"URL": ..., "Content": ...}` objects.

The `URL` in CSV and JSON output is the final address after redirects. Pages are written in discovery order, so output is the same however many `max_workers` you use. Only pages with content appear in CSV and JSON.

### Upgrading from 0.1.7

1. Install Python 3.9 or later. Earlier versions of the package declared 3.6+, but 0.1.8 requires 3.9+.
2. Expect skipped pages and images in the logs instead of exceptions. Add `logging.basicConfig(level=logging.INFO)` to see downloads as well as skips.
3. Review `max_links_to_follow`. It now counts child links only, and `0` means no child links.
4. Review `rate_limit`. It is now the minimum spacing between requests across all threads, so the effective rate can be lower than before.
5. Pass `user_agent=None` if you need the old User-Agent header.
6. Only pass `timeout=None`, `max_page_bytes=None` or `max_image_bytes=None` if you accept the risk of unbounded waits or downloads.

### Known limitations

- `robots.txt` is not checked. Make sure your use of a site follows its terms and policies.
- There are no retries or backoff. A failed request is skipped and logged.
- Lazy-loaded images (`data-src`, `srcset`) are not followed. Only `src` is used.
- SVG images are skipped, because Pillow cannot open them.
- JavaScript is not run. Only the HTML the server returns is parsed.
- Output is held in memory until the crawl finishes, and the output files are written at the end.
- `block_private_addresses` checks addresses before connecting, so it does not protect against DNS rebinding. For untrusted input, also restrict network egress.

## Contributing

Contributions are welcome! If you encounter any issues, have suggestions, or want to contribute to PyWebScrapr, please open an issue or submit a pull request on [GitHub](https://github.com/infinitode/pywebscrapr).

## License

PyWebScrapr is released under the terms of the **MIT License (Modified)**. Please see the [LICENSE](https://github.com/infinitode/pywebscrapr/blob/main/LICENSE) file for the full text.

**Modified License Clause**

The modified license clause grants users the permission to make derivative works based on the PyWebScrapr software. However, it requires any substantial changes to the software to be clearly distinguished from the original work and distributed under a different name.
