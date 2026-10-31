"""PyWebScrapr: scrape text, links and images from web pages.

Failures on individual URLs are skipped and logged through the standard
``logging`` module (logger name ``pywebscrapr``); they do not abort a run.
"""
import csv
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
from collections import namedtuple
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from functools import lru_cache
from io import BytesIO
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, NavigableString, SoupStrainer, Tag
from PIL import Image

__version__ = '0.1.8'

logger = logging.getLogger('pywebscrapr')

DEFAULT_TIMEOUT = (10, 30)  # (connect, read) seconds
DEFAULT_MAX_PAGE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_REDIRECTS = 10
DEFAULT_USER_AGENT = 'PyWebScrapr/{} (+https://github.com/infinitode/pywebscrapr)'.format(__version__)

_MIN_IMAGE_BYTES = 500  # responses smaller than this are treated as error pages
_CHUNK_SIZE = 64 * 1024
_REDIRECT_CODES = (301, 302, 303, 307, 308)
_ELEMENT_MODES = ('text', 'content', 'unseen', 'links')

# Text inside these elements is never emitted as page content.
_SKIPPED_TEXT_TAGS = frozenset(['head', 'title', 'script', 'style', 'noscript', 'template'])
# Text is grouped under its nearest block-level ancestor so that inline markup
# does not split words and nested containers do not repeat text.
_BLOCK_TAGS = frozenset([
    'address', 'article', 'aside', 'blockquote', 'body', 'caption', 'dd', 'details', 'div', 'dl',
    'dt', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header',
    'li', 'main', 'nav', 'ol', 'option', 'p', 'pre', 'section', 'summary', 'table', 'tbody', 'td',
    'tfoot', 'th', 'thead', 'tr', 'ul',
])
_FORMAT_EXTENSIONS = {'JPEG': '.jpg', 'MPO': '.jpg', 'TIFF': '.tif'}
_RESERVED_NAMES = frozenset(['con', 'prn', 'aux', 'nul'] +
                            ['com{}'.format(i) for i in range(1, 10)] +
                            ['lpt{}'.format(i) for i in range(1, 10)])

_Fetched = namedtuple('_Fetched', ['url', 'content_type', 'body'])


class _UnsafeURLError(Exception):
    """Raised for URLs that use a disallowed scheme or resolve to a blocked address."""


class _ResponseTooLargeError(Exception):
    """Raised when a response body exceeds the configured size limit."""


# ---------------------------------------------------------------------------
# Public helpers (unchanged signatures)
# ---------------------------------------------------------------------------

def normalize_text(text):
    """Normalize the text by converting to lowercase, removing extra whitespace and irrelevant patterns."""
    text = text.lower()  # Convert to lowercase
    text = re.sub(r'\s+', ' ', text)  # Remove extra whitespace
    text = re.sub(r'\b(the|and|for|in|with|to|a|an|of|on|at|by)\b', '', text)  # Remove common stopwords (optional)
    text = re.sub(r'\W+', ' ', text)  # Remove non-word characters
    return text.strip()


@lru_cache(maxsize=10000)
def _cached_normalize_words(text):
    """Helper to convert text string into normalized word frozenset with caching."""
    return frozenset(normalize_text(text).split())


def _get_normalized_words(text):
    """Helper to convert text into normalized word frozenset, caching string results to avoid re-normalization overhead."""
    if isinstance(text, (set, frozenset)):
        return text
    return _cached_normalize_words(text)


def is_similar_regex(new_text, seen_texts, threshold=0.8):
    """
    Check if the `new_text` is similar to any text in `seen_texts` based on word overlap.

    Similarity is the number of shared words divided by the word count of the *larger*
    of the two texts. Measuring against the larger text means a short item (for example a
    navigation label such as "Home") cannot mark a long, unrelated paragraph as a duplicate.
    """
    new_text_words = _get_normalized_words(new_text)
    if not new_text_words:
        return False

    for seen_item in seen_texts:
        seen_text_words = _get_normalized_words(seen_item)
        if not seen_text_words:
            continue  # Skip empty text comparisons

        shared = len(new_text_words & seen_text_words)
        match_ratio = shared / max(len(new_text_words), len(seen_text_words))
        if match_ratio >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------

