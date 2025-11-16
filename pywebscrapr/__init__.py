import os
import csv
import json
import requests
import re
import time
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
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

def _check_image_dimensions(img_source, min_width=None, min_height=None, max_width=None, max_height=None):
    """Helper function to check image dimensions, handling both URL and BytesIO sources."""
    try:
        if isinstance(img_source, str):  # If img_source is a URL
            response = requests.get(img_source, stream=True)
            response.raise_for_status()

            # Check if the content type is an image
            if 'image' not in response.headers.get('Content-Type', ''):
                print(f"Skipped non-image content: {img_source}")
                return False

            # Check for minimal content length to avoid error pages
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) < 500:  # arbitrary small size for images
                print(f"Skipped due to small content size: {img_source}")
                return False

            img_data = BytesIO(response.content)
            img = Image.open(img_data)
        elif isinstance(img_source, BytesIO):  # Already a BytesIO object
            img = Image.open(img_source)
        else:
            raise ValueError("img_source must be a URL (str) or BytesIO object.")

        img.verify()  # Verify it is indeed an image

        # Re-open after verification as img.verify() closes the file
        img = Image.open(img_data if isinstance(img_source, str) else img_source)
        width, height = img.size

        # Check against provided constraints
        if (min_width and width < min_width) or (min_height and height < min_height):
            return False
        if (max_width and width > max_width) or (max_height and height > max_height):
            return False

        return True
    except (Image.UnidentifiedImageError, requests.exceptions.RequestException, ValueError) as e:
        print(f"Error checking image dimensions: {e}")
        return False

