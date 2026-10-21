import csv

import pytest

from app.models.scraper import ScraperOutput
from app.services.data_saver import print_run_summary, save_data_incremental


def test_summary_reports_saved_rows_and_paths_without_changing_csv(tmp_path, capsys):
    details = tmp_path / "local_details.csv"
    pages = tmp_path / "local_pages.csv"
    for title in ("Flat 1", "Flat 2"):
        save_data_incremental(str(details), ScraperOutput(
            url="https://example.com", information={"title": title, "price": "100"}
        ))
    before = details.read_bytes()

    assert print_run_summary("local", str(pages), str(details), ["title", "price"]) == 0

    output = capsys.readouterr().out
    assert "2 listings saved" in output
    assert str(pages) in output
    assert str(details) in output
    assert "title: 2/2" in output
    assert "price: 2/2" in output
    assert details.read_bytes() == before
    with details.open() as file:
        assert csv.DictReader(file).fieldnames == ["url", "information", "date_time"]


@pytest.mark.parametrize("value", [None, "", "  ", [], [{"price": None}]])
def test_summary_fails_when_a_configured_field_has_no_values(tmp_path, capsys, value):
    details = tmp_path / "details.csv"
    save_data_incremental(str(details), ScraperOutput(
        url="https://example.com", information={"title": "Flat", "price": value}
    ))

    assert print_run_summary("local", "pages.csv", str(details), ["title", "price", "area"]) == 1

    output = capsys.readouterr().out
    assert "title: 1/1" in output
    assert "price: 0/1" in output
    assert "area: 0/1" in output
    assert "empty" in output.lower()
    assert "selector" in output.lower()
    assert details.exists()


def test_summary_counts_partial_and_dynamic_values(tmp_path, capsys):
    details = tmp_path / "details.csv"
    for info in (
        {"price": None, "features": [{"beds": None}], "zero": 0, "flag": False},
        {"price": "100", "features": [{"beds": "2"}], "zero": 0, "flag": False},
    ):
        save_data_incremental(str(details), ScraperOutput(url="https://example.com", information=info))

    assert print_run_summary("local", "pages.csv", str(details), ["price", "features", "zero", "flag"]) == 0

    output = capsys.readouterr().out
    assert "price: 1/2" in output
    assert "features: 1/2" in output
    assert "zero: 2/2" in output
    assert "flag: 2/2" in output


def test_summary_fails_if_no_details_were_saved(tmp_path, capsys):
    details = tmp_path / "missing.csv"

    assert print_run_summary("local", "pages.csv", str(details), ["title"]) == 1

    assert "0 listings saved" in capsys.readouterr().out
    assert not details.exists()


def test_summary_reads_large_information_cells(tmp_path, capsys):
    details = tmp_path / "details.csv"
    save_data_incremental(str(details), ScraperOutput(
        url="https://example.com", information={"description": "a" * 200_000}
    ))
    previous_limit = csv.field_size_limit(131_072)
    try:
        assert print_run_summary("local", "pages.csv", str(details), ["description"]) == 0
    finally:
        csv.field_size_limit(previous_limit)

    assert "description: 1/1" in capsys.readouterr().out
