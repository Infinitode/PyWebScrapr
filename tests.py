import unittest
from pywebscrapr import scrape_images, scrape_text

class TestPyWebScraper(unittest.TestCase):
    def test_scrape_images(self):
        # Test scraping images using links from a file
        scrape_images(links_file='test_links.txt', save_folder='output', min_height=128)

    def test_scrape_text(self):
        # Test scraping text using links directly
        links = ['https://blog.google/technology/google-deepmind/google-gemini-ai-update-december-2024/']
        scrape_text(links_array=links, output_file='output.txt', csv_output_file='csv_output.csv', remove_extra_whitespace=True, remove_duplicates=True, elements_to_scrape='text', similarity_threshold=0.8)

if __name__ == '__main__':
    unittest.main()