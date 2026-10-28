import csv

import pytest

from app.services.history import read_history
from app.services.search import prepare_listings

BASE = "https://www.metrocuadrado.com/inmueble/"


def info(price, title="Apartamento En Arriendo, Chico"):
    return repr({"title": title, "price": price})


def snapshot(folder, stamp, rows, source="metrocuadrado-x"):
    folder.mkdir(exist_ok=True)
    path = folder / f"{source}_details_{stamp}.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["url", "information", "date_time"])
        writer.writerows(rows)
    return path


@pytest.fixture
def archive(tmp_path):
    d = tmp_path / "data"
    snapshot(d, "20250101_100000", [
        (BASE + "a/MC1", info("$ 2.000.000"), "2025-01-01 10:00:00+00:00"),
        (BASE + "a/MC1?src=a", info("$ 2.000.000"), "2025-01-01 10:01:00+00:00"),
        ("https://www.metrocuadrado.com//inmueble/b/MC2", info("$ 3.000.000"), "2025-01-01 10:02:00-05:00"),
        (BASE + "c/MC3", info("$ 1.000.000"), "2025-01-01 10:03:00+00:00"),
        (BASE + "d/MC4", info("$ 4.000.000"), "2025-01-01 10:04:00+00:00"),
        (BASE + "g/MC7", info("$ 1.500.000"), "2025-01-01 10:07:00+00:00"),
    ])
    snapshot(d, "20250201_100000", [
        (BASE + "a/MC1", info("$ 2.500.000"), "2025-02-01 10:00:00+00:00"),
        (BASE + "a/MC1", info("$ 999", title="Lo mejor de buscar es encontrar"), "2025-02-01 10:00:30+00:00"),
        (BASE + "b/MC2/", info("$ 2.800.000"), ""),  # trailing slash: separate identity, filename date
        (BASE + "c/MC3", info("$ 1.000.000"), "2025-02-01 10:03:00+00:00"),
        (BASE + "d/MC4", info("$ 3.000.000"), "2025-02-01 10:04:00+00:00"),
        (BASE + "d/MC4?utm=x", info("$ 5.000.000"), "2025-02-01 10:04:30+00:00"),  # same saved run
        (BASE + "f/MC6", info("$ 6.000.000"), "2025-02-01 10:06:00+00:00"),
        (BASE + "g/MC7", info("Precio a convenir"), "2025-02-01 10:07:00+00:00"),
    ])
    snapshot(d, "20250301_100000", [
        (BASE + "a/MC1", info("Precio a convenir"), "2025-03-01 09:00:00+00:00"),  # unknown row before price
        (BASE + "a/MC1", info("$ 2.100.000"), "2025-03-01 10:00:00+00:00"),
        ("https://www.metrocuadrado.com//inmueble/b/MC2", info("150 m²"), "2025-03-01 10:02:00+00:00"),
        (BASE + "c/MC3", info("$ 0"), "2025-03-01 10:03:00+00:00"),
        (BASE + "d/MC4", info("$ 4.000.000"), "2025-03-01 10:04:00+00:00"),
        ("https://www.fincaraiz.com.co/x/5", info("$ 1", title="El portal inmobiliario #1 de Colombia"), "2025-03-01 10:05:00+00:00"),
        (BASE + "f/MC6", info("$ 6.500.000"), "2025-03-01 10:06:00+00:00"),
        (BASE + "g/MC7", info("$ 1.400.000"), "2025-03-01 10:07:00+00:00"),
    ])
    # A second source file with a different price at the same UTC instant as MC6 above.
    snapshot(d, "20250301_100500", [(BASE + "f/MC6", info("$ 6.400.000"), "2025-03-01 05:06:00-05:00")], "metro-copy")
    return d


