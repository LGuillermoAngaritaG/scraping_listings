import numpy as np
import pandas as pd
import pytest

from conftest import one_file_per_day, read_bronze
from listings import build_rent_listings, dedupe_daily, parse_listings, valid_rows
from rent_model import (
    FEATURES,
    TARGET,
    baseline_predict,
    build_model,
    error_bands,
    good_deals,
    metrics,
    metrics_by,
    prepare,
    run_backtest,
    temporal_split,
)


@pytest.fixture(scope="module")
def listings(spark):
    bronze = read_bronze(spark, one_file_per_day())
    silver = dedupe_daily(parse_listings(bronze)).filter(valid_rows())
    return prepare(build_rent_listings(silver).toPandas())


def test_model_beats_median_by_zone(listings):
    train, test, cutoff = temporal_split(listings, test_days=30)
    prediction = build_model().fit(train[FEATURES], train[TARGET]).predict(test[FEATURES])
    model = metrics(test[TARGET], prediction)
    baseline = metrics(test[TARGET], baseline_predict(train, test))
    print(f"\ncutoff {cutoff.date()} train {len(train)} test {len(test)}")
    print(f"model    {model}\nbaseline {baseline}")
    print(metrics_by(test, prediction, "property_type").to_string(index=False))
    assert model["mape"] < baseline["mape"]


def test_backtest_measures_three_horizons(listings):
    results = run_backtest(listings)
    print("\n" + results.groupby("horizon_months")[["mape", "baseline_mape"]].mean().to_string())
    assert set(results["horizon_months"]) == {1, 2, 3}


@pytest.fixture(scope="module")
def next_month(listings):
    """Ranges set on one month, applied to the month after it: (rows, low, high, bands, default)."""
    fit, rest, _ = temporal_split(listings, test_days=60)
    calibration, test, _ = temporal_split(rest, test_days=30)
    model = build_model().fit(fit[FEATURES], fit[TARGET])
    bands, default = error_bands(calibration, model.predict(calibration[FEATURES]))
    prediction = model.predict(test[FEATURES])
    ratios = np.array([bands.get(t, default) for t in test["property_type"]])
    return test, prediction * ratios[:, 0], prediction * ratios[:, 1], bands, default


def test_error_bands_hold_on_the_next_month(next_month):
    """Ranges set on one month should hold most prices of the month after it."""
    test, low, high, bands, default = next_month
    price = test[TARGET].to_numpy()
    inside = (price >= low) & (price <= high)
    print(f"\nbands {bands}\ndefault {default}\ncoverage {inside.mean():.3f} on {len(price)} rows")
    assert all(lo < 1 < hi for lo, hi in [default, *bands.values()])
    assert 0.65 <= inside.mean() <= 0.95


def test_good_deals_are_rare_on_the_next_month(next_month):
    """Only a small share of new listings should ask less than the low end of their range."""
    test, low, _, _, _ = next_month
    deals = good_deals(test, test[TARGET], low)
    print(f"\ngood deals {deals.mean():.3f} of {len(test)} rows")
    assert 0.03 <= deals.mean() <= 0.25


def test_good_deal_needs_a_range_and_a_size():
    rows = pd.DataFrame({"area_m2": [50.0, np.nan, np.nan, 50.0], "bedrooms": [np.nan, 2.0, np.nan, 2.0]})
    deals = good_deals(rows, price=[1.0, 1.0, 1.0, 1.0], low=[2.0, 2.0, 2.0, np.nan])
    assert deals.tolist() == [True, True, False, False]
