import unittest
from pywebscrapr import scrape_images, scrape_text

class TestPyWebScraper(unittest.TestCase):
    def test_scrape_images(self):
        # Test scraping images using links from a file
        scrape_images(links_file='test_links.txt', save_folder='output', min_height=128, follow_child_links=False)

    def test_scrape_text(self):
        # Test scraping text using links directly
        links = ['https://blog.google/technology/google-deepmind/google-gemini-ai-update-december-2024/']
        scrape_text(links_array=links, output_file='output.txt', json_output_file='json_output.json', remove_duplicates=False, remove_extra_whitespace=True, elements_to_scrape='text', follow_child_links=True, max_links_to_follow=10)

if __name__ == '__main__':
    unittest.main()