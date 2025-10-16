from models.scraper import ScraperOutput, ScraperInput
from services.yaml_loader import YamlLoaderListings
from services.data_saver import save_data
from scrapers.scrapy_scraper import ScrapyScraper
from scrapers.playwright_scraper import PlaywrightScraper
import argparse
import logging
import os
from datetime import datetime, timezone

# Add logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class Scraper:
    def __init__(self):
        self.yaml_loader = YamlLoaderListings()
        # Ensure data directory exists
        os.makedirs("data", exist_ok=True)

    def _select_engine(self, scraper_input: ScraperInput):
        return ScrapyScraper(scraper_input) if scraper_input.scrapying_engine == "scrapy" else PlaywrightScraper(scraper_input)

    def scrape_details(self, scraper_input: ScraperInput, urls: list[str], output_filename: str | None = None):
        """
        Scrape details from a list of URLs.
        
        :param scraper_input: Configuration for the scraper
        :param urls: List of URLs to scrape
        :param output_filename: Optional filename for saving results. If not provided, generates one.
        :return: List of scraped results (empty if using incremental save)
        """
        scraper_engine = scraper_input.scrapying_engine
        scraper_input.urls = urls
        
        # Generate filename with timestamp for incremental saving if not provided
        if output_filename is None:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            output_filename = f"data/{scraper_input.name}_details_{timestamp}.csv"
        
        scraper = ScrapyScraper(scraper_input, output_filename) if scraper_engine == "scrapy" else PlaywrightScraper(scraper_input, output_filename)
        results = [result for result in scraper.scrape()]
        
        # Results will be empty if incremental save is used, which is the expected behavior
        return results

    def _get_urls(self, scraper_input: ScraperInput, results: list[ScraperOutput], pages_filename: str | None = None):
        """
        Extract URLs from scraped results or from saved CSV file.
        
        :param scraper_input: Configuration for the scraper
        :param results: List of scraped results (may be empty if incremental save was used)
        :param pages_filename: Path to pages CSV file if results is empty
        :return: List of URLs to scrape
        """
        import pandas as pd
        import ast
        
        urls = []
        
        # If results are empty, read from the saved CSV file
        if not results and pages_filename:
            df = pd.read_csv(pages_filename)
            for _, row in df.iterrows():
                # Use ast.literal_eval to parse Python dict string representation
                info_dict = ast.literal_eval(row['information']) if isinstance(row['information'], str) else row['information']
                for url_entry in info_dict['urls']:
                    urls.append(url_entry['url'])
        else:
            # Original logic for when results are in memory
            for result in results:
                for url in result.information['urls']:
                    urls.append(url['url'])
        
        # if there is no base_url, add it
        if urls:
            initial_url = scraper_input.urls[0]
            base_url = scraper_input.base_url or "/".join(initial_url.split("/")[:2])
            if "http" not in urls[0]:
                urls = [base_url + "/" + url for url in urls]
        
        return urls

    def scrape_urls(self, scraper_input: ScraperInput, output_filename: str | None = None):
        """
        Scrape URLs from pages.
        
        :param scraper_input: Configuration for the scraper
        :param output_filename: Optional filename for saving results. If not provided, generates one.
        :return: List of scraped results (empty if using incremental save)
        """
        scraper_engine = scraper_input.scrapying_engine
        
        # Generate filename with timestamp for incremental saving if not provided
        if output_filename is None:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            output_filename = f"data/{scraper_input.name}_pages_{timestamp}.csv"
        
        scraper = ScrapyScraper(scraper_input, output_filename) if scraper_engine == "scrapy" else PlaywrightScraper(scraper_input, output_filename)
        results = [result for result in scraper.scrape()]
        
        # Results will be empty if incremental save is used, which is the expected behavior
        return results

    def scrape_from_yaml(self, scraper_name: str):
        """
        Main function to scrape data based on YAML configuration.
        
        :param scraper_name: Name of the scraper configuration to use
        """
        yaml_loader = YamlLoaderListings()
        scraper_inputs = yaml_loader.load()
        for scraper_input in scraper_inputs:
            if scraper_input['name'] == scraper_name:
                # Generate timestamp for this scraping session
                timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
                pages_filename = f"data/{scraper_input['name']}_pages_{timestamp}.csv"
                details_filename = f"data/{scraper_input['name']}_details_{timestamp}.csv"
                
                # Scrape urls from pages
                results = self.scrape_urls(scraper_input['pages_input'], pages_filename)
                
                # Scrape details from urls
                urls = self._get_urls(scraper_input['pages_input'], results, pages_filename)
                
                # Scrape details from urls
                results = self.scrape_details(scraper_input['details_input'], urls, details_filename)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a scraper by name")
    parser.add_argument("scraper_name", type=str, help="Name of the scraper to run")
    
    args = parser.parse_args()
    
    scraper = Scraper()
    scraper.scrape_from_yaml(args.scraper_name)