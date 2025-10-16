import asyncio
from playwright.async_api import async_playwright, Page
from typing import Any, Iterator
from app.scrapers.base import BaseScraper
from app.models.scraper import ScraperInput, ScraperOutput, SimpleInfo, DynamicInfo
from app.services.data_saver import save_data_incremental
from app.utils.shadow_dom_utils import (
    wait_shadow_aware,
    extract_shadow_aware,
    _query_all_shadow_chain_handles,
    _clean_xpath_and_mode,
)
import logging
logging.getLogger('playwright').setLevel(logging.WARNING)
logger = logging.getLogger("playwright_scraper")

class PlaywrightScraper(BaseScraper):
    """
    Playwright-based implementation of the base scraper.
    
    This scraper uses Playwright to handle JavaScript-rendered pages and dynamic content.
    It includes automatic scrolling to trigger lazy-loaded content.
    """
    
    def __init__(self, scraper_input: ScraperInput, output_filename: str | None = None, headless: bool = False):
        """
        Initialize the Playwright scraper.
        
        :param scraper_input: Configuration for the scraper
        :param output_filename: Optional filename for incremental saving. If provided, data is saved continuously.
        :param headless: Whether to run browser in headless mode
        """
        super().__init__(scraper_input, output_filename)
        self.headless = headless
    
    def scrape(self) -> Iterator[ScraperOutput]:
        """
        Execute the scraping process for all configured URLs.
        
        :return: Iterator of ScraperOutput objects containing extracted data
        """
        results = []
        
        async def run_scraping():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (compatible; PlaywrightScraper/1.0)'
                )
                for url in self.urls:
                    logger.info(f"Scraping URL: {url}")
                    self.pages_visited[url] = 0
                    await self._scrape_url(context, url, results)
                await browser.close()
        
        logger.info("Starting Playwright scraping process")
        asyncio.run(run_scraping())
        logger.info("Finished Playwright scraping process")
        return iter(results)
    
    async def _scrape_url(self, context, origin_url: str, results: list):
        """
        Scrape a single URL and handle pagination.
        
        :param context: Playwright browser context
        :param origin_url: Starting URL to scrape
        :param results: List to append scraper results to
        """
        page = await context.new_page()
        current_url = origin_url
        page_number = 0
        
        try:
            logger.info(f"Opening page: {current_url}")
            await page.goto(current_url, wait_until='domcontentloaded', timeout=60000)
            await self._wait_for_content(page)
            
            while True:
                logger.info(f"Extracting data from: {page.url}")
                extracted_data = await self._extract_information_async(page, page.url)
                output = ScraperOutput(
                    url=page.url,
                    information=extracted_data
                )
                
                # Save incrementally if output_filename is provided
                if self.output_filename:
                    save_data_incremental(self.output_filename, output)
                    logger.info(f"Saved data incrementally to: {self.output_filename}")
                else:
                    results.append(output)
                
                should_continue = self._should_continue_pagination(page_number)
                if not should_continue or not self.next_url_xpath:
                    logger.info("No more pages or pagination disabled.")
                    break
                has_next = await self._click_next_page_async(page)
                if not has_next:
                    logger.info("No next page found. Stopping pagination.")
                    break
                await self._wait_for_content(page)
                page_number += 1
        
        finally:
            await page.close()
    
    async def _wait_for_content(self, page: Page):
        """
        Wait for content to load and scroll down the page to trigger lazy-loaded content.
        Uses shadow-aware waiting for custom elements.
        
        :param page: Playwright page object
        """
        if not self.information:
            return
        first_info = self.information[0]
        xpath_to_wait = None
        if isinstance(first_info, SimpleInfo):
            xpath_to_wait = first_info.xpath
        elif isinstance(first_info, DynamicInfo):
            if self._is_xpath(first_info.xpath_names):
                xpath_to_wait = first_info.xpath_names
            elif self._is_xpath(first_info.xpath_values):
                xpath_to_wait = first_info.xpath_values
        if xpath_to_wait:
            try:
                await wait_shadow_aware(page, xpath_to_wait, timeout_ms=30000)
            except Exception as e:
                logger.warning(f"Timeout waiting for content: {e}")
        
        # Scroll down the page to trigger lazy-loaded content
        await self._scroll_page(page)
    
    async def _scroll_page(self, page: Page, scroll_pause: float = 0.5):
        """
        Scroll down the page gradually to trigger lazy-loaded content.
        
        :param page: Playwright page object
        :param scroll_pause: Time to pause between scrolls in seconds
        """
        try:
            # Get the total height of the page
            previous_height = await page.evaluate("document.body.scrollHeight")
            
            # Scroll in increments
            scroll_step = 500
            current_position = 0
            
            while True:
                # Scroll down by scroll_step pixels
                await page.evaluate(f"window.scrollBy(0, {scroll_step})")
                current_position += scroll_step
                
                # Wait for content to load
                await asyncio.sleep(scroll_pause)
                
                # Check if we've reached the bottom
                current_height = await page.evaluate("document.body.scrollHeight")
                current_scroll = await page.evaluate("window.pageYOffset + window.innerHeight")
                
                # If we've scrolled past the previous height, update it
                if current_height > previous_height:
                    previous_height = current_height
                # If we're at the bottom and height hasn't changed, we're done
                elif current_scroll >= current_height:
                    break
                
                # Safety check: if we've scrolled way too much, break
                if current_position > current_height + 10000:
                    break
            
            # Scroll back to top for consistent extraction
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.3)
            
        except Exception as e:
            logger.warning(f"Error during page scrolling: {e}")
    
    async def _extract_information_async(self, page: Page, current_url: str) -> dict[str, Any]:
        """
        Extract information from a page using configured XPath selectors.
        Ensures all extracted values are properly converted to strings.
        
        :param page: Playwright page object
        :param current_url: Current URL being scraped
        :return: Dictionary containing extracted data
        """
        extracted_data = {}
        for info in self.information:
            if isinstance(info, SimpleInfo):
                value = await self._extract_value_async(page, info.xpath)
                extracted_data[info.name] = value
            elif isinstance(info, DynamicInfo):
                if self._is_xpath(info.xpath_names):
                    names = await self._extract_values_async(page, info.xpath_names)
                else:
                    names = [str(info.xpath_names)]
                if self._is_xpath(info.xpath_values):
                    values = await self._extract_values_async(page, info.xpath_values)
                else:
                    values = [str(info.xpath_values)]
                dynamic_list = []
                max_len = max(len(names), len(values))
                for i in range(max_len):
                    name = str(names[i % len(names)]) if names else f"key_{i}"
                    value = str(values[i % len(values)]) if values else None
                    dynamic_list.append({name: value})
                extracted_data[info.name] = dynamic_list
        return extracted_data
    
    async def _extract_value_async(self, page: Page, xpath: str) -> str | None:
        """
        Extract a single value from the page using an XPath selector.
        Uses shadow-aware extraction for custom elements.
        If multiple values are found, they are joined with ' | ' separator.
        
        :param page: Playwright page object
        :param xpath: XPath selector to locate the element
        :return: Extracted text or attribute value (joined if multiple), or None if not found
        """
        try:
            values = await extract_shadow_aware(page, xpath)
            if values:
                # Join multiple values with separator to preserve all data
                return ' | '.join(values) if len(values) > 1 else values[0]
            return None
        except Exception as e:
            logger.warning(f"Error extracting value with xpath '{xpath}': {e}")
            return None
    
    async def _extract_values_async(self, page: Page, xpath: str) -> list[str]:
        """
        Extract multiple values from the page using an XPath selector.
        Uses shadow-aware extraction for custom elements.
        Ensures all values are returned as strings.
        
        :param page: Playwright page object
        :param xpath: XPath selector to locate the elements
        :return: List of extracted text or attribute values (all strings)
        """
        try:
            values = await extract_shadow_aware(page, xpath)
            # Ensure all values are strings
            return [str(v) for v in values] if values else []
        except Exception as e:
            logger.warning(f"Error extracting values with xpath '{xpath}': {e}")
            return []
    
    async def _click_next_page_async(self, page: Page) -> bool:
        """
        Click the next page button to navigate to the next page.
        Uses shadow-aware element location for custom elements.
        
        :param page: Playwright page object
        :return: True if successfully navigated to next page, False otherwise
        """
        if not self.next_url_xpath:
            return False
        try:
            # Wait for the next button using shadow-aware wait
            await wait_shadow_aware(page, self.next_url_xpath, timeout_ms=10000)
            
            # Get the element handle using shadow-aware query
            clean_xpath, _, _ = _clean_xpath_and_mode(self.next_url_xpath)
            handles = await _query_all_shadow_chain_handles(page, clean_xpath)
            
            if handles:
                element = handles[0]
                logger.info("Clicking next page button")
                await element.click()
                await page.wait_for_load_state('domcontentloaded', timeout=30000)
                return True
            return False
        except Exception as e:
            logger.info(f"Could not click next page: {e}")
            return False
    
    # Synchronous methods required by base class (not used in async context)
    def _extract_value(self, page_content: Any, xpath: str) -> str | None:
        raise NotImplementedError("Use _extract_value_async instead")
    
    def _extract_values(self, page_content: Any, xpath: str) -> list[str]:
        raise NotImplementedError("Use _extract_values_async instead")
    
    def _get_next_url(self, page_content: Any, current_url: str) -> str | None:
        raise NotImplementedError("Use _get_next_url_async instead")
