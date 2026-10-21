from __future__ import annotations

import argparse
import difflib
import logging
import os
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urljoin

if TYPE_CHECKING:
    from app.models.scraper import ScraperOutput, ScraperInput

# Add logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class Scraper:
    def __init__(self):
        from app.services.yaml_loader import YamlLoaderListings
        self.yaml_loader = YamlLoaderListings()

    def _select_engine(self, scraper_input: ScraperInput, output_filename: str | None = None):
        if scraper_input.scrapying_engine == "scrapy":
            # Importing this module installs the reactor before crochet setup.
            from app.scrapers.scrapy_scraper import ScrapyScraper
            return ScrapyScraper(scraper_input, output_filename)
        from app.scrapers.playwright_scraper import PlaywrightScraper
        return PlaywrightScraper(scraper_input, output_filename)

    def scrape_details(self, scraper_input: ScraperInput, urls: list[str], output_filename: str | None = None):
        """
        Scrape details from a list of URLs.
        
        :param scraper_input: Configuration for the scraper
        :param urls: List of URLs to scrape
        :param output_filename: Optional filename for saving results. If not provided, generates one.
        :return: List of scraped results (empty if using incremental save)
        """
        scraper_input.urls = urls
        os.makedirs("data", exist_ok=True)
        
        # Generate filename with timestamp for incremental saving if not provided
        if output_filename is None:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            output_filename = f"data/{scraper_input.name}_details_{timestamp}.csv"
        
        scraper = self._select_engine(scraper_input, output_filename)
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
        if not results and pages_filename and os.path.exists(pages_filename):
            df = pd.read_csv(pages_filename)
            for _, row in df.iterrows():
                # Use ast.literal_eval to parse Python dict string representation
                info_dict = ast.literal_eval(row['information']) if isinstance(row['information'], str) else row['information']
                for url_entry in info_dict.get('urls') or []:
                    if isinstance(url_entry, dict) and isinstance(url_entry.get('url'), str) and url_entry['url'].strip():
                        urls.append(url_entry['url'])
        else:
            # Original logic for when results are in memory
            for result in results:
                for url in result.information.get('urls') or []:
                    if isinstance(url, dict) and isinstance(url.get('url'), str) and url['url'].strip():
                        urls.append(url['url'])
        
        # Treat an explicit base as a directory, including for file:// fixtures.
        if urls:
            initial_url = scraper_input.urls[0]
            base_url = scraper_input.base_url.rstrip("/") + "/" if scraper_input.base_url else initial_url
            urls = [urljoin(base_url, url) for url in urls]
        
        return urls

    def scrape_urls(self, scraper_input: ScraperInput, output_filename: str | None = None):
        """
        Scrape URLs from pages.
        
        :param scraper_input: Configuration for the scraper
        :param output_filename: Optional filename for saving results. If not provided, generates one.
        :return: List of scraped results (empty if using incremental save)
        """
        os.makedirs("data", exist_ok=True)
        
        # Generate filename with timestamp for incremental saving if not provided
        if output_filename is None:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            output_filename = f"data/{scraper_input.name}_pages_{timestamp}.csv"
        
        scraper = self._select_engine(scraper_input, output_filename)
        results = [result for result in scraper.scrape()]
        
        # Results will be empty if incremental save is used, which is the expected behavior
        return results

    def scrape_from_yaml(self, scraper_name: str):
        """
        Main function to scrape data based on YAML configuration.
        
        :param scraper_name: Name of the scraper configuration to use
        """
        scraper_inputs = self.yaml_loader.load()
        names = [entry['name'] for entry in scraper_inputs]
        if scraper_name not in names:
            matches = difflib.get_close_matches(scraper_name, names, n=1)
            suggestion = f" Did you mean '{matches[0]}'?" if matches else ""
            raise ValueError(
                f"Unknown scraper '{scraper_name}'.{suggestion} Available scrapers: {', '.join(names)}. "
                "Run 'scrape --list' to see their descriptions."
            )
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
                if not urls:
                    pages = scraper_input['pages_input']
                    raise ValueError(
                        f"No listing URLs found for '{scraper_name}' on {', '.join(pages.urls)}. "
                        f"Check urls_xpath ({pages.information[0].xpath_values}) and that the search page loads. "
                        f"Search results, if saved: {pages_filename}."
                    )
                
                # Scrape details from urls
                results = self.scrape_details(scraper_input['details_input'], urls, details_filename)
                from app.services.data_saver import print_run_summary
                return print_run_summary(
                    scraper_name, pages_filename, details_filename,
                    [info.name for info in scraper_input['details_input'].information],
                )

def main():
    """Main entry point for the scrape command."""
    parser = argparse.ArgumentParser(description="Run a scraper by name")
    parser.add_argument("scraper_name", nargs="?", help="Name of the scraper to run")
    parser.add_argument("--list", action="store_true", help="List configured scrapers and descriptions")

    args = parser.parse_args()

    scraper = Scraper()
    try:
        if args.list or not args.scraper_name:
            entries = scraper.yaml_loader.load()
            print("Available scrapers (from config.yaml in the current directory):")
            for entry in entries:
                description = entry['pages_input'].description or "No description provided"
                print(f"  {entry['name']}: {description}")
            return 0
        return scraper.scrape_from_yaml(args.scraper_name)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
