import os
import csv
import requests
from bs4 import BeautifulSoup, SoupStrainer
from urllib.parse import urlparse, urljoin
from PIL import Image
from io import BytesIO

def _get_absolute_url(base_url, relative_url):
    """Helper function to get absolute URLs."""
    return urljoin(base_url, relative_url)

def _download_image(url, save_path):
    """Helper function to download an image."""
    response = requests.get(url)
    with open(save_path, 'wb') as file:
        file.write(response.content)

def _check_image_dimensions(url, min_width=None, min_height=None, max_width=None, max_height=None):
    """Helper function to check image dimensions."""
    try:
        response = requests.get(url)
        img = Image.open(BytesIO(response.content))
        width, height = img.size

        # Check against provided constraints
        if (min_width and width < min_width) or (min_height and height < min_height):
            return False
        if (max_width and width > max_width) or (max_height and height > max_height):
            return False

        return True
    except Exception as e:
        print(f"Error checking image dimensions: {e}")
        return False

def scrape_images(links_file=None, links_array=None, save_folder='images', min_width=None, min_height=None, max_width=None, max_height=None):
    """
    Scrape image content from the given links and save to specified output folder.

    Parameters:
    - links_file (str): Path to a file containing links, with each link on a new line.
    - links_array (list): List of links to scrape images from.
    - save_folder (str): Folder to save the scraped images.
    - min_width (int): Minimum width of images to include (optional).
    - min_height (int): Minimum height of images to include (optional).
    - max_width (int): Maximum width of images to include (optional).
    - max_height (int): Maximum height of images to include (optional).

    Example:
    ```python
    from pywebscrapr import scrape_images

    # Using links from a file and saving images to output_images folder.
    scrape_images(links_file='links.txt', save_folder='output_images', min_width=100, min_height=100)
    ```
    """
    if not os.path.exists(save_folder):
        os.makedirs(save_folder)

    links = []
    if links_file:
        with open(links_file, 'r') as file:
            links = file.read().splitlines()
    elif links_array:
        links = links_array
    else:
        raise ValueError("Either 'links_file' or 'links_array' must be provided.")

    strainer = SoupStrainer('img')

    for link in links:
        response = requests.get(link)
        soup = BeautifulSoup(response.text, 'html.parser', parse_only=strainer)

        for img_tag in soup.find_all('img'):
            img_url = _get_absolute_url(link, img_tag.get('src'))

            # Skip data URLs
            if not img_url.startswith('data:'):
                img_name = os.path.basename(urlparse(img_url).path)
                save_path = os.path.join(save_folder, img_name)

                # Check image dimensions before downloading
                if _check_image_dimensions(img_url, min_width, min_height, max_width, max_height):
                    _download_image(img_url, save_path)
                    print(f"Downloaded: {img_url} -> {save_path}")
                else:
                    print(f"Ignored due to size constraints: {img_url}")

def scrape_text(links_file=None, links_array=None, output_file='output.txt', csv_output_file=None, remove_extra_whitespace=True):
    """
    Scrape textual content from the given links and save to specified output file(s).

    Parameters:
    - links_file (str): Path to a file containing links, with each link on a new line.
    - links_array (list): List of links to scrape text from.
    - output_file (str): File to save the scraped text.
    - csv_output_file (str): File to save the URL and text information in CSV format.
    - remove_extra_whitespace (bool): If True, remove extra whitespace and empty lines from the output.

    Example:
    ```python
    from pywebscrapr import scrape_text

    # Using links from a file and saving text to output.txt
    scrape_text(links_file='links.txt', output_file='output.txt')

    # Using links directly and saving text to output.txt and csv_output.csv with extra whitespace removal
    links = ['https://example.com/page1', 'https://example.com/page2']
    scrape_text(links_array=links, output_file='output.txt', csv_output_file='csv_output.csv', remove_extra_whitespace=True)
    ```
    """
    links = []
    if links_file:
        with open(links_file, 'r') as file:
            links = file.read().splitlines()
    elif links_array:
        links = links_array
    else:
        raise ValueError("Either 'links_file' or 'links_array' must be provided.")

    all_text = ""
    csv_data = []
    strainer = SoupStrainer(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'a', 'code', 'span', 'nav', 'footer', 'header', 'table', 'td', 'ul', 'ol', 'div'])

    for link in links:
        response = requests.get(link)
        soup = BeautifulSoup(response.text, 'html.parser', parse_only=strainer)

        for element in soup.find_all(lambda tag: tag.name not in ['script', 'style']):
            if remove_extra_whitespace:
                text = element.get_text(strip=True)  # Remove extra whitespace
                if text:  # Skip empty lines
                    all_text += text + "\n"
            else:
                text = element.get_text()
                all_text += text + "\n"
            if csv_output_file:
                csv_data.append({'URL': link, 'Text': text})

    # Save text to output file
    with open(output_file, 'w', encoding='utf-8') as text_file:
        if remove_extra_whitespace:
            text_file.write(all_text.rstrip())  # Remove trailing whitespace
        else:
            text_file.write(all_text)

    # Save CSV data to CSV file
    if csv_output_file:
        with open(csv_output_file, 'w', newline='', encoding='utf-8') as csv_file:
            fieldnames = ['URL', 'Text']
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_data)