def _get_absolute_url(base_url, relative_url):
    """Helper function to get absolute URLs."""
    return urljoin(base_url, relative_url)


def _canonical_parts(url):
    """Return (scheme, netloc, path, query) with host lower-cased and default ports removed."""
    parts = urlparse(url)
    scheme = parts.scheme.lower()
    host = parts.hostname or ''
    if ':' in host:  # IPv6 literal
        host = '[{}]'.format(host)
    port = parts.port
    default_port = {'http': 80, 'https': 443}.get(scheme)
    netloc = host if port is None or port == default_port else '{}:{}'.format(host, port)
    return scheme, netloc, parts.path or '/', parts.query


def _canonical_url(url):
    """Canonical form of a URL used for de-duplication (fragments are dropped)."""
    scheme, netloc, path, query = _canonical_parts(url)
    return urlunparse((scheme, netloc, path, '', query, ''))


def _netloc(url):
    return _canonical_parts(url)[1]


def _to_ip(text):
    ip = ipaddress.ip_address(text.split('%', 1)[0])  # strip IPv6 zone id
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip


def _resolve(host, port):
    try:
        return [_to_ip(host)]  # host is an IP literal
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise _UnsafeURLError('cannot resolve host {!r}: {}'.format(host, exc))
    addresses = []
    for info in infos:
        try:
            addresses.append(_to_ip(info[4][0]))
        except ValueError:
            continue
    if not addresses:
        raise _UnsafeURLError('host {!r} did not resolve to an IP address'.format(host))
    return addresses


def _check_url(url, block_private_addresses):
    """Raise _UnsafeURLError if the URL is not an allowed http(s) target."""
    parts = urlparse(url)
    if parts.scheme not in ('http', 'https'):
        raise _UnsafeURLError('unsupported URL scheme {!r}'.format(parts.scheme))
    host = parts.hostname
    if not host:
        raise _UnsafeURLError('URL has no host name')
    if block_private_addresses:
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        for address in _resolve(host, port):
            if not address.is_global:
                raise _UnsafeURLError('{} resolves to a non-public address ({})'.format(host, address))


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

def _read_body(response, max_bytes):
    """Read a response body, enforcing an optional byte limit."""
    length = response.headers.get('Content-Length', '').strip()
    if max_bytes and length.isdigit() and int(length) > max_bytes:
        raise _ResponseTooLargeError('Content-Length {} exceeds the limit of {} bytes'.format(length, max_bytes))
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
        if not chunk:
            continue
        total += len(chunk)
        if max_bytes and total > max_bytes:
            raise _ResponseTooLargeError('response exceeds the limit of {} bytes'.format(max_bytes))
        chunks.append(chunk)
    return b''.join(chunks)


class _Fetcher(object):
    """Thread-aware HTTP client shared by one scraping run."""

    def __init__(self, timeout, user_agent, max_redirects, block_private_addresses, rate_limit):
        self.timeout = timeout
        self.user_agent = user_agent
        self.max_redirects = max_redirects
        self.block_private_addresses = block_private_addresses
        self.rate_limit = rate_limit
        self._local = threading.local()
        self._sessions = []
        self._sessions_lock = threading.Lock()
        self._slot_lock = threading.Lock()
        self._next_slot = 0.0

    def _session(self):
        # requests.Session is not guaranteed to be thread-safe, so each worker thread gets its own.
        session = getattr(self._local, 'session', None)
        if session is None:
            session = requests.Session()
            if self.user_agent:
                session.headers['User-Agent'] = self.user_agent
            with self._sessions_lock:
                self._sessions.append(session)
            self._local.session = session
        return session

    def _throttle(self):
        """Global rate limit: requests are spaced `rate_limit` seconds apart across all threads."""
        if not self.rate_limit:
            return
        with self._slot_lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.rate_limit
        delay = slot - now
        if delay > 0:
            time.sleep(delay)

    def get(self, url, max_bytes):
        """GET a URL, following redirects manually so every hop is validated."""
        current = url
        for _ in range(self.max_redirects + 1):
            _check_url(current, self.block_private_addresses)
            self._throttle()
            response = self._session().get(current, timeout=self.timeout, allow_redirects=False, stream=True)
            try:
                location = response.headers.get('Location')
                if response.status_code in _REDIRECT_CODES and location:
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                body = _read_body(response, max_bytes)
                return _Fetched(current, response.headers.get('Content-Type', ''), body)
            finally:
                response.close()
        raise requests.exceptions.TooManyRedirects('exceeded {} redirects'.format(self.max_redirects))

    def close(self):
        with self._sessions_lock:
            for session in self._sessions:
                session.close()
            self._sessions = []