def test_runs_changes_and_protections(archive):
    history = read_history(archive)
    listings = history["listings"]
    mc1 = listings[BASE + "a/MC1"]
    assert [(r["kind"], r["prices"], r["saved_runs"]) for r in mc1["runs"]] == [
        ("price", [2000000], 1), ("price", [2500000], 1), ("price", [2100000], 1)]
    assert [(c["from"], c["to"], c["across_gap"]) for c in mc1["changes"]] == [
        (2000000, 2500000, False), (2500000, 2100000, False)]
    # Variant rows join, homepage $999 never appears, and the earlier unpriced row in the
    # last run does not move the priced observation's date or evidence.
    assert mc1["runs"][0]["first"] == "2025-01-01T10:00:00+00:00"
    assert mc1["runs"][0]["last_ref"] == "metrocuadrado-x_details_20250101_100000.csv:3"
    assert mc1["runs"][2]["first"] == "2025-03-01T10:00:00+00:00"
    assert mc1["runs"][2]["first_ref"] == "metrocuadrado-x_details_20250301_100000.csv:3"

    mc2 = listings[BASE + "b/MC2"]  # double slash joins; "150 m²" is a gap, not a price
    assert mc2["state"] == "single" and mc2["changes"] == []
    assert mc2["runs"][0]["first"] == "2025-01-01T15:02:00+00:00"
    assert mc2["runs"][1]["kind"] == "unknown"
    assert listings[BASE + "b/MC2/"]["runs"][0]["first"] == "2025-02-01T10:00:00+00:00"

    mc3 = listings[BASE + "c/MC3"]  # equal repeats collapse; zero price is a gap
    assert [(r["kind"], r["saved_runs"]) for r in mc3["runs"]] == [("price", 2), ("unknown", 1)]
    assert mc3["changes"] == [] and mc3["valid_runs"] == 2

    mc4 = listings[BASE + "d/MC4"]  # two prices in one saved run: conflict blocks comparison
    assert [r["kind"] for r in mc4["runs"]] == ["price", "conflict", "price"]
    assert mc4["runs"][1]["prices"] == [3000000, 5000000] and mc4["changes"] == []

    mc6 = listings[BASE + "f/MC6"]  # different files, same UTC instant, different prices
    assert [r["kind"] for r in mc6["runs"]] == ["price", "conflict", "conflict"]
    assert mc6["changes"] == []

    mc7 = listings[BASE + "g/MC7"]  # comparison across a gap says so
    assert mc7["changes"] == [{"from": 1500000, "from_at": "2025-01-01T10:07:00+00:00", "to": 1400000,
                               "to_at": "2025-03-01T10:07:00+00:00", "across_gap": True}]

    assert "https://www.fincaraiz.com.co/x/5" not in listings
    meta = history["meta"]
    assert (meta["files"], meta["homepage_rows"], meta["unknown_price_rows"]) == (4, 2, 4)
    assert meta["conflict_runs"] == 3 and meta["change_events"] == 3 and meta["changed_listings"] == 2


def test_identity_matches_accepted_search(archive):
    urls = ["https://www.metrocuadrado.com//inmueble/b/MC2?x=1#y", BASE + "b/MC2/"]
    rows = [{"url": url, "last_seen": "2025-01-01T00:00:00+00:00"} for url in urls]
    assert {row["url"] for row in prepare_listings(rows)} <= set(read_history(archive)["listings"])


@pytest.mark.parametrize("case, message", [
    ("missing", "does not exist"), ("empty", "No *_details_*.csv"), ("columns", "not a details snapshot"),
    ("dict", "row 2: information is not a saved dictionary"), ("date", "Cannot date rows"),
    ("unreadable", "Cannot read"), ("encoding", "Cannot read"),
])
def test_bad_archives_raise_actionable_errors(tmp_path, case, message):
    d = tmp_path / "data"
    if case != "missing":
        d.mkdir()
    if case == "columns":
        (d / "x_details_20250101_000000.csv").write_text("a,b\n1,2\n")
    elif case == "dict":
        snapshot(d, "20250101_000000", [(BASE + "a", "not a dict", "2025-01-01 00:00:00")])
    elif case == "date":
        (d / "x_details_bad.csv").write_text(f'url,information,date_time\n{BASE}a,"{{}}",\n')
    elif case == "unreadable":
        snapshot(d, "20250101_000000", []).chmod(0)
    elif case == "encoding":
        (d / "x_details_20250101_000000.csv").write_bytes(b"url,information\n\xff\xfe,1\n")
    with pytest.raises(ValueError, match=message.replace("*", r"\*")):
        read_history(d)
