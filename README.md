# PyWebScrapr
![Python Version](https://img.shields.io/badge/python-3.13-blue.svg)
[![Code Size](https://img.shields.io/github/languages/code-size/infinitode/pywebscrapr)](https://github.com/infinitode/pywebscrapr)
![Downloads](https://pepy.tech/badge/pywebscrapr)
![License Compliance](https://img.shields.io/badge/license-compliance-brightgreen.svg)
![PyPI Version](https://img.shields.io/pypi/v/pywebscrapr)

An open-source Python library for web scraping tasks. Includes support for both text and image scraping.

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

PyWebScrapr supports the following Python versions:

- Python 3.6
- Python 3.7
- Python 3.8
- Python 3.9
- Python 3.10
- Python 3.11
- Python 3.12/Later (Preferred)

Please ensure that you have one of these Python versions installed before using PyWebScrapr. PyWebScrapr may not work as expected on lower versions of Python than the supported.

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

## Contributing

Contributions are welcome! If you encounter any issues, have suggestions, or want to contribute to PyWebScrapr, please open an issue or submit a pull request on [GitHub](https://github.com/infinitode/pywebscrapr).

## License

PyWebScrapr is released under the terms of the **MIT License (Modified)**. Please see the [LICENSE](https://github.com/infinitode/pywebscrapr/blob/main/LICENSE) file for the full text.

**Modified License Clause**

The modified license clause grants users the permission to make derivative works based on the PyWebScrapr software. However, it requires any substantial changes to the software to be clearly distinguished from the original work and distributed under a different name.