def _charset_from_content_type(content_type):
    match = re.search(r'charset=["\']?([A-Za-z0-9_.:-]+)', content_type or '', re.I)
    return match.group(1) if match else None


def _is_textual(content_type):
    if not content_type:
        return True
    mime = content_type.split(';', 1)[0].strip().lower()
    return mime.startswith('text/') or mime in ('application/xhtml+xml', 'application/xml')


def _parse(body, content_type, strainer=None):
    return BeautifulSoup(body, 'html.parser', from_encoding=_charset_from_content_type(content_type),
                         parse_only=strainer)


def _base_url(soup, page_url):
    base = soup.find('base', href=True)
    if base is None:
        return page_url
    try:
        return urljoin(page_url, base['href'].strip())
    except ValueError:
        return page_url


def _child_urls(soup, base, origin_netloc):
    """Yield same-domain child links (canonical form) found on a page."""
    for anchor in soup.find_all('a', href=True):
        href = anchor['href'].strip()
        if not href:
            continue
        url = urljoin(base, href)
        if urlparse(url).scheme not in ('http', 'https'):
            continue
        try:
            key = _canonical_url(url)
        except ValueError:
            continue
        if _netloc(key) == origin_netloc:
            yield key


# ---------------------------------------------------------------------------
# Crawl engine
# ---------------------------------------------------------------------------

def _crawl(seeds, fetcher, process, *, strainer, follow_child_links, max_links_to_follow,
           max_depth, max_workers, max_page_bytes, print_progress):
    """
    Fetch the seed URLs and, optionally, same-domain child links concurrently.

    `process(fetched, soup, base_url)` runs in worker threads and returns the page's result.
    Pages that fail are skipped and logged. Returns [(final_url, result), ...] in the order
    the pages were discovered, so output is deterministic for a given input.
    """
    lock = threading.Lock()
    seen = set()
    state = {'seq': 0, 'children': 0, 'scraped': 0}
    results = {}

    def visit(url, depth, origin):
        try:
            fetched = fetcher.get(url, max_page_bytes)
            if origin is not None and _netloc(fetched.url) != origin:
                logger.warning('Skipped %s: child link redirected to a different domain (%s)', url, fetched.url)
                return None
            if not _is_textual(fetched.content_type):
                logger.info('Skipped non-text content: %s (%s)', fetched.url, fetched.content_type)
                return None
            soup = _parse(fetched.body, fetched.content_type, strainer)
            base = _base_url(soup, fetched.url)
            result = process(fetched, soup, base)
            children = []
            if follow_child_links and (max_depth is None or depth < max_depth):
                children = list(_child_urls(soup, base, _netloc(fetched.url)))
            return fetched.url, result, children, depth
        except Exception as exc:  # one bad page must never abort the run
            logger.warning('Skipped %s: %s', url, exc)
            return None

    def queue(executor, pending, url, depth, origin, is_child):
        try:
            key = _canonical_url(url)
        except ValueError as exc:
            logger.warning('Skipped invalid URL %r: %s', url, exc)
            return
        with lock:
            if key in seen:
                return
            if is_child:
                if max_links_to_follow is not None and state['children'] >= max_links_to_follow:
                    return
                state['children'] += 1
            seen.add(key)
            seq = state['seq']
            state['seq'] += 1
        pending[executor.submit(visit, url, depth, origin)] = seq

    pending = {}
    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        for url in seeds:
            queue(executor, pending, url, 0, None, False)
        while pending:
            done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for future in done:
                seq = pending.pop(future)
                outcome = future.result()  # visit() never raises
                if outcome is None:
                    continue
                final_url, result, children, depth = outcome
                results[seq] = (final_url, result)
                state['scraped'] += 1
                if print_progress:
                    print('Scraped {} pages: {}'.format(state['scraped'], final_url))
                origin = _netloc(final_url)
                for child in children:
                    queue(executor, pending, child, depth + 1, origin, True)
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True)
        fetcher.close()
    return [results[seq] for seq in sorted(results)]


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _block_for(node):
    """Return (nearest block ancestor or None, skip flag) for a node."""
    block = None
    for parent in node.parents:
        name = parent.name
        if name in _SKIPPED_TEXT_TAGS:
            return None, True
        if block is None and name in _BLOCK_TAGS:
            block = parent
    return block, False


