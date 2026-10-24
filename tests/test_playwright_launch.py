from types import SimpleNamespace

import pytest
from playwright.async_api import Error

from app.models.scraper import ScraperInput, SimpleInfo


@pytest.mark.parametrize("missing", [True, False])
def test_launch_error_only_explains_missing_browser(monkeypatch, capsys, missing):
    import app.scrapers.playwright_scraper as module
    from app.main import Scraper, main

    original_error = Error("BrowserType.launch: Executable doesn't exist at /missing/chromium"
                           if missing else "BrowserType.launch: Permission denied")

    async def launch(**kwargs):
        raise original_error

    class PlaywrightContext:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(module, "async_playwright", PlaywrightContext)
    scraper_input = ScraperInput(name="local-test", urls=["https://example.com"],
                                 information=[SimpleInfo(name="title", xpath="//h1")],
                                 scrapying_engine="playwright")

    def scrape(self, name):
        return list(module.PlaywrightScraper(scraper_input).scrape())

    monkeypatch.setattr(Scraper, "scrape_from_yaml", scrape)
    monkeypatch.setattr("sys.argv", ["scrape", "local-test"])
    if missing:
        assert main() == 1
        output = capsys.readouterr().err
        assert output.strip() == (
            "Error: Chromium for Playwright is not installed; run: playwright install chromium"
        )
    else:
        with pytest.raises(Error) as caught:
            main()
        assert caught.value is original_error
