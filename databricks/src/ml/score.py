"""Daily: price every active listing with the champion model and flag good deals.

Runs as a Databricks job task (spark_python_task) after the pipeline refresh.
Writes the rent_predictions table, used by the dashboard and the app. A good deal
asks less than the low end of the likely range the model gives for it.
"""

import argparse
import inspect
import os
import sys

# Databricks runs this file with exec(), so __file__ is not set. The code object still has the path.
sys.path.insert(0, os.path.dirname(os.path.abspath(inspect.currentframe().f_code.co_filename)))

import mlflow  # noqa: E402
import pandas as pd  # noqa: E402
from mlflow import MlflowClient  # noqa: E402
from mlflow.exceptions import MlflowException  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402

from rent_model import CHAMPION, DATE, FEATURES, good_deals, point_estimate, prepare  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--active-days", type=int, default=7)
    return parser.parse_args()


def main():
    args = parse_args()
    spark = SparkSession.builder.getOrCreate()
    prefix = f"{args.catalog}.{args.schema}"

    try:
        champion = MlflowClient().get_model_version_by_alias(args.model_name, CHAMPION)
    except MlflowException:  # first setup: the refresh runs before the first training
        print("No champion model yet. Run the retrain job; nothing was scored.")
        return
    model = mlflow.pyfunc.load_model(f"models:/{args.model_name}@{CHAMPION}")
    train_cutoff = champion.tags.get("train_cutoff")

    df = prepare(spark.read.table(f"{prefix}.gold_rent_listings").toPandas())
    latest = df[DATE].max()

    # Active listings: seen by a scraper in the last `active-days`.
    active = df[df["last_seen_date"] > latest - pd.Timedelta(days=args.active_days)].copy()
    output = model.predict(active[FEATURES])
    active["predicted_price_cop"] = point_estimate(output).round()
    # Model versions registered before the range existed return no low end: no deals.
    active["predicted_low_cop"] = (
        output["low"].round().to_numpy() if isinstance(output, pd.DataFrame) else float("nan")
    )
    active["price_gap_pct"] = active["last_price_cop"] / active["predicted_price_cop"] - 1
    active["good_deal"] = good_deals(active, active["last_price_cop"], active["predicted_low_cop"])
    active["model_version"] = int(champion.version)
    active["train_cutoff"] = pd.to_datetime(train_cutoff) if train_cutoff else pd.NaT
    columns = ["property_id", "url", "first_seen_date", "last_seen_date"]  # source is in FEATURES
    columns += FEATURES + ["last_price_cop", "predicted_price_cop", "predicted_low_cop"]
    columns += ["price_gap_pct", "good_deal", "model_version", "train_cutoff"]
    (
        spark.createDataFrame(active[columns])
        .write.mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(f"{prefix}.rent_predictions")
    )
    print(f"champion v{champion.version}: {len(active)} active listings, {active['good_deal'].sum()} good deals")


if __name__ == "__main__":
    main()