def _extract_text_blocks(soup):
    """
    Return the visible text of each block-level element, in document order.

    Each block's text is built from its inline pieces (so `Hello <b>world</b>` stays
    "Hello world" and `Hel<b>lo</b>` stays "Hello"), and every piece of text is counted
    once, under its nearest block ancestor. `<br>` becomes a line break.
    """
    order = []
    parts = {}
    for node in soup.descendants:
        if isinstance(node, Tag):
            if node.name != 'br':
                continue
            text = '\n'
        elif type(node) is NavigableString:  # excludes comments, doctypes, script and style strings
            text = str(node)
        else:
            continue
        block, skipped = _block_for(node)
        if skipped:
            continue
        key = id(block) if block is not None else None
        if key not in parts:
            parts[key] = []
            order.append(key)
        parts[key].append(text)
    return [''.join(parts[key]) for key in order]


def _extract_items(soup, base, mode, remove_extra_whitespace):
    """Extract the requested content type from a parsed page as a list of strings."""
    if mode == 'text':
        raw = _extract_text_blocks(soup)
    elif mode == 'content':
        raw = [meta.get('content') for meta in soup.find_all('meta')]
    elif mode == 'unseen':
        raw = [meta.get('content') for meta in soup.find_all('meta')]
        raw += [script.get_text() for script in soup.find_all('script')]
    else:  # 'links'
        raw = []
        for element in soup.find_all(['a', 'img', 'video', 'audio', 'source']):
            value = element.get('href') if element.name == 'a' else element.get('src')
            if value and value.strip():
                url = urljoin(base, value.strip())
                if urlparse(url).scheme in ('http', 'https'):
                    raw.append(url)

    items = []
    for value in raw:
        if not isinstance(value, str):
            continue
        text = ' '.join(value.split()) if remove_extra_whitespace else value.strip()
        if text:
            items.append(text)
    return items


