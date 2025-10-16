from abc import ABC, abstractmethod
from typing import Any, Iterator
from models.scraper import ScraperInput, ScraperOutput, SimpleInfo, DynamicInfo

class BaseScraper(ABC):
    """Base class for web scrapers using different engines."""
    
    def __init__(self, scraper_input: ScraperInput, output_filename: str | None = None):
        self.scraper_input = scraper_input
        self.urls = scraper_input.urls
        self.next_url_xpath = scraper_input.next_url_xpath
        self.number_of_pages = scraper_input.number_of_pages
        self.information = scraper_input.information
        self.pages_visited = {}
        self.output_filename = output_filename
    
    @abstractmethod
    def scrape(self) -> Iterator[ScraperOutput]:
        """Main method to execute scraping. Returns iterator of ScraperOutput."""
        pass
    
    @abstractmethod
    def _extract_value(self, page_content: Any, xpath: str) -> str | None:
        """Extract a single value using xpath from page content."""
        pass
    
    @abstractmethod
    def _extract_values(self, page_content: Any, xpath: str) -> list[str]:
        """Extract multiple values using xpath from page content."""
        pass
    
    @abstractmethod
    def _get_next_url(self, page_content: Any, current_url: str) -> str | None:
        """Get the next page URL."""
        pass
    
    def _should_continue_pagination(self, current_page_number: int) -> bool:
        """
        Determine if we should continue to next page based on number_of_pages setting.
        
        - number_of_pages = 0: Continue indefinitely (until no next page)
        - number_of_pages = 1: Only scrape first page (no pagination)
        - number_of_pages = N: Scrape N pages total
        """
        if self.number_of_pages == 0:
            return True
        elif self.number_of_pages == 1:
            return False
        else:
            return current_page_number < (self.number_of_pages - 1)
    
    def _is_xpath(self, string: str) -> bool:
        """
        Detect if a string is an XPath expression.
        XPaths typically start with / or // or contain XPath syntax.
        """
        if not string:
            return False
        
        xpath_indicators = [
            string.startswith('/'),
            string.startswith('//'),
            string.startswith('.//'),
            string.startswith('./'),
            '@' in string and ('/' in string or '[' in string),
            '::' in string,
        ]
        
        return any(xpath_indicators)
    
    def _extract_information(self, page_content: Any, current_url: str) -> dict[str, Any]:
        """
        Extract all information from page content based on scraper_input.information.
        Ensures all extracted values are properly converted to strings.
        
        :param page_content: Page content to extract from (response object)
        :param current_url: Current URL being scraped
        :return: Dictionary containing extracted data
        """
        extracted_data = {}
        
        for info in self.information:
            if isinstance(info, SimpleInfo):
                value = self._extract_value(page_content, info.xpath)
                extracted_data[info.name] = value
            
            elif isinstance(info, DynamicInfo):
                if self._is_xpath(info.xpath_names):
                    names = self._extract_values(page_content, info.xpath_names)
                else:
                    names = [str(info.xpath_names)]
                
                if self._is_xpath(info.xpath_values):
                    values = self._extract_values(page_content, info.xpath_values)
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