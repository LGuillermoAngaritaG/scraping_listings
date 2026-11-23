"""What the vehicle price model needs to know about a car.

The model types, their average, the split, the search and the metrics are the same as for
property prices (property_model.py, model_search.py). Only the columns, the baseline and the
good-deal rule differ, so train.py and score.py take them from here for --listings vehicle.
"""

import numpy as np
import pandas as pd
from property_model import TARGET

CATEGORICAL = ["source", "city", "brand", "line", "transmission", "fuel", "body_type"]
NUMERIC = ["year", "km", "engine_cc"]
FEATURES = CATEGORICAL + NUMERIC
ID = "listing_id"
BAND_GROUP = "body_type"  # the likely range is set per body type
BREAKDOWNS = {"metrics_by_brand.json": "brand", "metrics_by_body_type.json": "body_type"}
EXTRA_COLUMNS = ["title", "version", "color", "branch"]  # shown in the dashboard and the app


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


def baseline_predict(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """Median price of the same brand, line and year in the training data.

    Without one, the median of the brand and line, then of the brand, then of all cars.
    """
    estimate = pd.Series(np.nan, index=test.index)
    for keys in (["brand", "line", "year"], ["brand", "line"], ["brand"]):
        median = train.groupby(keys)[TARGET].median().rename("median")
        estimate = estimate.fillna(test[keys].join(median, on=keys)["median"])
    return estimate.fillna(train[TARGET].median()).to_numpy()


def good_deals(listings: pd.DataFrame, price, low) -> np.ndarray:
    """True where the asking price is below the low end of the car's likely range.

    The year and the mileage must be known: without them the model cannot tell an old,
    worn car from a newer one, so an old one would look cheap.
    """
    known = (listings["year"].notna() & listings["km"].notna()).to_numpy()
    return known & (np.asarray(price, dtype=float) < np.asarray(low, dtype=float))


def deal_label(listings: pd.DataFrame) -> pd.Series:
    """A short description of each car for the new-deals email: "Kia Sportage 2020, 50,000 km"."""

    def label(row: dict) -> str:
        name = f"{row['brand']} {row['line']}".title()
        if pd.notna(row["year"]):
            name += f" {row['year']:.0f}"
        return f"{name}, {row['km']:,.0f} km" if pd.notna(row["km"]) else name

    return pd.Series([label(row) for row in listings.to_dict("records")], index=listings.index, dtype=object)
