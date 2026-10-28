import yaml
from pathlib import Path
from pydantic import ValidationError
from app.models.scraper import ScraperInput, SimpleInfo, DynamicInfo


class ConfigurationError(ValueError):
    """A configuration problem the person running the scraper can fix."""


class YamlLoaderListings:
    def __init__(self):
        self.default_yaml_file = "config.yaml"

    def load(self, yaml_file: str = None):
        """Load yaml file and return ScraperInput"""
        path = Path(self.default_yaml_file if yaml_file is None else yaml_file).resolve()
        try:
            with path.open(encoding="utf-8") as file:
                yaml_data = yaml.safe_load(file)
        except FileNotFoundError:
            raise ConfigurationError(
                f"Config file not found: {path}. The CLI reads config.yaml from the current directory; "
                "run from the project directory or create config.yaml there."
            ) from None
        except yaml.YAMLError as error:
            raise ConfigurationError(
                f"Invalid YAML in {path}: {error.problem}. Fix the YAML syntax and try again."
            ) from None
        except (OSError, UnicodeError) as error:
            raise ConfigurationError(
                f"Cannot read {path}: {error}. Check the file's permissions and UTF-8 encoding."
            ) from None
        if not isinstance(yaml_data, dict) or not isinstance(yaml_data.get('scrapers'), list) or not yaml_data['scrapers']:
            raise ConfigurationError(f"Invalid config {path}: add a non-empty 'scrapers' list.")

        scraper_inputs = []
        for index, scraper in enumerate(yaml_data['scrapers'], 1):
            if not isinstance(scraper, dict):
                raise ConfigurationError(f"Invalid config {path}: scraper entry {index} must be a mapping; add its name and settings.")
            name = scraper.get('name', f'entry {index}')

            def invalid(problem):
                return ConfigurationError(f"Invalid config {path} for scraper '{name}': {problem}. Fix this entry and try again.")

            for key in ('name', 'pages_url', 'urls_xpath', 'next_xpath', 'number_of_pages', 'information'):
                if key not in scraper:
                    raise invalid(f"missing required key '{key}'; add it")
            if not isinstance(name, str) or not name.strip():
                raise invalid("'name' must be a non-empty string")
            unsupported = scraper.get('unsupported')
            if 'unsupported' in scraper and (not isinstance(unsupported, str) or not unsupported.strip()):
                raise invalid("'unsupported' must be a non-empty string explaining why this scraper cannot run")
            urls = [scraper['pages_url']] if isinstance(scraper['pages_url'], str) else scraper['pages_url']
            if not isinstance(urls, list) or not urls or any(not isinstance(url, str) or not url.strip() for url in urls):
                raise invalid("'pages_url' must be a URL or a non-empty list of URLs")
            if not isinstance(scraper['urls_xpath'], str) or not scraper['urls_xpath'].strip():
                raise invalid("'urls_xpath' must be a non-empty selector")
            if not isinstance(scraper['information'], list) or not scraper['information']:
                raise invalid("'information' must be a non-empty list of named selectors")

            information = []
            for item in scraper['information']:
                if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip():
                    raise invalid("each 'information' entry needs a non-empty 'name'")
                keys = ('xpath',) if 'xpath' in item else ('xpath_names', 'xpath_values')
                for key in keys:
                    if not isinstance(item.get(key), str) or not item[key].strip():
                        raise invalid(f"information '{item['name']}' needs a non-empty '{key}' selector or value")
                if 'xpath' in item:
                    information.append(SimpleInfo(name=item['name'], xpath=item['xpath']))
                else:
                    information.append(DynamicInfo(name=item['name'], xpath_names=item['xpath_names'], xpath_values=item['xpath_values']))

            try:
                pages_input = ScraperInput(
                    name=name,
                    description=scraper.get('description'),
                    base_url=scraper.get('base_url'),
                    urls=urls,
                    next_url_xpath=scraper['next_xpath'],
                    number_of_pages=scraper['number_of_pages'],
                    information=[DynamicInfo(name="urls", xpath_names='url', xpath_values=scraper['urls_xpath'])],
                    scrapying_engine=scraper.get('scrapying_engine', 'scrapy'),
                )
            except ValidationError as error:
                issue = error.errors()[0]
                key = {'urls': 'pages_url', 'next_url_xpath': 'next_xpath'}.get(issue['loc'][0], issue['loc'][0])
                raise invalid(f"'{key}': {issue['msg']}") from None
            details_input = pages_input.model_copy(update={
                'next_url_xpath': None, 'number_of_pages': 0, 'information': information,
            })
            scraper_inputs.append({"name": scraper['name'], "pages_input": pages_input, "details_input": details_input,
                                   "unsupported": " ".join(unsupported.split()) if unsupported else None})
        return scraper_inputs
