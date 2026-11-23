"""Model code shared by training, backtest and monitoring, for the rent and the sale model.

Named rent_model.py until Nov 2026: model versions logged before then carry it under that name.

Plain pandas and scikit-learn, so the tests can run it without Databricks.
"""

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from lightgbm import LGBMRegressor
from sklearn.compose import ColumnTransformer, TransformedTargetRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OrdinalEncoder
from xgboost import XGBRegressor

TARGET = "price_cop"
DATE = "first_seen_date"
# City and neighborhood together: "la castellana" is a barrio in Bogota and in Medellin.
CATEGORICAL = ["source", "city", "property_type", "neighborhood"]
NUMERIC = ["area_m2", "bedrooms", "bathrooms", "parking", "estrato", "age_years"]
FEATURES = CATEGORICAL + NUMERIC
# What train.py and score.py need to know about a kind of listing. vehicle_model.py has its own.
ID = "property_id"
BAND_GROUP = "property_type"  # the likely range is set per property type
BREAKDOWNS = {"metrics_by_type.json": "property_type", "metrics_by_zone.json": "neighborhood"}
EXTRA_COLUMNS: list[str] = []  # columns copied to the predictions table besides FEATURES

CHAMPION = "campeon"
CHALLENGER = "retador"

# The model types the monthly search tunes. Random forest, a linear model and a small neural
# net were tried too (Nov 2026): each was worse alone and made the average worse.
FAMILIES = ("lightgbm", "catboost", "xgboost")
# Fixed LightGBM settings, used before the search (up to rent version 4) and by the backtest.
LIGHTGBM_DEFAULTS = {"n_estimators": 600, "learning_rate": 0.03, "num_leaves": 31, "min_child_samples": 20,
                     "subsample": 0.8, "colsample_bytree": 0.8}


def prepare(listings: pd.DataFrame) -> pd.DataFrame:
    """Fix types after toPandas(): dates as datetime, text as str, numbers as float."""
    df = listings.copy()
    for column in ["first_seen_date", "last_seen_date"]:
        df[column] = pd.to_datetime(df[column])
    for column in CATEGORICAL:
        df[column] = df[column].fillna("unknown").astype(str)
    for column in NUMERIC:
        df[column] = pd.to_numeric(df[column], errors="coerce").astype(float)
    return df


def temporal_split(df: pd.DataFrame, test_days: int):
    """Train on the past, test on the most recent `test_days`. Never a random split."""
    cutoff = df[DATE].max() - pd.Timedelta(days=test_days)
    return df[df[DATE] <= cutoff], df[df[DATE] > cutoff], cutoff


def split(df: pd.DataFrame, test_days: int, seed: int = 42):
    """(train, test, cutoff, kind), decided city by city.

    A city whose data covers 2 x test_days is tested on its listings first seen in the last
    test_days. A newer city has no past to train on (all sales in their first weeks, a city
    just added to the scrapers), so a random 1 in 5 of its listings is the test and the rest
    is trained on. Otherwise no model would learn a new city until it is test_days old.
    kind is "temporal", "random" (every city is new) or "mixed".
    """
    dates = df.groupby("city")[DATE]
    new_city = (dates.transform("max") - dates.transform("min")) < pd.Timedelta(days=2 * test_days)
    if new_city.all():
        cutoff, kind = df[DATE].max(), "random"
    else:  # from the last day of the old cities: newer cities can have later days
        cutoff = df.loc[~new_city, DATE].max() - pd.Timedelta(days=test_days)
        kind = "mixed" if new_city.any() else "temporal"
    recent = df[~new_city & (df[DATE] > cutoff)]
    sampled = df[new_city].sample(n=int(new_city.sum()) // 5, random_state=seed)
    test = pd.concat([recent, sampled])
    return df.drop(test.index), test, cutoff, kind


def build_model(family: str = "lightgbm", params: dict | None = None, seed: int = 42,
                categorical: list[str] | None = None) -> TransformedTargetRegressor:
    """One model type on log(price), as a pipeline: encode, then the model.

    LightGBM and XGBoost get text columns as integer codes, with -1 for a value not seen in
    training. CatBoost takes the text itself. Without params, LightGBM gets LIGHTGBM_DEFAULTS
    and the others their library defaults. `categorical` defaults to the property columns.
    """
    params = dict(params or {})
    categorical = list(categorical or CATEGORICAL)
    if family == "lightgbm":
        model = LGBMRegressor(**{**LIGHTGBM_DEFAULTS, **params}, subsample_freq=1, random_state=seed, verbose=-1)
    elif family == "xgboost":
        model = XGBRegressor(**params, tree_method="hist", random_state=seed)
    elif family == "catboost":
        # A tuple: CatBoost copies a list, and then scikit-learn cannot clone the model.
        model = CatBoostRegressor(**params, cat_features=tuple(categorical), random_seed=seed, verbose=0,
                                  allow_writing_files=False)
    else:
        raise ValueError(f"unknown model type {family}")
    if family == "catboost":
        encode = FunctionTransformer()  # keeps the table as it is
    else:
        encode = ColumnTransformer(
            [("categorical", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1), categorical)],
            remainder="passthrough",
        )
    return TransformedTargetRegressor(
        regressor=Pipeline([("encode", encode), ("model", model)]), func=np.log, inverse_func=np.exp
    )


