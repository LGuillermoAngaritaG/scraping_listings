"""Model code shared by training, backtest and monitoring.

Plain pandas and scikit-learn, so the tests can run it without Databricks.
"""

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.compose import ColumnTransformer, TransformedTargetRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

TARGET = "price_cop"
DATE = "first_seen_date"
CATEGORICAL = ["source", "property_type", "neighborhood"]
NUMERIC = ["area_m2", "bedrooms", "bathrooms", "parking", "estrato", "age_years"]
FEATURES = CATEGORICAL + NUMERIC

CHAMPION = "campeon"
CHALLENGER = "retador"


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


def build_model(seed: int = 42) -> TransformedTargetRegressor:
    """LightGBM on log(price). Text columns become integer codes; unknown values get -1."""
    encode = ColumnTransformer(
        [
            (
                "categorical",
                OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1),
                CATEGORICAL,
            )
        ],
        remainder="passthrough",
    )
    lgbm = LGBMRegressor(
        n_estimators=600,
        learning_rate=0.03,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        random_state=seed,
        verbose=-1,
    )
    return TransformedTargetRegressor(
        regressor=Pipeline([("encode", encode), ("lgbm", lgbm)]),
        func=np.log,
        inverse_func=np.exp,
    )


def baseline_predict(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """Median price of the neighborhood in the training data, else the overall median."""
    by_zone = train.groupby("neighborhood")[TARGET].median()
    return test["neighborhood"].map(by_zone).fillna(train[TARGET].median()).to_numpy()


def metrics(y_true, y_pred) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    error = np.abs(y_true - y_pred)
    return {
        "rows": int(len(y_true)),
        "mae": float(error.mean()),
        "mape": float((error / y_true).mean()),
    }


def error_bands(test: pd.DataFrame, prediction, low: float = 0.1, high: float = 0.9, min_rows: int = 50):
    """How far real prices fall from the prediction, as ratios, per property type.

    Returns ({type: (low_ratio, high_ratio)}, default). With the default quantiles,
    8 in 10 prices of the test listings were between prediction * low_ratio and
    prediction * high_ratio. Types with fewer than `min_rows` test rows use the
    ratios of all rows (the default).
    """
    log_ratio = np.log(test[TARGET].to_numpy(dtype=float) / np.asarray(prediction, dtype=float))
    frame = pd.DataFrame({"property_type": test["property_type"].to_numpy(), "r": log_ratio})

    def ratios(values):
        return tuple(float(x) for x in np.exp(np.quantile(values, [low, high])))

    bands = {
        name: ratios(group["r"])
        for name, group in frame.groupby("property_type")
        if len(group) >= min_rows
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
