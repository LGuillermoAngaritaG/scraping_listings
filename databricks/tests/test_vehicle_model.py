"""The vehicle price model: same model search as properties, vehicle columns."""

import numpy as np
import pandas as pd

from conftest import vehicle_files
from listings import CSV_OPTIONS
from model_search import search
from rent_model import TARGET, error_bands, metrics, split
from vehicle_model import CATEGORICAL, FEATURES, baseline_predict, good_deals, prepare
from vehicles import VEHICLE_SCHEMA, build_vehicle_listings, dedupe_vehicles_daily, parse_vehicles, valid_vehicle_rows

LINES = {("mazda", "3"): 95e6, ("mazda", "cx-5"): 140e6, ("renault", "duster"): 70e6,
         ("renault", "logan"): 45e6, ("toyota", "hilux"): 180e6, ("chevrolet", "onix"): 60e6}


def synthetic_cars(n=900, seed=0):
    """Price falls with age and with mileage, so a model that sees km can beat the median of the same year."""
    rng = np.random.default_rng(seed)
    keys = list(LINES)
    pick = rng.integers(len(keys), size=n)
    year = rng.integers(2012, 2025, size=n)
    km = np.clip((2026 - year) * 15000 * rng.uniform(0.3, 1.7, size=n), 0, None).round()
    base = np.array([LINES[keys[i]] for i in pick])
    price = base * 0.92 ** (2026 - year) * (1 - km / 600000) * rng.uniform(0.95, 1.05, size=n)
    return prepare(pd.DataFrame({
        "listing_id": [str(i) for i in range(n)], "source": "casatoro", "city": "bogota",
        "brand": [keys[i][0] for i in pick], "line": [keys[i][1] for i in pick],
        "transmission": "automatica", "fuel": "gasolina",
        "body_type": np.where([keys[i][1] in ("cx-5", "duster", "hilux") for i in pick], "suv", "sedan"),
        "year": year, "km": km, "engine_cc": 2000, TARGET: price.round(),
        "first_seen_date": "2026-11-20", "last_seen_date": "2026-11-20",
    }))


def test_search_runs_on_vehicle_columns_and_beats_the_median_of_the_same_line_and_year():
    cars = synthetic_cars()
    train, test, _, kind = split(cars, test_days=30)
    model, report = search(train, test_days=30, trials=2, features=FEATURES, categorical=CATEGORICAL)
    model_mape = metrics(test[TARGET], model.predict(test[FEATURES]))["mape"]
    baseline_mape = metrics(test[TARGET], baseline_predict(train, test))["mape"]
    print(f"\n{kind} split, {report['winner']}: model {model_mape:.3f} baseline {baseline_mape:.3f}")
    assert kind == "random" and model_mape < baseline_mape


def test_baseline_falls_back_from_year_to_line_to_brand():
    train = pd.DataFrame({"brand": ["mazda", "mazda", "kia"], "line": ["3", "3", "rio"],
                          "year": [2019.0, 2020.0, 2018.0], TARGET: [80.0, 100.0, 40.0]})
    test = pd.DataFrame({"brand": ["mazda", "mazda", "mazda", "bmw"], "line": ["3", "3", "cx-5", "x1"],
                         "year": [2019.0, 2015.0, 2019.0, 2019.0]})
    assert baseline_predict(train, test).tolist() == [80.0, 90.0, 90.0, 80.0]


def test_good_deal_needs_year_and_mileage():
    rows = pd.DataFrame({"year": [2019.0, np.nan, 2019.0, 2019.0], "km": [40000.0, 40000.0, np.nan, 40000.0]})
    deals = good_deals(rows, price=[1.0, 1.0, 1.0, 3.0], low=[2.0, 2.0, 2.0, 2.0])
    assert deals.tolist() == [True, False, False, False]


def test_likely_range_is_set_per_body_type():
    test = pd.DataFrame({"body_type": ["suv"] * 60 + ["sedan"] * 60, TARGET: [100.0] * 120})
    prediction = np.r_[np.linspace(80, 125, 60), np.full(60, 100.0)]
    bands, default = error_bands(test, prediction, group="body_type")
    assert set(bands) == {"suv", "sedan"} and bands["sedan"] == (1.0, 1.0) and bands["suv"][0] < 1 < bands["suv"][1]


def test_model_beats_the_median_of_the_same_line_and_year_on_real_cars(spark):
    """The real first scrape: one day, so the split is a random 1 in 5."""
    raw = spark.read.options(**CSV_OPTIONS).schema(VEHICLE_SCHEMA).csv(vehicle_files())
    silver = dedupe_vehicles_daily(parse_vehicles(raw.withColumn("source_file", raw["source"])))
    cars = prepare(build_vehicle_listings(silver.filter(valid_vehicle_rows())).toPandas())
    train, test, _, kind = split(cars, test_days=30)
    model, report = search(train, test_days=30, trials=5, features=FEATURES, categorical=CATEGORICAL)
    model_mape = metrics(test[TARGET], model.predict(test[FEATURES]))["mape"]
    baseline_mape = metrics(test[TARGET], baseline_predict(train, test))["mape"]
    print(f"\n{len(train)} train {len(test)} test, {report['winner']} {report['valid_mape']}")
    print(f"model {model_mape:.3f} baseline {baseline_mape:.3f}")
    assert model_mape < baseline_mape
