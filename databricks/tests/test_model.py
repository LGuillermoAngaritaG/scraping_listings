import numpy as np
import pandas as pd
import pytest
from conftest import one_file_per_day, read_bronze
from listings import build_listings, dedupe_daily, parse_listings, valid_rows
from model_search import search
from property_model import (
    FAMILIES,
    FEATURES,
    TARGET,
    Blend,
    baseline_predict,
    build_model,
    error_bands,
    good_deals,
    metrics,
    metrics_by,
    prepare,
    run_backtest,
    split,
    temporal_split,
)


@pytest.fixture(scope="module")
def listings(spark):
    bronze = read_bronze(spark, one_file_per_day())
    silver = dedupe_daily(parse_listings(bronze)).filter(valid_rows())
    return prepare(build_listings(silver, "arriendo").toPandas())


def test_model_beats_median_by_zone(listings):
    train, test, cutoff = temporal_split(listings, test_days=30)
    prediction = build_model().fit(train[FEATURES], train[TARGET]).predict(test[FEATURES])
    model = metrics(test[TARGET], prediction)
    baseline = metrics(test[TARGET], baseline_predict(train, test))
    print(f"\ncutoff {cutoff.date()} train {len(train)} test {len(test)}")
    print(f"model    {model}\nbaseline {baseline}")
    print(metrics_by(test, prediction, "property_type").to_string(index=False))
    assert model["mape"] < baseline["mape"]


@pytest.mark.parametrize("family", FAMILIES)
def test_every_model_type_beats_median_by_zone(listings, family):
    train, test, _ = temporal_split(listings, test_days=30)
    prediction = build_model(family).fit(train[FEATURES], train[TARGET]).predict(test[FEATURES])
    model, baseline = metrics(test[TARGET], prediction), metrics(test[TARGET], baseline_predict(train, test))
    print(f"\n{family} {model}\nbaseline {baseline}")
    assert model["mape"] < baseline["mape"]


def test_blend_is_the_geometric_mean_of_its_models():
    class Fixed:
        def __init__(self, price):
            self.price = price

        def predict(self, X):
            return np.full(len(X), self.price)

    assert Blend([Fixed(1.0), Fixed(4.0)]).predict(pd.DataFrame({"x": [0, 1]})).tolist() == [2.0, 2.0]


def test_search_keeps_the_candidate_with_the_lowest_validation_error(listings):
    """Two trials per model type: enough to run every step, not to tune well."""
    train, test, _, _ = split(listings, test_days=30)
    model, report = search(train, test_days=30, trials=2)
    scores = report["valid_mape"]
    print(f"\n{report['winner']} {scores} on {report['valid_rows']} validation rows")
    assert set(scores) == {*FAMILIES, "blend"} and report["winner"] == min(scores, key=scores.get)
    assert all(len(report["trials"][family]) == 2 for family in FAMILIES)
    assert 0 < report["valid_rows"] < len(train)
    prediction = model.predict(test[FEATURES])
    assert metrics(test[TARGET], prediction)["mape"] < metrics(test[TARGET], baseline_predict(train, test))["mape"]


def test_model_works_without_a_barrio(listings):
    """The app sends "unknown" when the user picks no barrio: the value prepare() gives a missing one.

    Without the barrio the model still has the city, estrato and the rest, so it must stay
    close to its usual error and far better than the median of the barrio.
    """
    train, test, _ = temporal_split(listings, test_days=30)
    model = build_model().fit(train[FEATURES], train[TARGET])
    with_barrio = metrics(test[TARGET], model.predict(test[FEATURES]))
    without = metrics(test[TARGET], model.predict(test[FEATURES].assign(neighborhood="unknown")))
    baseline = metrics(test[TARGET], baseline_predict(train, test))
    print(f"\nwith barrio {with_barrio}\nwithout    {without}\nbaseline   {baseline}")
    assert without["mape"] < with_barrio["mape"] + 0.05
    assert without["mape"] < baseline["mape"]


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


def test_baseline_keeps_same_barrio_names_of_two_cities_apart():
    train = pd.DataFrame({"city": ["bogota", "bogota", "medellin"], "neighborhood": ["la castellana"] * 3,
                          TARGET: [4.0, 6.0, 2.0]})
    test = pd.DataFrame({"city": ["bogota", "medellin", "envigado"], "neighborhood": ["la castellana"] * 3})
    assert baseline_predict(train, test).tolist() == [5.0, 2.0, 4.0]  # unknown pair: overall median


@pytest.mark.parametrize("bogota_days, envigado_days, kind",
                         [(5, 5, "random"), (90, 90, "temporal"), (90, 5, "mixed")])
def test_split_is_random_for_a_city_without_enough_history(bogota_days, envigado_days, kind):
    def listings(city, days):
        dates = pd.date_range(end="2026-03-31", periods=days).repeat(10)
        return pd.DataFrame({"city": city, "first_seen_date": dates, TARGET: 1.0})

    df = pd.concat([listings("bogota", bogota_days), listings("envigado", envigado_days)], ignore_index=True)
    train, test, cutoff, used = split(df, test_days=30)
    assert used == kind and len(train) + len(test) == len(df)
    for city, days in [("bogota", bogota_days), ("envigado", envigado_days)]:
        city_train, city_test = train[train["city"] == city], test[test["city"] == city]
        assert len(city_train) > 0, "every city must be in training"
        if days >= 60:  # old enough: train on the past, test on the last 30 days
            assert city_train["first_seen_date"].max() <= cutoff < city_test["first_seen_date"].min()
    if kind == "mixed":  # the new city: a random 1 in 5
        assert len(test[test["city"] == "envigado"]) == 5 * 10 // 5


def test_split_cutoff_follows_the_old_cities():
    """Inside the training data the new cities have the latest days. The old city must still get test rows."""
    old = pd.DataFrame({"city": "bogota", "first_seen_date": pd.date_range(end="2026-03-01", periods=90).repeat(10)})
    new = pd.DataFrame({"city": "envigado", "first_seen_date": pd.date_range(end="2026-03-31", periods=5).repeat(10)})
    train, test, cutoff, kind = split(pd.concat([old, new], ignore_index=True).assign(**{TARGET: 1.0}), test_days=30)
    assert kind == "mixed" and cutoff == pd.Timestamp("2026-01-30")
    assert len(test[test["city"] == "bogota"]) == 30 * 10
