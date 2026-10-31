"""Test suite for PyWebScrapr.

The tests run offline: they start small HTTP servers on 127.0.0.1 and scrape those.
Set PYWEBSCRAPR_LIVE_TESTS=1 to also run a single test against a real website.

Run with:  python -m unittest -v tests
"""
import io
import json
import logging
import os
import shutil
import struct
import tempfile
import threading
import time
import unittest
import zlib
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from PIL import Image

from pywebscrapr import (_check_url, _csv_safe, _extract_text_blocks, _safe_stem, _UnsafeURLError,
                         is_similar_regex, scrape_images, scrape_text)

# Skipped URLs are logged as warnings; keep the test output quiet unless a test asserts on them.
logging.getLogger('pywebscrapr').setLevel(logging.CRITICAL)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def png_bytes(width=300, height=300):
    """A real PNG. Noise keeps the file above the library's minimum image size."""
    buffer = io.BytesIO()
    Image.effect_noise((width, height), 64).convert('RGB').save(buffer, 'PNG')
    return buffer.getvalue()


def bomb_png(side=20000):
    """A PNG whose header declares side x side pixels: PIL rejects it as a decompression bomb."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)

    header = struct.pack('>IIBBBBB', side, side, 8, 2, 0, 0, 0)
    padding = chunk(b'tEXt', b'comment\x00' + b'x' * 1000)  # keep the file above the minimum size
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + padding +
            chunk(b'IDAT', zlib.compress(b'\x00')) + chunk(b'IEND', b''))


class FixtureSite(object):
    """A tiny HTTP server on 127.0.0.1 with scripted routes.

    Route values:
      str / bytes                       -> 200 text/html
      ('image', bytes)                  -> 200 image/png
      ('status', code, body)            -> that status with text/html
      ('redirect', location)            -> 302 to location
      ('sleep', seconds, body)          -> waits, then 200 text/html
      ('raw', content_type, bytes)      -> 200 with the given content type
    """

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.hits = Counter()
        self._lock = threading.Lock()
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                with site._lock:
                    site.hits[self.path] += 1
                spec = site.routes.get(self.path)
                if spec is None:
                    spec = site.routes.get(urlsplit(self.path).path)
                if spec is None:
                    return self._send(404, 'text/html', b'<html><body>not found</body></html>')
                if isinstance(spec, str):
                    spec = spec.encode('utf-8')
                if isinstance(spec, bytes):
                    return self._send(200, 'text/html; charset=utf-8', spec)
                kind = spec[0]
                if kind == 'image':
                    return self._send(200, 'image/png', spec[1])
                if kind == 'status':
                    body = spec[2].encode('utf-8') if isinstance(spec[2], str) else spec[2]
                    return self._send(spec[1], 'text/html; charset=utf-8', body)
                if kind == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', spec[1])
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return None
                if kind == 'sleep':
                    time.sleep(spec[1])
                    body = spec[2].encode('utf-8') if isinstance(spec[2], str) else spec[2]
                    return self._send(200, 'text/html; charset=utf-8', body)
                if kind == 'raw':
                    return self._send(200, spec[1], spec[2])
                raise AssertionError('bad route spec {!r}'.format(spec))

            def _send(self, code, content_type, body):
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = True
        self.base = 'http://127.0.0.1:{}'.format(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path):
        return self.base + path

    def close(self):
        self._server.shutdown()
        self._server.server_close()


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='pywebscrapr-test-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def read(self, name):
        with open(self.path(name), encoding='utf-8') as handle:
            return handle.read()


class SiteTestCase(TempDirTestCase):
    routes = {}

    def setUp(self):
        super(SiteTestCase, self).setUp()
        self.site = FixtureSite(self.routes)

    def tearDown(self):
        self.site.close()
        super(SiteTestCase, self).tearDown()


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

class SimilarityTests(unittest.TestCase):
    def test_short_seen_text_does_not_swallow_long_paragraph(self):
        paragraph = ('Welcome home. Our company has been building reliable software for twenty years '
                     'and we love it.')
        self.assertFalse(is_similar_regex(paragraph, ['Home'], 0.8))

    def test_near_duplicates_are_detected(self):
        original = 'the quick brown fox jumps over the lazy dog today'
        self.assertTrue(is_similar_regex(original, ['the quick brown fox jumps over the lazy dog'], 0.8))

    def test_unrelated_texts_are_not_similar(self):
        self.assertFalse(is_similar_regex('apples and oranges', ['cars and trucks'], 0.8))

    def test_empty_inputs(self):
        self.assertFalse(is_similar_regex('', ['anything here'], 0.8))
        self.assertFalse(is_similar_regex('something here', [''], 0.8))


class TextExtractionTests(unittest.TestCase):
    def blocks(self, html):
        return _extract_text_blocks(BeautifulSoup(html, 'html.parser'))

    def test_inline_markup_does_not_glue_words(self):
        self.assertEqual(self.blocks('<p>Hello <b>world</b>, this is <i>great</i> news</p>'),
                         ['Hello world, this is great news'])

    def test_nested_containers_do_not_repeat_text(self):
        self.assertEqual(self.blocks('<div><div><p>Only once</p></div></div>'), ['Only once'])

    def test_head_script_and_comments_are_excluded(self):
        html = ('<html><head><title>Title</title><style>p{}</style></head>'
                '<body><!-- hidden --><script>var x=1;</script><p>Visible</p></body></html>')
        self.assertEqual(self.blocks(html), ['Visible'])

    def test_line_breaks_are_kept(self):
        self.assertEqual(self.blocks('<p>one<br>two</p>'), ['one\ntwo'])


class HelperTests(unittest.TestCase):
    def test_csv_formula_cells_are_neutralised(self):
        self.assertEqual(_csv_safe('=HYPERLINK("x")'), "'=HYPERLINK(\"x\")")
        self.assertEqual(_csv_safe('+1'), "'+1")
        self.assertEqual(_csv_safe('plain text'), 'plain text')

    def test_image_names_are_safe(self):
        self.assertEqual(_safe_stem('https://example.com/a/logo%20one.png?x=1'), 'logo_one')
        self.assertEqual(_safe_stem('https://example.com/?format=png'), 'image')
        self.assertEqual(_safe_stem('https://example.com/CON.png'), 'image_CON')
        self.assertEqual(_safe_stem('https://example.com/..%2F..%2Fetc.png'), 'etc')

    def test_private_addresses_allowed_by_default_and_blockable(self):
        _check_url('http://127.0.0.1/', block_private_addresses=False)  # no exception
        with self.assertRaises(_UnsafeURLError):
            _check_url('http://127.0.0.1/', block_private_addresses=True)
        with self.assertRaises(_UnsafeURLError):
            _check_url('http://169.254.169.254/latest/meta-data/', block_private_addresses=True)
        with self.assertRaises(_UnsafeURLError):
            _check_url('http://[::1]/', block_private_addresses=True)

    def test_unsupported_schemes_are_rejected(self):
        for url in ('file:///etc/passwd', 'ftp://example.com/x', 'javascript:alert(1)'):
            with self.assertRaises(_UnsafeURLError):
                _check_url(url, block_private_addresses=False)


class ArgumentValidationTests(unittest.TestCase):
    def test_invalid_arguments_raise(self):
        with self.assertRaises(ValueError):
            scrape_text(links_array=['http://127.0.0.1:1/'], output_file=None, max_workers=0)
        with self.assertRaises(ValueError):
            scrape_text(links_array=['http://127.0.0.1:1/'], output_file=None, elements_to_scrape='bogus')
        with self.assertRaises(ValueError):
            scrape_text(links_array=['http://127.0.0.1:1/'], output_file=None, similarity_threshold=0)
        with self.assertRaises(ValueError):
            scrape_images(links_array=['http://127.0.0.1:1/'], save_folder='unused', timeout=-1)
        with self.assertRaises(ValueError):
            scrape_text(links_array=['http://127.0.0.1:1/'], output_file=None, max_page_bytes=0)
        with self.assertRaises(ValueError):
            scrape_text(output_file=None)  # no links at all


# ---------------------------------------------------------------------------
# scrape_text against a local site
# ---------------------------------------------------------------------------

class ScrapeTextTests(SiteTestCase):
    routes = {
        '/article': ('<html><head><title>Ignored title</title></head><body>'
                     '<div><div><p>Only once here.</p></div></div>'
                     '<p>Hello <b>world</b>, this is <i>great</i> news.</p>'
                     '<p>Home</p>'
                     '<p>Welcome home. Our company has been building reliable software for twenty years and we love it.</p>'
                     '<a href="/child">child</a><a href="#top">top</a>'
                     '</body></html>'),
        '/child': '<html><body><p>child page text</p></body></html>',
        '/links': ('<html><body><a href="/a1">a</a><img src="/i1.png"><video src="/v1.mp4"></video>'
                   '<a href="mailto:x@example.com">mail</a></body></html>'),
        '/unseen': ('<html><head><meta name="description" content="desc here"></head>'
                    '<body><script>var secret = 1;</script></body></html>'),
        '/missing-page': ('status', 404, '<html><body><h1>404 Not Found</h1><p>Sorry, nothing here.</p></body></html>'),
        '/csv': '<html><body><p>=HYPERLINK("http://evil.test/x","click")</p></body></html>',
        '/big': '<html><body><p>' + ('lorem ipsum ' * 2000) + '</p></body></html>',
        '/slow': ('sleep', 3, '<html><body><p>too slow</p></body></html>'),
        '/good': '<html><body><p>good page content</p></body></html>',
        '/fragments': '<html><body><a href="/frag#one">1</a><a href="/frag#two">2</a></body></html>',
        '/frag': '<html><body><p>fragment target</p></body></html>',
        '/depth0': '<html><body><p>seed</p><a href="/depth1">d1</a></body></html>',
        '/depth1': '<html><body><p>depth one</p><a href="/depth2">d2</a></body></html>',
        '/depth2': '<html><body><p>depth two</p></body></html>',
    }

    def test_text_extraction_spacing_and_no_duplicates(self):
        out = self.path('out.txt')
        scrape_text(links_array=[self.site.url('/article')], output_file=out, max_workers=1)
        text = self.read('out.txt')
        self.assertIn('Only once here.', text)
        self.assertEqual(text.count('Only once here.'), 1)
        self.assertIn('Hello world, this is great news.', text)
        self.assertNotIn('Helloworld', text)
        self.assertNotIn('Ignored title', text)

    def test_remove_duplicates_keeps_unrelated_paragraph(self):
        scrape_text(links_array=[self.site.url('/article')], output_file=self.path('out.txt'),
                    remove_duplicates=True, max_workers=1)
        self.assertIn('Welcome home. Our company has been building reliable software', self.read('out.txt'))

    def test_links_mode_returns_absolute_http_urls_only(self):
        scrape_text(links_array=[self.site.url('/links')], output_file=self.path('out.txt'),
                    elements_to_scrape='links')
        lines = self.read('out.txt').splitlines()
        self.assertEqual(sorted(lines), sorted([
            self.site.url('/a1'), self.site.url('/i1.png'), self.site.url('/v1.mp4')]))

    def test_unseen_mode_reads_meta_and_script(self):
        scrape_text(links_array=[self.site.url('/unseen')], output_file=self.path('out.txt'),
                    elements_to_scrape='unseen')
        text = self.read('out.txt')
        self.assertIn('desc here', text)
        self.assertIn('var secret = 1;', text)

    def test_error_pages_are_skipped(self):
        scrape_text(links_array=[self.site.url('/missing-page')], output_file=self.path('out.txt'))
        self.assertEqual(self.read('out.txt'), '')

    def test_blank_lines_and_bad_urls_do_not_abort_the_run(self):
        with open(self.path('links.txt'), 'w', encoding='utf-8') as handle:
            handle.write('{}\n\nnot a url\n{}\nhttp://127.0.0.1:1/unreachable\n'.format(
                self.site.url('/good'), self.site.url('/good')))
        with self.assertLogs('pywebscrapr', level='WARNING') as logs:
            scrape_text(links_file=self.path('links.txt'), output_file=self.path('out.txt'), timeout=(2, 2))
        self.assertIn('good page content', self.read('out.txt'))
        self.assertTrue(any('not a url' in line for line in logs.output))
        self.assertTrue(any('127.0.0.1:1/unreachable' in line for line in logs.output))

    def test_oversized_page_is_skipped(self):
        scrape_text(links_array=[self.site.url('/big'), self.site.url('/good')],
                    output_file=self.path('out.txt'), max_page_bytes=2000, max_workers=1)
        text = self.read('out.txt')
        self.assertIn('good page content', text)
        self.assertNotIn('lorem', text)

    def test_slow_page_times_out_and_is_skipped(self):
        start = time.monotonic()
        scrape_text(links_array=[self.site.url('/slow'), self.site.url('/good')],
                    output_file=self.path('out.txt'), timeout=(2, 0.5), max_workers=2)
        self.assertLess(time.monotonic() - start, 2.5)
        self.assertIn('good page content', self.read('out.txt'))
        self.assertNotIn('too slow', self.read('out.txt'))

    def test_child_links_are_followed_and_fragments_fetched_once(self):
        scrape_text(links_array=[self.site.url('/fragments')], output_file=self.path('out.txt'),
                    follow_child_links=True, max_links_to_follow=10, max_workers=1)
        self.assertIn('fragment target', self.read('out.txt'))
        self.assertEqual(self.site.hits['/frag'], 1)

    def test_max_links_to_follow_counts_child_links_only(self):
        # Two seed pages, zero child links allowed: both seeds are still scraped.
        scrape_text(links_array=[self.site.url('/article'), self.site.url('/good')],
                    output_file=self.path('out.txt'), follow_child_links=True, max_links_to_follow=0)
        text = self.read('out.txt')
        self.assertIn('good page content', text)
        self.assertNotIn('child page text', text)

    def test_max_depth_limits_child_links(self):
        scrape_text(links_array=[self.site.url('/depth0')], output_file=self.path('out.txt'),
                    follow_child_links=True, max_depth=1, max_workers=1)
        text = self.read('out.txt')
        self.assertIn('depth one', text)
        self.assertNotIn('depth two', text)

    def test_child_links_must_stay_on_domain_after_redirect(self):
        other = FixtureSite({'/offsite': '<html><body><p>OFFSITE CONTENT</p></body></html>'})
        try:
            self.site.routes['/bounce'] = ('redirect', other.url('/offsite'))
            self.site.routes['/start'] = '<html><body><p>start</p><a href="/bounce">b</a></body></html>'
            scrape_text(links_array=[self.site.url('/start')], output_file=self.path('out.txt'),
                        follow_child_links=True, max_links_to_follow=5)
            self.assertNotIn('OFFSITE CONTENT', self.read('out.txt'))
            self.assertIn('start', self.read('out.txt'))
        finally:
            other.close()

    def test_seed_redirect_to_another_host_is_allowed(self):
        other = FixtureSite({'/landing': '<html><body><p>landed here</p></body></html>'})
        try:
            self.site.routes['/moved'] = ('redirect', other.url('/landing'))
            scrape_text(links_array=[self.site.url('/moved')], output_file=self.path('out.txt'))
            self.assertIn('landed here', self.read('out.txt'))
        finally:
            other.close()

    def test_block_private_addresses_flag(self):
        scrape_text(links_array=[self.site.url('/good')], output_file=self.path('default.txt'))
        self.assertIn('good page content', self.read('default.txt'))
        scrape_text(links_array=[self.site.url('/good')], output_file=self.path('blocked.txt'),
                    block_private_addresses=True)
        self.assertEqual(self.read('blocked.txt'), '')

    def test_rate_limit_is_shared_across_threads(self):
        paths = ['/good?n={}'.format(i) for i in range(4)]
        for path in paths:
            self.site.routes[path] = '<html><body><p>rate</p></body></html>'
        start = time.monotonic()
        scrape_text(links_array=[self.site.url(p) for p in paths], output_file=None,
                    rate_limit=0.3, max_workers=4)
        # Four requests spaced 0.3 s apart need at least 0.9 s in total.
        self.assertGreaterEqual(time.monotonic() - start, 0.8)

    def test_csv_and_json_exports(self):
        scrape_text(links_array=[self.site.url('/csv'), self.site.url('/good')], output_file=None,
                    csv_output_file=self.path('out.csv'), json_output_file=self.path('out.json'), max_workers=1)
        csv_text = self.read('out.csv')
        self.assertIn("'=HYPERLINK", csv_text)
        with open(self.path('out.json'), encoding='utf-8') as handle:
            records = json.load(handle)
        self.assertEqual(sorted(r['URL'] for r in records), sorted([self.site.url('/csv'), self.site.url('/good')]))
        self.assertFalse(os.path.exists(self.path('output.txt')))

    def test_output_is_deterministic_in_discovery_order(self):
        urls = [self.site.url('/good'), self.site.url('/csv'), self.site.url('/links')]
        scrape_text(links_array=urls, output_file=self.path('a.txt'), max_workers=3)
        scrape_text(links_array=urls, output_file=self.path('b.txt'), max_workers=1)
        self.assertEqual(self.read('a.txt'), self.read('b.txt'))


# ---------------------------------------------------------------------------
# scrape_images against a local site
# ---------------------------------------------------------------------------

class ScrapeImagesTests(SiteTestCase):
    routes = {
        '/gallery': ('<html><body>'
                     '<img src="/good.png"><img src="/broken.png"><img src="/?format=png">'
                     '<img src="/bomb.png"><img><img src="data:image/png;base64,AAAA">'
                     '</body></html>'),
        '/good.png': ('image', png_bytes(300, 300)),
        '/small.png': ('image', png_bytes(40, 40)),
        '/broken.png': ('raw', 'image/png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 1000),
        '/bomb.png': ('raw', 'image/png', bomb_png()),
        '/?format=png': ('image', png_bytes(300, 300)),
        '/a/logo.png': ('image', png_bytes(120, 120)),
        '/b/logo.png': ('image', png_bytes(130, 130)),
        '/notimage': ('raw', 'text/plain', b'plain text, not an image at all, padded to be long enough ' * 20),
        '/pair': '<html><body><img src="/a/logo.png"><img src="/b/logo.png"></body></html>',
        '/sizes': '<html><body><img src="/small.png"><img src="/good.png"></body></html>',
        '/with-child': '<html><body><img src="/good.png"><a href="/child-page">c</a></body></html>',
        '/child-page': '<html><body><img src="/b/logo.png"></body></html>',
        '/huge-img': ('image', png_bytes(900, 900)),
        '/notimage-page': '<html><body><img src="/notimage"></body></html>',
        '/nosrc': '<html><body><img alt="no source"></body></html>',
    }

    def test_valid_images_saved_and_bad_ones_skipped_without_crashing(self):
        folder = self.path('imgs')
        scrape_images(links_array=[self.site.url('/gallery')], save_folder=folder)
        names = sorted(os.listdir(folder))
        self.assertIn('good.png', names)
        self.assertIn('image.png', names)  # from /?format=png, no usable basename
        self.assertEqual(len(names), 2)  # broken, bomb, data: and empty src were skipped

    def test_each_image_is_downloaded_once(self):
        scrape_images(links_array=[self.site.url('/gallery')], save_folder=self.path('imgs'), max_workers=1)
        self.assertEqual(self.site.hits['/good.png'], 1)

    def test_name_collisions_do_not_overwrite(self):
        folder = self.path('imgs')
        scrape_images(links_array=[self.site.url('/pair')], save_folder=folder, max_workers=1)
        names = sorted(os.listdir(folder))
        self.assertEqual(len(names), 2)
        self.assertTrue(all(name.endswith('.png') for name in names))

    def test_size_constraints(self):
        folder = self.path('imgs')
        scrape_images(links_array=[self.site.url('/sizes')], save_folder=folder, min_width=100)
        self.assertEqual(os.listdir(folder), ['good.png'])

    def test_image_size_limit(self):
        folder = self.path('imgs')
        scrape_images(links_array=[self.site.url('/huge-img')], save_folder=folder, max_image_bytes=1000)
        self.assertEqual(os.listdir(folder), [])

    def test_non_image_responses_are_not_saved(self):
        folder = self.path('imgs')
        scrape_images(links_array=[self.site.url('/notimage-page')], save_folder=folder)
        self.assertEqual(os.listdir(folder), [])

    def test_block_private_addresses_applies_to_images(self):
        self.site.routes['/private-gallery'] = '<html><body><img src="{}/good.png"></body></html>'.format(self.site.base)
        allowed = self.path('allowed')
        scrape_images(links_array=[self.site.url('/private-gallery')], save_folder=allowed)
        self.assertEqual(os.listdir(allowed), ['good.png'])  # private hosts are allowed by default
        blocked = self.path('blocked')
        scrape_images(links_array=[self.site.url('/private-gallery')], save_folder=blocked,
                      block_private_addresses=True)
        self.assertEqual(os.listdir(blocked), [])

    def test_image_without_src_does_not_fetch_the_page_again(self):
        folder = self.path('imgs')
        scrape_images(links_array=[self.site.url('/nosrc')], save_folder=folder)
        self.assertEqual(self.site.hits['/nosrc'], 1)
        self.assertEqual(os.listdir(folder), [])

    def test_child_pages_are_fetched_once(self):
        folder = self.path('imgs')
        scrape_images(links_array=[self.site.url('/with-child')], save_folder=folder,
                      follow_child_links=True, max_links_to_follow=5, max_workers=1)
        self.assertEqual(self.site.hits['/with-child'], 1)
        self.assertEqual(self.site.hits['/child-page'], 1)
        self.assertIn('logo.png', os.listdir(folder))


# ---------------------------------------------------------------------------
# Optional live check (off by default so the suite stays offline)
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get('PYWEBSCRAPR_LIVE_TESTS') == '1', 'set PYWEBSCRAPR_LIVE_TESTS=1 to run')
class LiveTests(TempDirTestCase):
    def test_example_com_text(self):
        out = self.path('live.txt')
        scrape_text(links_array=['https://example.com/'], output_file=out, timeout=(10, 30))
        self.assertIn('Example Domain', self.read('live.txt'))


if __name__ == '__main__':
    unittest.main()
