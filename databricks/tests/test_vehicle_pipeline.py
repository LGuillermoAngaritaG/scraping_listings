"""The vehicle tables: typed silver rows, one row per car and day, one gold row per car."""

import pandas as pd
import pytest

from conftest import vehicle_files
from listings import CSV_OPTIONS
from vehicles import VEHICLE_SCHEMA, build_vehicle_listings, dedupe_vehicles_daily, parse_vehicles, valid_vehicle_rows

COLUMNS = [c.split()[0] for c in VEHICLE_SCHEMA.split(", ")]


def car(listing_id, scraped_at, price="85000000", **fields):
    row = dict.fromkeys(COLUMNS, "")
    row.update(source="casatoro", listing_id=listing_id, url=f"https://example.test/{listing_id}",
               title="Mazda 3 Grand Touring", brand="Mazda", line="3", year="2019", km="45000",
               price_cop=price, transmission="Automatica", fuel="Gasolina", body_type="Sedan",
               engine_cc="2000", city="Bogota", scraped_at=scraped_at)
    row.update(fields)
    return row


@pytest.fixture(scope="module")
def parsed(spark, tmp_path_factory):
    rows = [
        car("a1", "2026-11-20T08:00:00", price="90000000"),
        car("a1", "2026-11-20T18:00:00", price="88000000"),  # same day, later: wins
        car("a1", "2026-11-21T08:00:00", price="85000000", km=""),  # km missing on day 2
        car("b2", "2026-11-20T08:00:00", price="1500000"),  # price too low: dropped
        car("c3", "2026-11-20T08:00:00", year="2021.0", brand="  RENAULT ", line="Duster",
            title='Renault "Duster"\nIntens', city="Bogotá"),
        car("d4", "2026-11-20T08:00:00", year="1975"),  # year too old: dropped
    ]
    path = tmp_path_factory.mktemp("vehicles") / "vehicles-casatoro_20261120_080000.csv"
    pd.DataFrame(rows, columns=COLUMNS).to_csv(path, index=False)
    raw = spark.read.options(**CSV_OPTIONS).schema(VEHICLE_SCHEMA).csv(str(path))
    return parse_vehicles(raw.withColumn("source_file", raw["source"]))


def test_fields_are_typed_and_cleaned(parsed):
    c3 = parsed.filter("listing_id = 'c3'").first()
    assert (c3.brand, c3.line, c3.city, c3.year, c3.km, c3.engine_cc) == ("renault", "duster", "bogota", 2021, 45000, 2000)
    assert c3.title == 'Renault "Duster"\nIntens'  # quotes and line breaks survive the CSV


def test_invalid_rows_are_flagged(parsed):
    valid = {r.listing_id for r in parsed.filter(valid_vehicle_rows()).select("listing_id").distinct().collect()}
    assert valid == {"a1", "c3"}


def test_silver_keeps_the_latest_row_of_each_car_and_day(parsed):
    silver = dedupe_vehicles_daily(parsed).filter(valid_vehicle_rows())
    a1 = silver.filter("listing_id = 'a1'").orderBy("scrape_date").collect()
    assert [r.price_cop for r in a1] == [88000000, 85000000]


def test_gold_has_one_row_per_car_with_first_and_last_price(parsed):
    gold = build_vehicle_listings(dedupe_vehicles_daily(parsed).filter(valid_vehicle_rows()))
    a1 = gold.filter("listing_id = 'a1'").first()
    assert gold.count() == 2
    assert (a1.price_cop, a1.last_price_cop, a1.days_seen) == (88000000, 85000000, 2)
    assert a1.km == 45000  # latest known value when the last day has none


@pytest.fixture(scope="module")
def real(spark):
    raw = spark.read.options(**CSV_OPTIONS).schema(VEHICLE_SCHEMA).csv(vehicle_files())
    return raw, parse_vehicles(raw.withColumn("source_file", raw["source"]))


def test_real_files_reconcile_from_csv_to_gold(real):
    """Every scraped car reaches bronze; almost all pass the rules; gold has one row per car."""
    raw, parsed = real
    rows = sum(len(pd.read_csv(path, dtype=str)) for path in vehicle_files())
    silver = dedupe_vehicles_daily(parsed).filter(valid_vehicle_rows())
    gold = build_vehicle_listings(silver)
    print(f"\ncsv {rows} bronze {raw.count()} silver {silver.count()} gold {gold.count()}")
    assert raw.count() == rows
    assert parsed.filter("scraped_at IS NULL OR price_cop IS NULL OR year IS NULL").count() == 0
    assert silver.count() >= 0.97 * rows
    assert gold.count() == gold.select("source", "listing_id").distinct().count() == silver.count()