class Blend:
    """Average of several models on the log scale: the geometric mean of their prices.

    Models that make different mistakes cancel part of them out.
    """

    def __init__(self, models: list):
        self.models = models

    def fit(self, X, y):
        for model in self.models:
            model.fit(X, y)
        return self

    def predict(self, X) -> np.ndarray:
        return np.exp(np.mean([np.log(model.predict(X)) for model in self.models], axis=0))


def baseline_predict(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """Median price of the neighborhood (in its city) in the training data, else the overall median."""
    by_zone = train.groupby(["city", "neighborhood"])[TARGET].median().rename("zone_median")
    zone = test[["city", "neighborhood"]].join(by_zone, on=["city", "neighborhood"])["zone_median"]
    return zone.fillna(train[TARGET].median()).to_numpy()


def metrics(y_true, y_pred) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    error = np.abs(y_true - y_pred)
    return {
        "rows": int(len(y_true)),
        "mae": float(error.mean()),
        "mape": float((error / y_true).mean()),
    }


def error_bands(test: pd.DataFrame, prediction, low: float = 0.1, high: float = 0.9, min_rows: int = 50,
                group: str = "property_type"):
    """How far real prices fall from the prediction, as ratios, per group (property type by default).

    Returns ({group value: (low_ratio, high_ratio)}, default). With the default quantiles,
    8 in 10 prices of the test listings were between prediction * low_ratio and
    prediction * high_ratio. Groups with fewer than `min_rows` test rows use the
    ratios of all rows (the default).
    """
    log_ratio = np.log(test[TARGET].to_numpy(dtype=float) / np.asarray(prediction, dtype=float))
    frame = pd.DataFrame({"group": test[group].to_numpy(), "r": log_ratio})

    def ratios(values):
        return tuple(float(x) for x in np.exp(np.quantile(values, [low, high])))

    bands = {
        name: ratios(rows["r"])
        for name, rows in frame.groupby("group")
        if len(rows) >= min_rows
    }
    return bands, ratios(frame["r"])


def point_estimate(output) -> np.ndarray:
    """Prediction from a loaded model: a table with prediction/low/high, or a plain array
    for model versions registered before the range was added."""
    if isinstance(output, pd.DataFrame):
        return output["prediction"].to_numpy()
    return np.asarray(output)


def good_deals(listings: pd.DataFrame, price, low) -> np.ndarray:
    """True where the asking price is below the low end of the listing's likely range.

    The low end comes from error_bands (10th percentile), so with an unbiased model
    about 1 in 10 listings is a good deal. A missing low end is never a deal. Nor is a
    listing with no size (no area and no bedrooms, common for offices): the model
    cannot tell a small one from a large one, so a small one would look cheap.
    """
    size_known = (listings["area_m2"].notna() | listings["bedrooms"].notna()).to_numpy()
    return size_known & (np.asarray(price, dtype=float) < np.asarray(low, dtype=float))


def deal_label(listings: pd.DataFrame) -> pd.Series:
    """A short description of each listing for the new-deals email: "Apartamento, Venecia, 2 bedrooms"."""

    def label(row: dict) -> str:
        parts = [row["property_type"].capitalize()]
        if row["neighborhood"] != "unknown":
            parts.append(row["neighborhood"].title())
        if pd.notna(row["area_m2"]):
            parts.append(f"{row['area_m2']:.0f} m2")
        if pd.notna(row["bedrooms"]):
            parts.append(f"{row['bedrooms']:.0f} bedroom" + ("" if row["bedrooms"] == 1 else "s"))
        return ", ".join(parts)

    return pd.Series([label(row) for row in listings.to_dict("records")], index=listings.index, dtype=object)


def metrics_by(test: pd.DataFrame, y_pred, column: str, min_rows: int = 20) -> pd.DataFrame:
    """MAE and MAPE per group (property type or neighborhood) with enough rows."""
    frame = test[[column, TARGET]].assign(prediction=np.asarray(y_pred))
    rows = [
        {column: name, **metrics(group[TARGET], group["prediction"])}
        for name, group in frame.groupby(column)
        if len(group) >= min_rows
    ]
    columns = [column, "rows", "mae", "mape"]
    return pd.DataFrame(rows, columns=columns).sort_values("rows", ascending=False)


def run_backtest(df: pd.DataFrame, horizons: int = 3, min_train_months: int = 3) -> pd.DataFrame:
    """Train up to each month start, then measure error 1, 2 and 3 months later.

    The slope of error against months elapsed tells how often to retrain.
    """
    months = pd.date_range(
        df[DATE].min().to_period("M").to_timestamp(), df[DATE].max(), freq="MS"
    )
    results = []
    for cutoff in months[min_train_months:]:
        train = df[df[DATE] < cutoff]
        model = build_model().fit(train[FEATURES], train[TARGET])
        for horizon in range(1, horizons + 1):
            start = cutoff + pd.DateOffset(months=horizon - 1)
            test = df[(df[DATE] >= start) & (df[DATE] < start + pd.DateOffset(months=1))]
            if len(test) == 0:
                continue
            model_scores = metrics(test[TARGET], model.predict(test[FEATURES]))
            baseline_scores = metrics(test[TARGET], baseline_predict(train, test))
            results.append(
                {
                    "cutoff": cutoff.date(),
                    "horizon_months": horizon,
                    "train_rows": len(train),
                    "test_rows": model_scores["rows"],
                    "mae": model_scores["mae"],
                    "mape": model_scores["mape"],
                    "baseline_mape": baseline_scores["mape"],
                }
            )
    return pd.DataFrame(results)