def _csv_safe(value):
    """Neutralise spreadsheet formula injection by prefixing risky cells with an apostrophe."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


# ---------------------------------------------------------------------------
# Image handling
# ---------------------------------------------------------------------------

def _safe_stem(url):
    """Build a filesystem-safe base name from the URL path."""
    path = unquote(urlparse(url).path)
    name = re.split(r'[\\/]', path)[-1]
    stem = os.path.splitext(name)[0]
    stem = re.sub(r'[^A-Za-z0-9._-]+', '_', stem).strip('._-')[:100]
    if not stem:
        return 'image'
    if stem.lower() in _RESERVED_NAMES:
        return 'image_' + stem
    return stem


def _reserve_path(folder, url, image_format, used, used_lock):
    """Pick a file path that no other image in this run has used (case-insensitive)."""
    stem = _safe_stem(url)
    extension = _FORMAT_EXTENSIONS.get(image_format.upper(), '.' + image_format.lower())
    with used_lock:
        name = stem + extension
        count = 0
        while name.lower() in used:
            count += 1
            digest = hashlib.sha1(url.encode('utf-8')).hexdigest()[:8]
            suffix = digest if count == 1 else '{}-{}'.format(digest, count)
            name = '{}-{}{}'.format(stem, suffix, extension)
        used.add(name.lower())
    return os.path.join(folder, name)


def _inspect_image(body):
    """Verify the bytes are a real image and return (format, width, height)."""
    buffer = BytesIO(body)
    with Image.open(buffer) as img:
        img.verify()  # raises on corrupt data
    buffer.seek(0)  # verify() invalidates the image, so reopen it
    with Image.open(buffer) as img:
        image_format, (width, height) = img.format, img.size
    if not image_format:
        raise ValueError('unrecognised image format')
    return image_format, width, height


def _within_bounds(width, height, min_width, min_height, max_width, max_height):
    if (min_width and width < min_width) or (min_height and height < min_height):
        return False
    if (max_width and width > max_width) or (max_height and height > max_height):
        return False
    return True


def _save_image(image_url, fetcher, folder, bounds, max_image_bytes, used, used_lock):
    """Fetch, validate and save one image. Each image is downloaded exactly once."""
    try:
        fetched = fetcher.get(image_url, max_image_bytes)
        mime = fetched.content_type.split(';', 1)[0].strip().lower()
        if not mime.startswith('image/'):
            logger.info('Skipped non-image content: %s (%s)', image_url, fetched.content_type)
            return
        if len(fetched.body) < _MIN_IMAGE_BYTES:
            logger.info('Skipped due to small content size: %s', image_url)
            return
        image_format, width, height = _inspect_image(fetched.body)
        if not _within_bounds(width, height, *bounds):
            logger.info('Ignored due to size constraints: %s', image_url)
            return
        path = _reserve_path(folder, image_url, image_format, used, used_lock)
        with open(path, 'wb') as file:
            file.write(fetched.body)
        logger.info('Downloaded: %s -> %s', image_url, path)
    except Exception as exc:  # corrupt, oversized, unreachable: skip and continue
        logger.warning('Skipped image %s: %s', image_url, exc)


# ---------------------------------------------------------------------------
# Input and option validation
# ---------------------------------------------------------------------------

def _check_optional_int(name, value, minimum):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError('{} must be None or an integer >= {}'.format(name, minimum))


def _check_timeout(timeout):
    def valid(value):
        return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0)

    if isinstance(timeout, tuple):
        if len(timeout) != 2 or not all(valid(part) for part in timeout):
            raise ValueError('timeout tuple must be (connect, read) with positive numbers or None')
    elif not valid(timeout):
        raise ValueError('timeout must be a positive number, a (connect, read) tuple, or None')


def _validate_options(max_workers, rate_limit, timeout, max_links_to_follow, max_depth,
                      max_redirects, byte_limits):
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError('max_workers must be an integer >= 1')
    if not isinstance(rate_limit, (int, float)) or isinstance(rate_limit, bool) or rate_limit < 0:
        raise ValueError('rate_limit must be a number of seconds >= 0')
    _check_timeout(timeout)
    _check_optional_int('max_links_to_follow', max_links_to_follow, 0)
    _check_optional_int('max_depth', max_depth, 0)
    _check_optional_int('max_redirects', max_redirects, 0)
    for name, value in byte_limits.items():
        _check_optional_int(name, value, 1)


def _load_links(links_file, links_array):
    """Return the non-blank links from a file or list. Non-string entries are skipped and logged."""
    if links_file:
        with open(links_file, 'r', encoding='utf-8-sig') as file:
            raw = [line for line in file]
    elif links_array:
        raw = list(links_array)
    else:
        raise ValueError("Either 'links_file' or 'links_array' must be provided.")

    links = []
    for entry in raw:
        if not isinstance(entry, str):
            logger.warning('Skipped link that is not a string: %r', entry)
            continue
        entry = entry.strip()
        if entry:
            links.append(entry)
    return links


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_images(links_file=None, links_array=None, save_folder='images', min_width=None, min_height=None,
                  max_width=None, max_height=None, follow_child_links=False, max_links_to_follow=None,
                  print_progress=False, rate_limit=0, max_workers=5, *, timeout=DEFAULT_TIMEOUT,
                  max_page_bytes=DEFAULT_MAX_PAGE_BYTES, max_image_bytes=DEFAULT_MAX_IMAGE_BYTES,
                  max_depth=None, block_private_addresses=False, user_agent=DEFAULT_USER_AGENT,
                  max_redirects=DEFAULT_MAX_REDIRECTS):
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
    - max_links_to_follow (int): Maximum number of child links to follow (seed links are always scraped).
    - print_progress (bool): If True, print progress updates to the console.
    - rate_limit (float): Minimum seconds between requests, shared by all threads.
    - max_workers (int): Maximum number of threads to use.
    - timeout (float | tuple | None): Request timeout in seconds, or (connect, read). Default (10, 30).
      None disables timeouts (not recommended).
    - max_page_bytes (int | None): Largest HTML page accepted, in bytes. Default 10 MB.
    - max_image_bytes (int | None): Largest image accepted, in bytes. Default 20 MB.
    - max_depth (int | None): Maximum child-link depth (0 = seed pages only). None = unlimited.
    - block_private_addresses (bool): If True, refuse URLs that resolve to private, loopback,
      link-local or otherwise non-public addresses. Default False (allowed).
    - user_agent (str | None): User-Agent header to send. None keeps the requests default.
    - max_redirects (int): Maximum redirects followed per request.

    Failed or rejected images and pages are skipped and logged to the 'pywebscrapr' logger.

    Example:
    ```python
    from pywebscrapr import scrape_images

    # Using links from a file and saving images to output_images folder.
    scrape_images(links_file='links.txt', save_folder='output_images', min_width=100, min_height=100, follow_child_links=True, max_links_to_follow=10)
    ```
    """
    _validate_options(max_workers, rate_limit, timeout, max_links_to_follow, max_depth, max_redirects,
                      {'max_page_bytes': max_page_bytes, 'max_image_bytes': max_image_bytes})
    seeds = _load_links(links_file, links_array)
    os.makedirs(save_folder, exist_ok=True)

    fetcher = _Fetcher(timeout, user_agent, max_redirects, block_private_addresses, rate_limit)
    used_names = set()
    used_lock = threading.Lock()
    bounds = (min_width, min_height, max_width, max_height)

    def process(fetched, soup, base):
        for img_tag in soup.find_all('img'):
            src = (img_tag.get('src') or '').strip()
            if not src or src.lower().startswith('data:'):
                continue
            _save_image(urljoin(base, src), fetcher, save_folder, bounds, max_image_bytes,
                        used_names, used_lock)
        return None

    _crawl(seeds, fetcher, process, strainer=SoupStrainer(['a', 'img', 'base']),
           follow_child_links=follow_child_links, max_links_to_follow=max_links_to_follow,
           max_depth=max_depth, max_workers=max_workers, max_page_bytes=max_page_bytes,
           print_progress=print_progress)


