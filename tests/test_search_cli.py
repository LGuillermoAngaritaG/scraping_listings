import csv
import os
import subprocess
import sys

import pytest

from app.services.export import EXPORT_COLUMNS


def write_export(tmp_path, rows):
    path = tmp_path / "data" / "listings.csv"
    path.parent.mkdir(exist_ok=True)
    defaults = {"source": "local", "title": "Apartamento", "location": "Usaquén",
                "price": "2000000", "bedrooms": "2", "bathrooms": "1", "area_m2": "50",
                "last_seen": "2026-07-19T00:00:00+00:00"}
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows({**defaults, **row} for row in rows)
    return path


def search(tmp_path, *args):
    return subprocess.run([sys.executable, "-m", "app.main", "search", *args],
                          cwd=tmp_path, text=True, capture_output=True)


def test_cli_filters_sorts_limits_and_normalizes_links_without_config(tmp_path):
    path = write_export(tmp_path, [
        {"url": "https://example.com//one?src=old", "price": "1000000",
         "last_seen": "2026-07-18T00:00:00+00:00"},
        {"url": "https://example.com/one?src=new", "price": "3000000"},
        {"url": "https://example.com/two", "price": "2500000", "title": "Cheaper home"},
        {"url": "https://example.com/three", "location": "Chapinero"}])
    before = path.read_bytes()
    result = search(tmp_path, "--min-price", "2000000", "--max-price", "3000000",
                    "--bedrooms", "2", "--bathrooms", "1", "--min-area", "50",
                    "--where", "USAQUEN", "--source", "local", "--limit", "1")
    assert result.returncode == 0, result.stderr
    assert "Data as of 2026-07-19" in result.stdout
    assert "2 listings matched; showing 1" in result.stdout
    assert all(text in result.stdout for text in ("2,500,000", "Beds", "Baths", "Area", "Cheaper home", "Usaquén"))
    assert "https://example.com/two" in result.stdout and "https://example.com/one" not in result.stdout
    assert path.read_bytes() == before


def test_empty_results_explain_each_filter_and_default_recency(tmp_path):
    write_export(tmp_path, [
        {"url": "https://example.com/new", "price": "9000000", "location": "Elsewhere"},
        {"url": "https://example.com/old", "last_seen": "2026-01-01T00:00:00+00:00"}])
    result = search(tmp_path, "--max-price", "3000000", "--bedrooms", "2", "--where", "usaquen")
    assert result.returncode == 0, result.stderr
    assert "No listings matched" in result.stdout
    assert "Without --max-price: 0" in result.stdout
    assert "Without --bedrooms: 0" in result.stdout
    assert "Without --where: 0" in result.stdout
    assert "Without --recent: 1" in result.stdout
    widened = search(tmp_path, "--max-price", "3000000", "--where", "usaquen", "--recent", "365")
    assert widened.returncode == 0 and "1 listings matched" in widened.stdout


def test_missing_export_is_one_actionable_line_with_no_traceback(tmp_path):
    result = search(tmp_path)
    assert result.returncode == 1
    assert "scrape export" in result.stderr and "data/listings.csv" in result.stderr
    assert len(result.stderr.splitlines()) == 1 and "Traceback" not in result.stderr
    assert not (tmp_path / "data").exists()


def test_custom_input_warns_if_raw_data_is_newer_and_still_searches(tmp_path):
    path = write_export(tmp_path, [{"url": "https://example.com/one"}])
    custom = tmp_path / "custom.csv"
    path.rename(custom)
    raw = tmp_path / "data" / "local_details_20260720_000000.csv"
    raw.write_text("new raw data")
    os.utime(custom, (1, 1))
    result = search(tmp_path, "--input", str(custom))
    assert result.returncode == 0, result.stderr
    assert "older than" in result.stderr and "scrape export" in result.stderr
    assert "1 listings matched" in result.stdout
    assert raw.read_text() == "new raw data" and not path.exists()


@pytest.mark.parametrize("args", [("--max-price", "nan"), ("--bedrooms", "-1"),
                                  ("--recent", "-1"), ("--limit", "0"),
                                  ("--min-price", "100", "--max-price", "50")])
def test_invalid_filters_fail_before_loading_any_data(tmp_path, args):
    result = search(tmp_path, *args)
    assert result.returncode == 2
    assert "error:" in result.stderr and "Traceback" not in result.stderr


def test_empty_export_is_searchable_and_malformed_export_explains_rebuild(tmp_path):
    path = write_export(tmp_path, [])
    result = search(tmp_path)
    assert result.returncode == 0 and "No listings matched" in result.stdout
    assert "empty export" in result.stdout
    path.write_text("url,title\nhttps://example.com/x,Flat\n")
    result = search(tmp_path)
    assert result.returncode == 1
    assert "columns" in result.stderr and "scrape export" in result.stderr
    assert "Traceback" not in result.stderr
