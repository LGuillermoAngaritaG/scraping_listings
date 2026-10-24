import csv
import json
import os
import subprocess
import sys

import pytest

from app.services.browse import catalogue_data, render_page
from app.services.export import EXPORT_COLUMNS
from app.services.search import load_listings

HOSTILE = '</script><script>window.pwned=1</script> "quote" & Ñandú'


def write_export(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    defaults = {"source": "local", "title": "Apartamento", "location": "Usaquén",
                "price": "2000000", "bedrooms": "2", "bathrooms": "1", "area_m2": "50",
                "last_seen": "2026-07-19T00:00:00+00:00"}
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows({**defaults, **row} for row in rows)
    return path


def embedded(page):
    start = page.index('<script type="application/json" id="data">') + len('<script type="application/json" id="data">')
    return json.loads(page[start:page.index("</script>", start)])


def browse(cwd, *args):
    return subprocess.run([sys.executable, "-m", "app.main", "browse", *args],
                          cwd=cwd, text=True, capture_output=True)


def test_payload_uses_canonical_rows_and_keeps_unknown_numbers(tmp_path):
    path = write_export(tmp_path / "listings.csv", [
        {"url": "https://example.com//a?src=old", "last_seen": "2026-07-01T00:00:00+00:00"},
        {"url": "https://example.com/a?src=new", "price": "", "area_m2": ""},
        {"url": "https://example.com/b", "last_seen": "2026-06-30T23:30:00-05:00"}])
    data = catalogue_data(load_listings(path))
    assert [row["url"] for row in data["rows"]] == ["https://example.com/a", "https://example.com/b"]
    assert data["rows"][0]["price"] is None and data["rows"][0]["area_m2"] is None
    assert data["rows"][1]["last_seen"] == "2026-07-01"  # UTC calendar date, as search uses
    assert data["meta"]["data_date"] == "2026-07-19" and data["meta"]["properties"] == 2


def test_page_embeds_untrusted_text_as_inert_json(tmp_path):
    path = write_export(tmp_path / "listings.csv", [
        {"url": "javascript:alert(1)", "title": HOSTILE, "location": "<img src=x onerror=alert(2)>"}])
    page = render_page(catalogue_data(load_listings(path)))
    assert "window.pwned" in page and "</script><script>window.pwned" not in page
    assert "<img src=x" not in page
    assert embedded(page)["rows"][0]["title"] == HOSTILE
    assert "http://" not in page.replace("http://www.w3.org", "") and "https://" not in page


def test_cli_replaces_output_atomically_without_touching_input(tmp_path):
    path = write_export(tmp_path / "data" / "listings.csv", [{"url": "https://example.com/a"}])
    before = path.read_bytes()
    output = tmp_path / "data" / "browse" / "listings.html"
    output.parent.mkdir()
    output.write_text("old page")
    result = browse(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "data/browse/listings.html: 1 properties, data as of 2026-07-19" in result.stdout
    assert embedded(output.read_text(encoding="utf-8"))["meta"]["properties"] == 1
    assert path.read_bytes() == before
    assert sorted(p.name for p in output.parent.iterdir()) == ["listings.html"]


def test_cli_header_only_export_reports_an_empty_page(tmp_path):
    write_export(tmp_path / "data" / "listings.csv", [])
    result = browse(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "no listings" in result.stdout and "nan" not in result.stdout.lower()
    page = (tmp_path / "data" / "browse" / "listings.html").read_text(encoding="utf-8")
    assert embedded(page)["meta"]["data_date"] is None


@pytest.mark.parametrize("case", ["missing", "malformed", "unwritable", "csv-output"])
def test_cli_failures_are_actionable_without_traceback(tmp_path, case):
    path = write_export(tmp_path / "data" / "listings.csv", [{"url": "https://example.com/a"}])
    args, expected = [], {"missing": "Run 'scrape export' first", "malformed": "Run 'scrape export' to rebuild",
                          "unwritable": "Choose a writable location with --output",
                          "csv-output": "--output must end in .html"}[case]
    if case == "missing":
        path.unlink()
    elif case == "malformed":
        path.write_text("not,an,export\n1,2,3\n")
    elif case == "unwritable":
        locked = tmp_path / "locked"
        locked.mkdir(mode=0o500)
        args = ["--output", str(locked / "page.html")]
    else:
        args = ["--output", str(path)]
    before = path.read_bytes() if path.exists() else None
    result = browse(tmp_path, *args)
    if case == "unwritable":
        os.chmod(tmp_path / "locked", 0o700)
    assert result.returncode != 0
    assert expected in result.stderr and "Traceback" not in result.stderr
    assert (path.read_bytes() if path.exists() else None) == before


def test_history_option_embeds_page_homes_only_and_reports_mismatches(tmp_path):
    from tests.test_history import BASE, info, snapshot
    data = tmp_path / "data"
    snapshot(data, "20250101_100000", [(BASE + "a", info("$ 2.000.000"), "2025-01-01 10:00:00+00:00"),
                                       (BASE + "other", info("$ 9.000.000"), "2025-01-01 10:01:00+00:00")])
    snapshot(data, "20250201_100000", [(BASE + "a", info("$ 2.500.000"), "2025-02-01 10:00:00+00:00")])
    write_export(data / "listings.csv", [
        {"url": BASE + "a?src=x", "price": "2400000", "last_seen": "2025-02-01T10:00:00+00:00"},
        {"url": BASE + "new", "last_seen": "2025-03-01T00:00:00+00:00"}])
    result = browse(tmp_path, "--history-data", "data")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "this can take a minute" in out and "2 snapshot files, 2025-01-01 to 2025-02-01" in out
    assert "1 homes show a page price that differs" in out and "1 homes were last seen after" in out
    assert "before the oldest snapshot" not in out
    page = embedded((data / "browse" / "listings.html").read_text(encoding="utf-8"))
    assert sorted(page["history"]) == [BASE + "a", BASE + "new"]  # the unrelated archive listing is not embedded
    assert page["history"][BASE + "a"]["notes"] == ["price_differs"]
    assert page["history"][BASE + "new"] == {"state": "absent", "runs": [], "changes": [], "notes": ["after_archive"]}
    assert page["rows"][0]["price"] == 2400000  # the card price is never replaced by history
    assert page["meta"]["history"]["change_events"] == 1 and page["meta"]["history"]["changed_listings"] == 1


@pytest.mark.parametrize("case", ["missing", "malformed"])
def test_history_failure_keeps_previous_page(tmp_path, case):
    from tests.test_history import BASE, snapshot
    write_export(tmp_path / "data" / "listings.csv", [{"url": "https://example.com/a"}])
    if case == "malformed":
        snapshot(tmp_path / "data", "20250101_100000", [(BASE + "a", "[1, 2]", "2025-01-01 10:00:00")])
    output = tmp_path / "data" / "browse" / "listings.html"
    output.parent.mkdir()
    output.write_text("old page")
    result = browse(tmp_path, "--history-data", "data" if case == "malformed" else "nowhere")
    assert result.returncode == 1 and "Traceback" not in result.stderr
    assert ("information is not a saved dictionary" if case == "malformed" else "does not exist") in result.stderr
    assert "is unchanged" in result.stderr and "Leave out --history-data" in result.stderr
    assert output.read_text() == "old page" and sorted(p.name for p in output.parent.iterdir()) == ["listings.html"]


def test_default_browse_has_no_history_payload(tmp_path):
    write_export(tmp_path / "data" / "listings.csv", [{"url": "https://example.com/a"}])
    result = browse(tmp_path)
    page = embedded((tmp_path / "data" / "browse" / "listings.html").read_text(encoding="utf-8"))
    assert result.returncode == 0 and "history" not in page and "history" not in page["meta"]
    assert "asking prices" not in result.stdout


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def raw_browse(cwd, *args):
    """Raw-snapshot mode as a newcomer runs it: no site packages, so no scraper dependencies."""
    env = {**os.environ, "PYTHONPATH": ROOT, "TMPDIR": str(cwd / "tmp")}
    (cwd / "tmp").mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-S", "-B", "-m", "app.main", "browse", *args],
                          cwd=cwd, text=True, capture_output=True, env=env)


@pytest.fixture
def snapshots(tmp_path):
    from tests.test_history import BASE, info, snapshot
    data = tmp_path / "data"
    snapshot(data, "20250101_100000", [(BASE + "a", info("$ 2.000.000"), "2025-01-01 10:00:00+00:00")])
    snapshot(data, "20250201_100000", [
        (BASE + "a", info("$ 2.500.000"), "2025-02-01 10:00:00+00:00"),
        (BASE + "b", info("$ 1"), "2025-02-01 10:00:00+00:00"),
        ("https://fr.example/x", info("$ 1", "Lo mejor de buscar es encontrar"), "2025-02-01 10:00:00+00:00")])
    write_export(data / "listings.csv", [{"url": "https://example.com/unrelated"}])
    output = data / "browse" / "listings.html"
    output.parent.mkdir()
    output.write_text("old page")
    return data


def tree_bytes(folder):
    return {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}


def test_from_data_builds_page_from_snapshots_without_site_packages(tmp_path, snapshots):
    before = tree_bytes(snapshots)
    result = raw_browse(tmp_path, "--from-data", "data")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.index("Reading the saved snapshots in data/") < out.index("Read 2 snapshot files")
    assert "skipped 1 homepage rows" in out
    assert "data/browse/listings.html: 2 properties, data as of 2025-02-01" in out and "Open it in a web browser" in out
    page = embedded((snapshots / "browse" / "listings.html").read_text(encoding="utf-8"))
    assert [(row["url"].rsplit("/", 1)[1], row["price"]) for row in page["rows"]] == [("a", 2500000), ("b", 1)]
    assert "history" not in page and "asking prices" not in out  # history stays opt-in
    assert tree_bytes(snapshots) == before  # raw snapshots and the unrelated export are untouched
    assert sorted(p.name for p in (snapshots / "browse").iterdir()) == ["listings.html"]
    assert list((tmp_path / "tmp").iterdir()) == []  # the throwaway export is gone


def test_from_data_composes_with_explicit_history(tmp_path, snapshots):
    result = raw_browse(tmp_path, "--from-data", "data", "--history-data", "data")
    assert result.returncode == 0, result.stderr
    page = embedded((snapshots / "browse" / "listings.html").read_text(encoding="utf-8"))
    assert page["meta"]["history"]["change_events"] == 1 and page["history"][page["rows"][0]["url"]]["changes"]


def test_from_data_rejects_input_before_reading(tmp_path, snapshots):
    result = raw_browse(tmp_path, "--from-data", "data", "--input", "data/listings.csv")
    assert result.returncode == 2 and "not allowed with argument" in result.stderr
    assert (snapshots / "browse" / "listings.html").read_text() == "old page"


@pytest.mark.parametrize("case", ["missing", "empty", "columns", "malformed", "encoding", "unreadable", "unwritable"])
def test_from_data_failures_keep_previous_page(tmp_path, snapshots, case):
    folder, args = "data", []
    first = snapshots / "metrocuadrado-x_details_20250101_100000.csv"
    expected = {"missing": "No folder nowhere. Run this from the scraping_listings checkout",
                "empty": "No saved snapshots (*_details_*.csv files) in empty/",
                "columns": "expected url and information columns",
                "malformed": "Invalid information in data/metrocuadrado-x_details_20250101_100000.csv, row 2",
                "encoding": f"Cannot read data/{first.name}: it is not UTF-8 text. Restore the saved copy",
                "unreadable": f"Cannot read data/{first.name}: Permission denied",
                "unwritable": "Choose a writable location with --output"}[case]
    if case == "missing":
        folder = "nowhere"
    elif case == "empty":
        (tmp_path / "empty").mkdir()
        folder = "empty"
    elif case == "columns":
        first.write_text("a,b\n1,2\n")
    elif case == "malformed":
        first.write_text("url,information,date_time\nhttps://x,{broken,2025-01-01 10:00:00\n")
    elif case == "encoding":
        first.write_bytes(b"url,information,date_time\nhttps://x,\xff,2025-01-01 10:00:00\n")
    elif case == "unreadable":
        first.chmod(0o000)
    else:
        locked = tmp_path / "locked"
        locked.mkdir(mode=0o500)
        args = ["--output", str(locked / "page.html")]
    before = tree_bytes(snapshots) if case != "unreadable" else None
    result = raw_browse(tmp_path, "--from-data", folder, *args)
    first.chmod(0o644)
    (tmp_path / "locked").exists() and (tmp_path / "locked").chmod(0o700)
    assert result.returncode == 1 and "Traceback" not in result.stderr
    assert expected in result.stderr and "scrape --list" not in result.stderr
    if case not in ("missing", "empty", "unwritable"):
        assert "data/browse/listings.html is unchanged" in result.stderr
    assert (snapshots / "browse" / "listings.html").read_text() == "old page"
    assert sorted(p.name for p in (snapshots / "browse").iterdir()) == ["listings.html"]
    assert list((tmp_path / "tmp").iterdir()) == []
    if before is not None:
        assert tree_bytes(snapshots) == before