def scrape_text(links_file=None, links_array=None, output_file='output.txt', csv_output_file=None, json_output_file=None,
                remove_extra_whitespace=True, remove_duplicates=False, similarity_threshold=0.8,
                elements_to_scrape='text', follow_child_links=False, max_links_to_follow=None, print_progress=False,
                rate_limit=0, max_workers=5, *, timeout=DEFAULT_TIMEOUT, max_page_bytes=DEFAULT_MAX_PAGE_BYTES,
                max_depth=None, block_private_addresses=False, user_agent=DEFAULT_USER_AGENT,
                max_redirects=DEFAULT_MAX_REDIRECTS):
    """
    Scrape content from the given links and save to specified output file(s).

    Parameters:
    - links_file (str): Path to a file containing links, with each link on a new line.
    - links_array (list): List of links to scrape content from.
    - output_file (str | None): File to save the scraped content as plain text. None skips it.
    - csv_output_file (str): File to save the URL and scraped information in CSV format.
    - json_output_file (str): File to save the URL and scraped information in JSON format.
    - remove_extra_whitespace (bool): If True, collapse whitespace and drop empty lines from the output.
    - remove_duplicates (bool): If True, remove duplicate or highly similar paragraphs.
    - similarity_threshold (float): Similarity (0-1) at or above which paragraphs are considered duplicates.
      Similarity is measured against the larger of the two paragraphs.
    - elements_to_scrape (str): Type of content to scrape. Options are:
        'text' (default) - Scrape visible textual content, one block element per line.
        'content' - Scrape the content attribute of meta tags.
        'unseen' - Scrape hidden or non-visible elements (meta tag content and script data).
        'links' - Scrape absolute URLs from href and src attributes of anchor and media elements.
    - follow_child_links (bool): If True, follow and scrape content from same-domain child links.
    - max_links_to_follow (int | None): Maximum number of child links to follow (seed links are always scraped).
    - print_progress (bool): If True, print progress updates to the console.
    - rate_limit (float): Minimum seconds between requests, shared by all threads.
    - max_workers (int): Maximum number of threads to use.
    - timeout (float | tuple | None): Request timeout in seconds, or (connect, read). Default (10, 30).
      None disables timeouts (not recommended).
    - max_page_bytes (int | None): Largest page accepted, in bytes. Default 10 MB.
    - max_depth (int | None): Maximum child-link depth (0 = seed pages only). None = unlimited.
    - block_private_addresses (bool): If True, refuse URLs that resolve to private, loopback,
      link-local or otherwise non-public addresses. Default False (allowed).
    - user_agent (str | None): User-Agent header to send. None keeps the requests default.
    - max_redirects (int): Maximum redirects followed per request.

    Failed or rejected pages are skipped and logged to the 'pywebscrapr' logger.

    Example:
    ```python
    scrape_text(links_array=['https://example.com'], similarity_threshold=0.9, follow_child_links=True, max_links_to_follow=10)
    ```
    """
    if elements_to_scrape not in _ELEMENT_MODES:
        raise ValueError('elements_to_scrape must be one of {}'.format(', '.join(_ELEMENT_MODES)))
    if not isinstance(similarity_threshold, (int, float)) or not 0 < similarity_threshold <= 1:
        raise ValueError('similarity_threshold must be a number greater than 0 and at most 1')
    _validate_options(max_workers, rate_limit, timeout, max_links_to_follow, max_depth, max_redirects,
                      {'max_page_bytes': max_page_bytes})
    seeds = _load_links(links_file, links_array)

    fetcher = _Fetcher(timeout, user_agent, max_redirects, block_private_addresses, rate_limit)

    def process(fetched, soup, base):
        return _extract_items(soup, base, elements_to_scrape, remove_extra_whitespace)

    results = _crawl(seeds, fetcher, process, strainer=None,
                     follow_child_links=follow_child_links, max_links_to_follow=max_links_to_follow,
                     max_depth=max_depth, max_workers=max_workers, max_page_bytes=max_page_bytes,
                     print_progress=print_progress)

    # De-duplicate in discovery order, after the crawl, so results do not depend on thread timing.
    seen_texts = []
    all_content = []
    records = []
    for final_url, items in results:
        kept = []
        for item in items or []:
            if remove_duplicates:
                if is_similar_regex(item, seen_texts, similarity_threshold):
                    continue
                seen_texts.append(item)
            kept.append(item)
        if kept:
            content = "\n".join(kept)
            all_content.append(content)
            records.append({'URL': final_url, 'Content': content})

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as text_file:
            text_file.write("\n\n".join(all_content).rstrip())

    if csv_output_file:
        with open(csv_output_file, 'w', newline='', encoding='utf-8') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=['URL', 'Content'])
            writer.writeheader()
            for record in records:
                writer.writerow({key: _csv_safe(value) for key, value in record.items()})

    if json_output_file:
        with open(json_output_file, 'w', encoding='utf-8') as json_file:
            json.dump(records, json_file, ensure_ascii=False, indent=4)
