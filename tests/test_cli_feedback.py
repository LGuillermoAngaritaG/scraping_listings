import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app.models.scraper import ScraperOutput
from app.services.yaml_loader import YamlLoaderListings


CONFIG = {
    "name": "local-test",
    "description": "Local listings for testing",
    "pages_url": "https://example.com/search",
    "base_url": "https://example.com",
    "urls_xpath": "//a/@href",
    "next_xpath": None,
    "number_of_pages": 1,
    "information": [{"name": "title", "xpath": "//h1/text()"}],
}


def write_config(path, scraper=None):
    path.write_text(yaml.safe_dump({"scrapers": [CONFIG if scraper is None else scraper]}))


@pytest.mark.parametrize("key", ["pages_url", "urls_xpath", "information", "next_xpath", "number_of_pages"])
def test_loader_names_scraper_and_missing_key(tmp_path, key):
    config = copy.deepcopy(CONFIG)
    del config[key]
    path = tmp_path / "config.yaml"
    write_config(path, config)

    with pytest.raises(ValueError) as error:
        YamlLoaderListings().load(str(path))

    message = str(error.value)
    assert "local-test" in message and key in message
    assert "config.yaml" in message and "add" in message.lower()


@pytest.mark.parametrize("value", [None, [], "wrong", {"scrapers": []}, {"scrapers": [None]}])
def test_loader_rejects_invalid_root_with_next_step(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(value))

    with pytest.raises(ValueError, match="config.yaml"):
        YamlLoaderListings().load(str(path))


@pytest.mark.parametrize(("key", "value"), [
    ("pages_url", []), ("urls_xpath", " "), ("information", []),
    ("information", [{"name": "title"}]),
    ("information", [{"name": "specs", "xpath_names": "//dt/text()"}]),
    ("scrapying_engine", "unknown"),
])
def test_loader_rejects_invalid_values_with_context(tmp_path, key, value):
    config = copy.deepcopy(CONFIG)
    config[key] = value
    path = tmp_path / "config.yaml"
    write_config(path, config)

    with pytest.raises(ValueError) as error:
        YamlLoaderListings().load(str(path))

    assert "local-test" in str(error.value)
    assert key in str(error.value)


def test_cli_discovery_and_errors_do_not_load_heavy_dependencies(tmp_path):
    write_config(tmp_path / "config.yaml")
    # Run a fresh interpreter for each path so a prior import cannot mask eager loading.
    code = """
import json, sys
from app.main import main
sys.argv = ['scrape'] + json.loads(sys.argv[1])
try:
    status = main()
except SystemExit as error:
    status = error.code
print('RESULT=' + json.dumps({'status': status, 'heavy': sorted(
    name for name in ('pandas', 'scrapy', 'playwright') if name in sys.modules)}))
"""
    for args, expected_code, text in [
        (["--list"], 0, CONFIG["description"]),
        ([], 0, CONFIG["description"]),
        (["--help"], 0, "--list"),
        (["local-tes"], 1, "local-test"),
    ]:
        result = subprocess.run([sys.executable, "-c", code, json.dumps(args)],
                                cwd=tmp_path, capture_output=True, text=True, check=True)
        assert text in result.stdout + result.stderr
        assert "Traceback" not in result.stderr
        report = json.loads(result.stdout.split("RESULT=")[1])
        assert report == {"status": expected_code, "heavy": []}
    assert not (tmp_path / "data").exists()


def test_missing_config_and_yaml_syntax_have_plain_errors(tmp_path):
    for contents in (None, "scrapers: ["):
        if contents is not None:
            (tmp_path / "config.yaml").write_text(contents)
        result = subprocess.run([sys.executable, "-m", "app.main", "local-test"],
                                cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 1
        assert "config.yaml" in result.stderr
        assert "Traceback" not in result.stderr
        if contents is None:
            assert str(tmp_path) in result.stderr
            assert "current directory" in result.stderr


def test_get_urls_ignores_empty_entries_and_values(tmp_path, monkeypatch):
    from app.main import Scraper

    monkeypatch.chdir(tmp_path)
    write_config(tmp_path / "config.yaml")
    scraper = Scraper()
    pages = scraper.yaml_loader.load()[0]["pages_input"]
    output = ScraperOutput(url=CONFIG["pages_url"], information={
        "urls": [None, {"url": None}, {"url": ""}, {"url": "d1.html"}]
    })

    assert scraper._get_urls(pages, [output]) == ["https://example.com/d1.html"]


@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize(("base_url", "page_url", "hrefs", "expected"), [
    ("https://example.com", "https://example.com/search",
     ["/listing/1", "listing/2", "https://other.example/listing/3"],
     ["https://example.com/listing/1", "https://example.com/listing/2",
      "https://other.example/listing/3"]),
    ("https://example.com", "https://example.com/search",
     ["https://other.example/listing/1", "/listing/2"],
     ["https://other.example/listing/1", "https://example.com/listing/2"]),
    (None, "https://example.com/search/index.html", ["../listing/1", "/listing/2"],
     ["https://example.com/listing/1", "https://example.com/listing/2"]),
    ("file:///tmp/site", "file:///tmp/site/search.html", ["d1.html"],
     ["file:///tmp/site/d1.html"]),
])
def test_get_urls_resolves_each_href(tmp_path, monkeypatch, saved, base_url, page_url,
                                    hrefs, expected):
    from app.main import Scraper
    from app.services.data_saver import save_data_incremental

    monkeypatch.chdir(tmp_path)
    write_config(tmp_path / "config.yaml")
    scraper = Scraper()
    pages = scraper.yaml_loader.load()[0]["pages_input"]
    pages.base_url = base_url
    pages.urls = [page_url]
    output = ScraperOutput(url=page_url, information={"urls": [{"url": href} for href in hrefs]})
    filename = str(tmp_path / "pages.csv")
    if saved:
        save_data_incremental(filename, output)

    assert scraper._get_urls(pages, [] if saved else [output], filename) == expected


@pytest.mark.parametrize("has_page_csv", [True, False])
def test_no_listing_urls_stop_before_details(tmp_path, monkeypatch, has_page_csv):
    from app.main import Scraper
    from app.services.data_saver import save_data_incremental

    monkeypatch.chdir(tmp_path)
    write_config(tmp_path / "config.yaml")
    scraper = Scraper()

    def empty_search(scraper_input, output_filename):
        if has_page_csv:
            Path(output_filename).parent.mkdir(exist_ok=True)
            save_data_incremental(output_filename, ScraperOutput(
                url=CONFIG["pages_url"], information={"urls": [{"url": None}]}
            ))
        return []

    def unexpected_details(*args):
        pytest.fail("No listing URLs should stop before starting the details engine")

    monkeypatch.setattr(scraper, "scrape_urls", empty_search)
    monkeypatch.setattr(scraper, "scrape_details", unexpected_details)

    with pytest.raises(ValueError) as error:
        scraper.scrape_from_yaml("local-test")

    message = str(error.value)
    assert "urls_xpath" in message and CONFIG["pages_url"] in message
    assert "check" in message.lower()
    assert not list(Path("data").glob("*_details_*.csv"))
