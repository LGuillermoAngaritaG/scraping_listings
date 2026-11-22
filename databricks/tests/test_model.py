import pytest

from conftest import one_file_per_day, read_bronze
from listings import build_rent_listings, dedupe_daily, parse_listings, valid_rows
from rent_model import (
    FEATURES,
    TARGET,
    baseline_predict,
    build_model,
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
