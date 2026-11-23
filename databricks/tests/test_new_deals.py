"""The labels in the new-deals emails and the alert queries that pick the new deals."""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import property_model
import vehicle_model
import yaml

ALERT = Path(__file__).resolve().parents[1] / "resources" / "alerts.yml"


def test_property_label_skips_what_is_unknown():
    listings = pd.DataFrame({
        "property_type": ["apartamento", "casa"],
        "neighborhood": ["chico norte", "unknown"],
        "area_m2": [85.0, np.nan],
        "bedrooms": [2.0, 1.0],
    })
    assert property_model.deal_label(listings).tolist() == [
        "Apartamento, Chico Norte, 85 m2, 2 bedrooms",
        "Casa, 1 bedroom",
    ]


def test_car_label_has_year_and_mileage():
    listings = pd.DataFrame({"brand": ["kia"], "line": ["sportage"], "year": [2020.0], "km": [50000.0]})
    assert vehicle_model.deal_label(listings).tolist() == ["Kia Sportage 2020, 50,000 km"]


def test_labels_of_no_deals_are_empty():
    empty = pd.DataFrame(columns=["brand", "line", "year", "km"])
    assert vehicle_model.deal_label(empty).empty


TODAY = datetime.now(ZoneInfo("America/Bogota")).date()  # the alerts compare with Bogota's date
YESTERDAY = TODAY - timedelta(days=1)


def deal(listings, listing_id, day, gap=-0.2, source="fincaraiz", kind="apartamento"):
    price = 350e6 if listings == "sale" else 80e6
    expected = price / (1 + gap)
    return (listings, source, listing_id, day, kind, f"listing {listing_id}", "bogota",
            price, expected, expected * 0.9, gap, f"https://example.com/{listing_id}")


def run_alert(spark, alert, rows):
    columns = ["listings", "source", "listing_id", "scored_date", "listing_type", "label", "city",
               "last_price_cop", "predicted_price_cop", "predicted_low_cop", "price_gap_pct", "url"]
    table = "sale" if alert == "new_property_deals" else "vehicle"
    spark.createDataFrame(rows or [deal(table, "none", YESTERDAY)], columns) \
        .filter("listing_id != 'none'").createOrReplaceTempView(f"{table}_deal_history")
    query = yaml.safe_load(ALERT.read_text())["resources"]["alerts"][alert]["query_text"]
    return spark.sql(query.replace("${var.catalog}.${var.schema}.", "")).collect()


def test_property_email_has_houses_and_apartments_only(spark):
    rows = [deal("sale", "a", TODAY, kind="apartamento"), deal("sale", "c", TODAY, kind="casa"),
            deal("sale", "e", TODAY, kind="apartaestudio"), deal("sale", "o", TODAY, kind="oficina"),
            deal("sale", "f", TODAY, kind="finca")]
    result = run_alert(spark, "new_property_deals", rows)  # first day: every deal is new
    assert sorted(r["title"] for r in result) == ["listing a", "listing c", "listing e"]
    assert all(r["deals_today"] == 3 for r in result)


def test_email_text_says_why_it_is_a_deal(spark):
    [sale] = run_alert(spark, "new_property_deals", [deal("sale", "s", TODAY, gap=-0.2)])
    assert sale["details"] == "For sale in Bogota on Fincaraiz"
    assert sale["why"] == ("Asks 350 M COP, 20% below the 438 M COP the model expects for a property of this "
                           "type, size, barrio and estrato. Its likely range starts at 394 M COP, and this price "
                           "is below it.")
    assert sale["link"] == "https://example.com/s"
    [car] = run_alert(spark, "new_car_deals", [deal("vehicle", "v", TODAY, gap=-0.1, source="casatoro")])
    assert car["details"] == "Used car in Bogota at Casatoro"
    assert car["why"] == ("Asks 80.0 M COP, 10% below the 88.9 M COP the model expects for a car of this "
                          "brand, line, year and mileage. Its likely range starts at 80.0 M COP, and this price "
                          "is below it.")


def test_only_deals_missing_the_day_before_are_new(spark):
    rows = [
        deal("vehicle", "kept", YESTERDAY), deal("vehicle", "kept", TODAY),          # still a deal: not new
        deal("vehicle", "gone", YESTERDAY),                                          # no longer a deal
        deal("vehicle", "fresh", TODAY, gap=-0.15),                                  # new
        deal("vehicle", "kept", TODAY, gap=-0.25, source="autoexpo"),                # same id, other dealer: new
        deal("vehicle", "old", TODAY - timedelta(days=3)), deal("vehicle", "old", TODAY, gap=-0.3),  # new
    ]
    result = run_alert(spark, "new_car_deals", rows)  # biggest discount first
    assert [(r["title"], r["details"]) for r in result] == [
        ("listing old", "Used car in Bogota at Fincaraiz"),
        ("listing kept", "Used car in Bogota at Autoexpo"),
        ("listing fresh", "Used car in Bogota at Fincaraiz"),
    ]


def test_a_day_with_new_deals_already_sent_or_no_scoring_sends_nothing(spark):
    assert run_alert(spark, "new_car_deals", [deal("vehicle", "a", YESTERDAY), deal("vehicle", "a", TODAY)]) == []
    assert run_alert(spark, "new_car_deals", [deal("vehicle", "a", YESTERDAY)]) == []  # not scored today


def test_a_discount_over_half_gets_a_data_error_warning(spark):
    rows = [deal("sale", "typo", TODAY, gap=-0.84), deal("sale", "fine", TODAY, gap=-0.3)]
    typo, fine = run_alert(spark, "new_property_deals", rows)
    assert typo["why"].endswith(" Warning: a price this far below the model often comes from a data error, "
                                "such as a wrong area. Check the listing.")
    assert fine["why"].endswith("and this price is below it.")