def scrape_images(links_file=None, links_array=None, save_folder='images', min_width=None, min_height=None, max_width=None, max_height=None, follow_child_links=False, max_links_to_follow=None, print_progress=False, rate_limit=0, max_workers=5):
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
    - follow_child_links (bool): If True, follow and scrape images from child links found on the page.
    - max_links_to_follow (int): Maximum number of links to follow when scraping child links.
    - print_progress (bool): If True, print progress updates to the console.
    - rate_limit (int): Seconds to wait between requests.
    - max_workers (int): Maximum number of threads to use.

    Example:
    ```python
    from pywebscrapr import scrape_images

    # Using links from a file and saving images to output_images folder.
    scrape_images(links_file='links.txt', save_folder='output_images', min_width=100, min_height=100, follow_child_links=True, max_links_to_follow=10)
    ```
    """
    def get_links_from_page(url):
        """Helper function to get all links from a page."""
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        return [urljoin(url, a['href']) for a in soup.find_all('a', href=True)]

    def is_same_domain(url1, url2):
        """Helper function to check if two URLs belong to the same domain."""
        return urlparse(url1).netloc == urlparse(url2).netloc

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

    visited_links = set()  # Track visited links to avoid loops
    strainer = SoupStrainer('img')
    links_followed = 0  # Track the number of links followed
    lock = Lock()

    def scrape_page(link):
        """Helper function to scrape images from a single page."""
        nonlocal links_followed
        with lock:
            if link in visited_links or (max_links_to_follow and links_followed >= max_links_to_follow):
                return link, []
            visited_links.add(link)
            links_followed += 1

        if rate_limit > 0:
            time.sleep(rate_limit)
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

        child_links_to_scrape = []
        if follow_child_links:
            child_links = get_links_from_page(link)
            for child_link in child_links:
                if is_same_domain(link, child_link):
                    child_links_to_scrape.append(child_link)
        return link, child_links_to_scrape

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(scrape_page, link): link for link in links}
        scraped_count = 0
        while futures:
            for future in as_completed(list(futures)):
                link, child_links = future.result()
                scraped_count += 1
                if print_progress:
                    print(f"Scraped {scraped_count} pages: {link}")

                del futures[future]

                if child_links:
                    for child_link in child_links:
                        if child_link not in visited_links:
                            futures[executor.submit(scrape_page, child_link)] = child_link


def normalize_text(text):
    """Normalize the text by converting to lowercase, removing extra whitespace and irrelevant patterns."""
    text = text.lower()  # Convert to lowercase
    text = re.sub(r'\s+', ' ', text)  # Remove extra whitespace
    text = re.sub(r'\b(the|and|for|in|with|to|a|an|of|on|at|by)\b', '', text)  # Remove common stopwords (optional)
    text = re.sub(r'\W+', ' ', text)  # Remove non-word characters
    return text.strip()

def is_similar_regex(new_text, seen_texts, threshold=0.8):
    """Check if the `new_text` is similar to any text in `seen_texts` based on regex similarity."""
    normalized_new_text = normalize_text(new_text)

    for seen_text in seen_texts:
        normalized_seen_text = normalize_text(seen_text)

        # Split texts into words and calculate the match ratio
        new_text_words = set(normalized_new_text.split())
        seen_text_words = set(normalized_seen_text.split())

        # Skip empty text comparisons
        if len(seen_text_words) == 0:
            continue

        # Calculate similarity ratio
        match_ratio = len(new_text_words & seen_text_words) / len(seen_text_words)

        # If match ratio exceeds threshold, consider as a duplicate
        if match_ratio >= threshold:
            return True
    return False

def scrape_text(links_file=None, links_array=None, output_file='output.txt', csv_output_file=None, json_output_file=None,
                remove_extra_whitespace=True, remove_duplicates=False, similarity_threshold=0.8,
                elements_to_scrape='text', follow_child_links=False, max_links_to_follow=None, print_progress=False, rate_limit=0, max_workers=5):
    """
    Scrape content from the given links and save to specified output file(s).

    Parameters:
    - links_file (str): Path to a file containing links, with each link on a new line.
    - links_array (list): List of links to scrape content from.
    - output_file (str): File to save the scraped content.
    - csv_output_file (str): File to save the URL and scraped information in CSV format.
    - json_output_file (str): File to save the URL and scraped information in JSON format.
    - remove_extra_whitespace (bool): If True, remove extra whitespace and empty lines from the output.
    - remove_duplicates (bool): If True, remove duplicate or highly similar paragraphs.
    - similarity_threshold (float): Similarity percentage (0-1) above which paragraphs are considered duplicates.
    - elements_to_scrape (str): Type of content to scrape. Options are:
        'text' (default) - Scrape visible textual content.
        'content' - Scrape the [content](http://_vscodecontentref_/3) attribute of meta tags.
        'unseen' - Scrape hidden or non-visible elements (e.g., meta tags, script data).
        'links' - Scrape [href](http://_vscodecontentref_/4) or `src` attributes of anchor and media elements.
    - follow_child_links (bool): If True, follow and scrape content from child links found on the page.
    - max_links_to_follow (int): Maximum number of links to follow when scraping child links.
    - print_progress (bool): If True, print progress updates to the console.
    - rate_limit (int): Seconds to wait between requests.
    - max_workers (int): Maximum number of threads to use.

    Example:
    ```python
    scrape_text(links_array=['https://example.com'], similarity_threshold=0.9, follow_child_links=True, max_links_to_follow=10)
    ```
    """
    def get_links_from_page(url):
        """Helper function to get all links from a page."""
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        return [urljoin(url, a['href']) for a in soup.find_all('a', href=True)]

    def is_same_domain(url1, url2):
        """Helper function to check if two URLs belong to the same domain."""
        return urlparse(url1).netloc == urlparse(url2).netloc

    links = []
    if links_file:
        with open(links_file, 'r') as file:
            links = file.read().splitlines()
    elif links_array:
        links = links_array
    else:
        raise ValueError("Either 'links_file' or 'links_array' must be provided.")

    all_content = []
    csv_data = []
    json_data = []
    seen_texts = []  # Store unique paragraphs
    visited_links = set()  # Track visited links to avoid loops
    strainer = SoupStrainer(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'a', 'code', 'span', 'nav',
                             'footer', 'header', 'table', 'td', 'ul', 'ol', 'div', 'meta'])
    links_followed = 0  # Track the number of links followed
    lock = Lock()

    def scrape_page(link):
        """Helper function to scrape content from a single page."""
        nonlocal links_followed
        with lock:
            if link in visited_links or (max_links_to_follow and links_followed >= max_links_to_follow):
                return link, []
            visited_links.add(link)
            links_followed += 1

        if rate_limit > 0:
            time.sleep(rate_limit)
        response = requests.get(link)
        soup = BeautifulSoup(response.text, 'html.parser', parse_only=strainer)

        page_content = []

        for element in soup.find_all(lambda tag: tag.name not in ['script', 'style']):
            if elements_to_scrape == 'text':
                content = element.get_text(strip=remove_extra_whitespace) if remove_extra_whitespace else element.get_text()
            elif elements_to_scrape == 'content' and element.has_attr('content'):
                content = element['content']
            elif elements_to_scrape == 'unseen' and element.name in ['meta', 'script']:
                content = element.get('content', element.get_text(strip=True))
            elif elements_to_scrape == 'links' and element.name in ['a', 'img', 'video']:
                content = element.get('href') or element.get('src')
            else:
                continue  # Skip unsupported elements for the chosen scrape type

            if content:
                if remove_duplicates:
                    # Check if content is similar to existing unique paragraphs using regex-based similarity
                    if not is_similar_regex(content, seen_texts, similarity_threshold):
                        seen_texts.append(content)
                        page_content.append(content)
                else:
                    page_content.append(content)

        if page_content:
            with lock:
                all_content.append("\n".join(page_content))
                if csv_output_file or json_output_file:
                    csv_data.append({'URL': link, 'Content': "\n".join(page_content)})
                    json_data.append({'URL': link, 'Content': "\n".join(page_content)})

        child_links_to_scrape = []
        if follow_child_links:
            child_links = get_links_from_page(link)
            for child_link in child_links:
                if is_same_domain(link, child_link):
                    child_links_to_scrape.append(child_link)
        return link, child_links_to_scrape

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(scrape_page, link): link for link in links}
        scraped_count = 0
        while futures:
            for future in as_completed(list(futures)):
                link, child_links = future.result()
                scraped_count += 1
                if print_progress:
                    print(f"Scraped {scraped_count} pages: {link}")

                del futures[future]

                if child_links:
                    for child_link in child_links:
                        if child_link not in visited_links:
                            futures[executor.submit(scrape_page, child_link)] = child_link

    # Save content to output file
    with open(output_file, 'w', encoding='utf-8') as text_file:
        text_file.write("\n\n".join(all_content).rstrip())

    # Save CSV data to CSV file
    if csv_output_file:
        with open(csv_output_file, 'w', newline='', encoding='utf-8') as csv_file:
            fieldnames = ['URL', 'Content']
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_data)

    # Save JSON data to JSON file
    if json_output_file:
        with open(json_output_file, 'w', encoding='utf-8') as json_file:
            json.dump(json_data, json_file, ensure_ascii=False, indent=4)