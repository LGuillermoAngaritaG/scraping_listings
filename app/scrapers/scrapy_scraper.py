import scrapy
from scrapy.crawler import CrawlerRunner
from crochet import setup, wait_for
from typing import Any, Iterator
from urllib.parse import urljoin
from scrapers.base import BaseScraper, ScraperInput, ScraperOutput
from services.data_saver import save_data_incremental

# Install asyncio reactor before crochet setup to match Scrapy's requirements
import asyncio
from twisted.internet import asyncioreactor
asyncioreactor.install(asyncio.new_event_loop())
import logging
logging.getLogger('scrapy').setLevel(logging.WARNING)
logger = logging.getLogger("scrapy_scraper")
# Setup crochet to manage the reactor in a separate thread
# This allows the scraper to be run multiple times
setup()


class ScrapyScraper(BaseScraper):
    """Scrapy-based implementation of the base scraper using crochet for reactor management."""

    def __init__(self, scraper_input: ScraperInput, output_filename: str | None = None):
        """
        Initialize the Scrapy scraper.
        
        :param scraper_input: Configuration object containing URLs, XPath selectors, and pagination settings.
        :param output_filename: Optional filename for incremental saving. If provided, data is saved continuously.
        """
        super().__init__(scraper_input, output_filename)
        self.runner = CrawlerRunner(settings={
            'LOG_LEVEL': 'INFO',
            'USER_AGENT': 'Mozilla/5.0 (compatible; ScrapyScraper/1.0)',
            'TWISTED_REACTOR': 'twisted.internet.asyncioreactor.AsyncioSelectorReactor',
        })
    
    @wait_for(timeout=300.0)  # 5 minute timeout
    def scrape(self) -> Iterator[ScraperOutput]:
        """
        Execute scraping using Scrapy with crochet for restartable execution.
        
        :return: Iterator of ScraperOutput objects containing scraped data.
        """
        results = []
        
        class DynamicSpider(scrapy.Spider):
            name = 'dynamic_scraper'
            
            def __init__(self, scraper_instance, *args, **kwargs):
                super(DynamicSpider, self).__init__(*args, **kwargs)
                self.scraper = scraper_instance
                self.start_urls = scraper_instance.urls
                self.results = results
            
            def start_requests(self):
                logger.info(f"Starting requests for URLs: {self.start_urls}")
                for url in self.start_urls:
                    self.scraper.pages_visited[url] = 0
                    yield scrapy.Request(
                        url=url,
                        callback=self.parse,
                        meta={'origin_url': url, 'page_number': 0}
                    )
            
            def parse(self, response):
                origin_url = response.meta.get('origin_url')
                page_number = response.meta.get('page_number', 0)
                
                logger.info(f"Scraping page: {response.url} (page {page_number})")
                extracted_data = self.scraper._extract_information(response, response.url)
                
                output = ScraperOutput(
                    url=response.url,
                    information=extracted_data
                )
                
                # Save incrementally if output_filename is provided
                if self.scraper.output_filename:
                    save_data_incremental(self.scraper.output_filename, output)
                    logger.info(f"Saved data incrementally to: {self.scraper.output_filename}")
                else:
                    self.results.append(output)
                
                logger.info(f"Scraped data from: {response.url}")

                yield output.model_dump()
                
                should_continue = self.scraper._should_continue_pagination(page_number)
                
                if should_continue and self.scraper.next_url_xpath:
                    next_url = self.scraper._get_next_url(response, response.url)
                    
                    if next_url:
                        logger.info(f"Following pagination to: {next_url}")
                        yield scrapy.Request(
                            url=next_url,
                            callback=self.parse,
                            meta={
                                'origin_url': origin_url,
                                'page_number': page_number + 1
                            }
                        )
                    else:
                        logger.info("No next page found.")
                else:
                    logger.info("Stopping pagination.")
        
        logger.info("Launching Scrapy spider.")
        deferred = self.runner.crawl(DynamicSpider, scraper_instance=self)
        deferred.addCallback(lambda _: iter(results))
        logger.info("Spider finished.")
        return deferred
    
    def _extract_value(self, page_content: Any, xpath: str) -> str | None:
        """
        Extract a single value using xpath from Scrapy response.
        If multiple values are found, they are joined with ' | ' separator.
        
        :param page_content: Scrapy response object to extract from.
        :param xpath: XPath expression to locate the element.
        :return: Extracted text or attribute value (joined if multiple), None if not found.
        """
        # Try to get all values first
        values = page_content.xpath(xpath).getall()
        if values:
            # Join multiple values with separator to preserve all data
            result = ' | '.join(str(v) for v in values) if len(values) > 1 else str(values[0])
            logger.debug(f"_extract_value with xpath '{xpath}': {result}")
            return result
        logger.debug(f"_extract_value with xpath '{xpath}': None")
        return None
    
    def _extract_values(self, page_content: Any, xpath: str) -> list[str]:
        """
        Extract multiple values using xpath from Scrapy response.
        Ensures all values are returned as strings.
        
        :param page_content: Scrapy response object to extract from.
        :param xpath: XPath expression to locate the elements.
        :return: List of extracted text or attribute values (all strings).
        """
        values = page_content.xpath(xpath).getall()
        # Ensure all values are strings
        result = [str(v) for v in values] if values else []
        logger.debug(f"_extract_values with xpath '{xpath}': found {len(result)} values")
        return result
    
    def _get_next_url(self, page_content: Any, current_url: str) -> str | None:
        """
        Get the next page URL from Scrapy response.
        
        :param page_content: Scrapy response object to extract from.
        :param current_url: Current page URL for resolving relative URLs.
        :return: Absolute URL of the next page, None if not found.
        """
        if not self.next_url_xpath:
            return None
        
        next_url = page_content.xpath(self.next_url_xpath).get()
        
        if next_url:
            logger.debug(f"Next page URL extracted: {next_url}")
            return urljoin(current_url, next_url)
        
        logger.debug("No next page URL found.")
        return None


# if __name__ == "__main__":
#     from base_scraper import SimpleInfo, DynamicInfo
    
#     scraper_input = ScraperInput(
#         urls=["https://example.com"],
#         next_url_xpath="//a[@class='next']/@href",
#         number_of_pages=3,
#         information=[
#             SimpleInfo(name="title", xpath="//h1/text()"),
#             SimpleInfo(name="description", xpath="//meta[@name='description']/@content"),
#             DynamicInfo(
#                 name="links",
#                 xpath_names="//a/@title",
#                 xpath_values="//a/@href"
#             )
#         ]
#     )
    
#     scraper = ScrapyScraper(scraper_input)
    
#     for result in scraper.scrape():
#         print(f"URL: {result.url}")
#         print(f"Data: {result.information}")
#         print("-" * 80)