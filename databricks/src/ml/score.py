"""Daily: price every active listing with the champion model and flag good deals.

Runs as a Databricks job task (spark_python_task) after the pipeline refresh, once
each for rentals, sales and vehicles (--listings). Writes rent_predictions,
sale_predictions or vehicle_predictions, used by the dashboard and the app. A good deal asks less than
the low end of the likely range the model gives for it. The day's good deals are also kept in
rent_deal_history (sale_, vehicle_), so the new-deals alerts (resources/alerts.yml) can tell
which deals are new since the day before.
"""

import argparse
import inspect
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Databricks runs this file with exec(), so __file__ is not set. The code object still has the path.
sys.path.insert(0, os.path.dirname(os.path.abspath(inspect.currentframe().f_code.co_filename)))

import mlflow  # noqa: E402
import pandas as pd  # noqa: E402
import property_model  # noqa: E402
import vehicle_model  # noqa: E402
from mlflow import MlflowClient  # noqa: E402
from mlflow.exceptions import MlflowException  # noqa: E402
from property_model import CHAMPION, DATE, point_estimate  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402

HISTORY_COLUMNS = (
    "listings STRING, source STRING, listing_id STRING, scored_date DATE, listing_type STRING, label STRING, city STRING, "
    "last_price_cop DOUBLE, predicted_price_cop DOUBLE, predicted_low_cop DOUBLE, price_gap_pct DOUBLE, url STRING"
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listings", choices=["rent", "sale", "vehicle"], default="rent")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--active-days", type=int, default=7)
    return parser.parse_args()


def main():
    args = parse_args()
    spec = vehicle_model if args.listings == "vehicle" else property_model
    spark = SparkSession.builder.getOrCreate()
    prefix = f"{args.catalog}.{args.schema}"

    try:
        champion = MlflowClient().get_model_version_by_alias(args.model_name, CHAMPION)
    except MlflowException:  # first setup: the refresh runs before the first training
        print("No champion model yet. Run the retrain job; nothing was scored.")
        return
    model = mlflow.pyfunc.load_model(f"models:/{args.model_name}@{CHAMPION}")
    train_cutoff = champion.tags.get("train_cutoff")

    df = spec.prepare(spark.read.table(f"{prefix}.gold_{args.listings}_listings").toPandas())
    latest = df[DATE].max()

    # Active listings: seen by a scraper in the last `active-days`.
    active = df[df["last_seen_date"] > latest - pd.Timedelta(days=args.active_days)].copy()
    output = model.predict(active[spec.FEATURES])
    active["predicted_price_cop"] = point_estimate(output).round()
    # Model versions registered before the range existed return no low end: no deals.
    active["predicted_low_cop"] = (
        output["low"].round().to_numpy() if isinstance(output, pd.DataFrame) else float("nan")
    )
    active["price_gap_pct"] = active["last_price_cop"] / active["predicted_price_cop"] - 1
    active["good_deal"] = spec.good_deals(active, active["last_price_cop"], active["predicted_low_cop"])
    active["model_version"] = int(champion.version)
    active["train_cutoff"] = pd.to_datetime(train_cutoff) if train_cutoff else pd.NaT
    columns = [spec.ID, "url", "first_seen_date", "last_seen_date"]  # source is in FEATURES
    columns += spec.FEATURES + spec.EXTRA_COLUMNS + ["last_price_cop", "predicted_price_cop", "predicted_low_cop"]
    columns += ["price_gap_pct", "good_deal", "model_version", "train_cutoff"]
    (
        spark.createDataFrame(active[columns])
        .write.mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(f"{prefix}.{args.listings}_predictions")
    )
    print(f"champion v{champion.version}: {len(active)} active listings, {active['good_deal'].sum()} good deals")

    # Today's deals replace any from an earlier run today. The date is Bogota's, where the job is scheduled.
    today = datetime.now(ZoneInfo("America/Bogota")).date()
    deals = active[active["good_deal"]]
    history = pd.DataFrame({
        "listings": args.listings,
        "source": deals["source"],
        "listing_id": deals[spec.ID].astype(str),
        "scored_date": today,
        "listing_type": deals[spec.BAND_GROUP],  # property type, or body type for cars
        "label": spec.deal_label(deals),
        "city": deals["city"],
        "last_price_cop": deals["last_price_cop"].astype(float),
        "predicted_price_cop": deals["predicted_price_cop"].astype(float),
        "predicted_low_cop": deals["predicted_low_cop"].astype(float),
        "price_gap_pct": deals["price_gap_pct"].astype(float),
        "url": deals["url"],
    })
    table = f"{prefix}.{args.listings}_deal_history"
    spark.sql(f"CREATE TABLE IF NOT EXISTS {table} ({HISTORY_COLUMNS})")
    (
        spark.createDataFrame(history, HISTORY_COLUMNS)
        .write.mode("overwrite")
        .option("replaceWhere", f"scored_date = '{today}'")
        .saveAsTable(table)
    )


if __name__ == "__main__":
    main()
