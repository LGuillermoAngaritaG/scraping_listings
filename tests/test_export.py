import ast
import csv
import subprocess
import sys

import pytest

from app.services.export import export_listings


def write_details(data, timestamp, rows, source="local"):
    data.mkdir(exist_ok=True)
    path = data / f"{source}_details_{timestamp}.csv"
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["url", "information", "date_time"])
        writer.writeheader()
        for url, info, seen in rows:
            writer.writerow({"url": url, "information": repr(info), "date_time": seen})
    return path


def read_export(path):
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def test_latest_valid_values_and_all_sightings_survive_junk_fallback(tmp_path):
    data, output = tmp_path / "data", tmp_path / "listings.csv"
    url = "https://example.com/flat"
    old = write_details(data, "20260101_000000", [
        (url, {"title": "Old", "pricing": "$ 1.000.000"}, "2026-01-01T00:00:00Z")])
    write_details(data, "20260201_000000", [
        (url, {"title": "Latest valid", "pricing": "$ 2.000.000", "bathrooms": "2 Baños"},
         "2026-02-01T00:00:00+00:00")])
    write_details(data, "20260301_000000", [
        (url, {"title": "Lo mejor de buscar es encontrar"}, "2026-03-01T00:00:00Z"),
        ("https://example.com/junk", {"title": "El portal inmobiliario #1 de Colombia"}, "")])
    original = old.read_bytes()

    stats = export_listings(output, data)

    assert read_export(output) == [{
        "source": "local", "url": url, "title": "Latest valid", "location": "",
        "price": "2000000", "bedrooms": "", "bathrooms": "2", "area_m2": "",
        "estrato": "", "first_seen": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-03-01T00:00:00+00:00"}]
    assert stats["rows"] == 1 and stats["dropped_homepages"] == 2
    assert stats["sources"] == {"local": 1}
    assert old.read_bytes() == original


def test_duplicate_snapshots_decode_only_the_value_supplier(tmp_path, monkeypatch):
    data, output = tmp_path / "data", tmp_path / "listings.csv"
    write_details(data, "20260101_000000", [
        ("https://example.com/flat", {"title": "Flat"}, "2026-01-01T00:00:00Z")
    ] * 100)
    original = ast.literal_eval
    calls = []

    def decode(raw):
        calls.append(raw)
        return original(raw)

    monkeypatch.setattr(ast, "literal_eval", decode)
    export_listings(output, data)
    assert len(calls) == 1
    assert len(read_export(output)) == 1


def test_row_timestamps_choose_latest_values_even_when_file_order_differs(tmp_path):
    data, output = tmp_path / "data", tmp_path / "listings.csv"
    url = "https://example.com/flat"
    write_details(data, "20260201_000000", [
        (url, {"title": "Older"}, "2026-01-01T00:00:00Z")])
    write_details(data, "20260101_000000", [
        (url, {"title": "Newest"}, "2026-03-01T00:00:00+01:00"),
        (url, {"title": "Middle"}, "2026-02-01T00:00:00Z")])
    export_listings(output, data)
    row = read_export(output)[0]
    assert row["title"] == "Newest"
    assert row["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert row["last_seen"] == "2026-02-28T23:00:00+00:00"


def test_export_handles_both_sources_and_filename_date_fallback(tmp_path):
    data, output = tmp_path / "data", tmp_path / "listings.csv"
    write_details(data, "20260101_123456", [
        ("https://example.com/one", {"title": "Flat", "area": "148 | | m²"}, "")], "fincaraiz")
    write_details(data, "20260102_000000", [
        ("https://example.com/two", {"title": "Shop", "area": "Área Construida 4.71m²",
          "estrato": "Estrato Comercial", "main_specs": "1 hab. | 2 bañ. | 3 par."}, "invalid")], "metrocuadrado")
    stats = export_listings(output, data)
    one, two = read_export(output)
    assert one["first_seen"] == "2026-01-01T12:34:56+00:00"
    assert two["area_m2"] == "4.71" and two["estrato"] == "" and two["bathrooms"] == "2"
    assert stats["sources"] == {"fincaraiz": 1, "metrocuadrado": 1}
    assert stats["filled"]["area_m2"] == 2


def test_listing_description_can_mention_a_homepage_title(tmp_path):
    data, output = tmp_path / "data", tmp_path / "listings.csv"
    write_details(data, "20260101_000000", [
        ("https://example.com/flat", {"title": "Flat", "description": "Lo mejor de buscar es encontrar"}, "")])
    export_listings(output, data)
    assert read_export(output)[0]["title"] == "Flat"


def test_invalid_information_explains_file_and_preserves_previous_output(tmp_path):
    data, output = tmp_path / "data", tmp_path / "listings.csv"
    path = write_details(data, "20260101_000000", [("https://example.com/flat", [], "")])
    output.write_text("previous export")
    with pytest.raises(ValueError, match=path.name):
        export_listings(output, data)
    assert output.read_text() == "previous export"


def test_output_cannot_overwrite_an_input_even_through_a_symlink(tmp_path):
    data = tmp_path / "data"
    path = write_details(data, "20260101_000000", [("https://example.com/flat", {"title": "Flat"}, "")])
    alias = tmp_path / "alias.csv"
    alias.symlink_to(path)
    before = path.read_bytes()
    for output in (path, alias):
        with pytest.raises(ValueError, match="raw"):
            export_listings(output, data)
    assert path.read_bytes() == before


@pytest.mark.parametrize("custom_output", [False, True])
def test_cli_export_needs_no_config_and_reports_output_and_health(tmp_path, custom_output):
    write_details(tmp_path / "data", "20260101_000000", [
        ("https://example.com/flat", {"title": "Flat", "price": "$ 2.000.000"}, "")])
    output = tmp_path / ("out" if custom_output else "data") / "listings.csv"
    args = ["--output", str(output)] if custom_output else []
    result = subprocess.run([sys.executable, "-m", "app.main", "export", *args],
                            cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "1 listings" in result.stdout and "local: 1" in result.stdout
    assert "price: 1/1" in result.stdout and "100.0%" in result.stdout
    assert "0 homepage" in result.stdout
    assert (str(output) if custom_output else "data/listings.csv") in result.stdout
    assert read_export(output)[0]["price"] == "2000000"


def test_cli_export_without_input_gives_a_next_step(tmp_path):
    result = subprocess.run([sys.executable, "-m", "app.main", "export"], cwd=tmp_path,
                            text=True, capture_output=True)
    assert result.returncode == 1
    assert "details" in result.stderr and "scrape --list" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "data").exists()


def test_only_homepage_rows_produce_an_empty_table_with_a_clear_summary(tmp_path):
    data, output = tmp_path / "data", tmp_path / "listings.csv"
    write_details(data, "20260101_000000", [
        ("https://example.com/junk", {"title": "Lo mejor de buscar es encontrar"}, "")])
    stats = export_listings(output, data)
    assert read_export(output) == []
    assert stats["rows"] == 0 and stats["dropped_homepages"] == 1


def test_missing_csv_columns_and_broken_dicts_leave_no_output(tmp_path):
    data, output = tmp_path / "data", tmp_path / "listings.csv"
    path = write_details(data, "20260101_000000", [("https://example.com/flat", {"title": "Flat"}, "")])
    path.write_text("url,information,date_time\nhttps://example.com/flat,{broken,\n")
    with pytest.raises(ValueError, match="row 2"):
        export_listings(output, data)
    path.write_text("url,title\nhttps://example.com/flat,Flat\n")
    with pytest.raises(ValueError, match="columns"):
        export_listings(output, data)
    assert not output.exists()
