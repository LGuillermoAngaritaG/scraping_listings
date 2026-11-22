import pandas as pd
import pytest
from pyspark.sql import functions as F

from conftest import detail_files, read_bronze
from listings import build_rent_listings, dedupe_daily, parse_listings, valid_rows

GOOD_DAY = "20251120"  # both scrapers worked
BROKEN_DAY = "20260615"  # Fincaraiz saved 404 pages, Metrocuadrado had no area


@pytest.fixture(scope="module")
def bronze(spark):
    return read_bronze(spark, detail_files(GOOD_DAY, BROKEN_DAY)).cache()


@pytest.fixture(scope="module")
def parsed(bronze):
    return parse_listings(bronze).cache()


def test_bronze_keeps_every_csv_row(bronze):
    files = detail_files(GOOD_DAY, BROKEN_DAY)
    expected = sum(len(pd.read_csv(f)) for f in files)
    assert bronze.count() == expected
    assert {r.source for r in bronze.select("source").distinct().collect()} == {
        "fincaraiz",
        "metrocuadrado",
    }


def share(rows, condition) -> float:
    return rows.filter(condition).count() / rows.count()


def test_good_day_fields_are_parsed(parsed):
    day = parsed.filter(F.col("scrape_date") == "2025-11-20")
    for source in ["fincaraiz", "metrocuadrado"]:
        rows = day.filter(F.col("source") == source)
        assert rows.count() > 0
        assert share(rows, valid_rows()) > 0.95, source
        assert share(rows, F.col("area_m2").isNotNull()) > 0.85, source
        assert share(rows, F.col("neighborhood").isNotNull()) > 0.95, source
        assert share(rows, F.col("property_type").isNotNull()) > 0.95, source
        assert share(rows, F.col("bathrooms").isNotNull()) > 0.8, source
        assert share(rows, F.col("estrato").isNotNull()) > 0.6, source


def test_broken_fincaraiz_rows_are_invalid(parsed):
    broken = parsed.filter((F.col("source") == "fincaraiz") & (F.col("scrape_date") == "2026-06-15"))
    assert broken.count() > 0
    assert broken.filter(valid_rows()).count() == 0


def test_silver_has_one_row_per_property_and_day(parsed):
    silver = dedupe_daily(parsed).filter(valid_rows())
    keys = ["source", "property_id", "scrape_date"]
    assert silver.count() == silver.select(*keys).distinct().count()


def test_layer_counts_reconcile(parsed):
    """bronze rows = silver rows + same-day duplicates + days with no valid row."""
    deduped = dedupe_daily(parsed)
    silver = deduped.filter(valid_rows()).count()
    duplicates = parsed.count() - deduped.count()
    dropped = deduped.filter(~valid_rows()).count()
    assert parsed.count() == silver + duplicates + dropped
    assert silver > 0 and duplicates > 0


def test_gold_has_one_row_per_listing(parsed):
    gold = build_rent_listings(dedupe_daily(parsed).filter(valid_rows()))
    assert gold.count() == gold.select("source", "property_id").distinct().count()
    row = gold.filter(F.col("area_m2").isNotNull()).first()
    assert row.price_per_m2 == round(row.price_cop / row.area_m2)


def test_area_number_formats(spark):
    from listings import _area

    cases = [
        ("4.521 m2", 4521.0),
        ("60,5 m2", 60.5),
        ("1.234,5 m2", 1234.5),
        ("999.99  m2", 999.99),
        ("Área Construida 445.9m²", 445.9),
        ("54 |   | m²", 54.0),
        (None, None),
    ]
    df = spark.createDataFrame(cases, "text STRING, expected DOUBLE")
    assert [r.area for r in df.select(_area(F.col("text")).alias("area")).collect()] == [
        expected for _, expected in cases
    ]
