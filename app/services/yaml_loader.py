import yaml
from app.models.scraper import ScraperInput, SimpleInfo, DynamicInfo

class YamlLoaderListings:
    def __init__(self):
        self.default_yaml_file = "config.yaml"

    def load(self, yaml_file: str = None):
        """Load yaml file and return ScraperInput"""
        with open(self.default_yaml_file if yaml_file is None else yaml_file, 'r') as file:
            yaml_data = yaml.safe_load(file)
        yaml_data = yaml_data['scrapers']
        scraper_inputs = []
        for scraper in yaml_data:
            # Scraper for Pages
            pages_input = ScraperInput(
                                name=scraper['name'],
                                description=scraper['description'] if 'description' in scraper else None,
                                base_url=scraper['base_url'] if 'base_url' in scraper else None,
                                urls=[scraper['pages_url']] if isinstance(scraper['pages_url'], str) else scraper['pages_url'],
                                next_url_xpath=scraper['next_xpath'],
                                number_of_pages=scraper['number_of_pages'],            # 0 => keep going until no next
                                information=[
                                    DynamicInfo(
                                        name="urls",
                                        xpath_names='url',
                                        xpath_values=scraper['urls_xpath']
                                    )
                                ],
                                scrapying_engine=scraper['scrapying_engine'] if 'scrapying_engine' in scraper else "scrapy"
                                )

            information = []
            for item in scraper['information']:
                if "xpath" in item:
                    information.append(SimpleInfo(name=item['name'], xpath=item['xpath']))
                elif "xpath_names" in item:
                    information.append(DynamicInfo(name=item['name'], xpath_names=item['xpath_names'], xpath_values=item['xpath_values']))

            details_input = ScraperInput(
                name=scraper['name'],
                description=scraper['description'] if 'description' in scraper else None,
                urls=pages_input.urls,
                base_url=pages_input.base_url,
                next_url_xpath=None,
                number_of_pages=0,
                information=information,
                scrapying_engine=scraper['scrapying_engine'] if 'scrapying_engine' in scraper else "scrapy"
            )
            scraper_inputs.append({"name": scraper['name'], "pages_input": pages_input, "details_input": details_input})
        return scraper_inputs