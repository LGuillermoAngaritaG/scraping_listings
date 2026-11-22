"""Weekly check of the champion model on listings it has never seen.

Runs as a Databricks job task (spark_python_task). Steps:
1. Predict the price of listings first seen in the last `window-days`.
2. Append the error to the monitoring table.
3. Score all active listings into the predictions table (used by the dashboard).
4. If the error is above the threshold, start the retraining job.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mlflow  # noqa: E402
import pandas as pd  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402
from mlflow import MlflowClient  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402

from rent_model import CHAMPION, DATE, FEATURES, TARGET, metrics, prepare  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--retrain-job-id", type=int, required=True)
    parser.add_argument("--mape-threshold", type=float, default=0.60)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--min-rows", type=int, default=30)
    return parser.parse_args()


def main():
    args = parse_args()
    spark = SparkSession.builder.getOrCreate()
    prefix = f"{args.catalog}.{args.schema}"

    champion = MlflowClient().get_model_version_by_alias(args.model_name, CHAMPION)
    model = mlflow.pyfunc.load_model(f"models:/{args.model_name}@{CHAMPION}")
    train_cutoff = champion.tags.get("train_cutoff")

    df = prepare(spark.read.table(f"{prefix}.gold_rent_listings").toPandas())
    latest = df[DATE].max()

    # New listings: the champion never saw them, because they appeared after training.
    new = df[df[DATE] > latest - pd.Timedelta(days=args.window_days)]
    scores = metrics(new[TARGET], model.predict(new[FEATURES])) if len(new) else {"rows": 0}
    retrain = scores["rows"] >= args.min_rows and scores["mape"] > args.mape_threshold

    check = pd.DataFrame(
        [
            {
                "checked_at": datetime.now(timezone.utc),
                "model_version": int(champion.version),
                "window_days": args.window_days,
                "rows": scores["rows"],
                "mae": scores.get("mae", float("nan")),
                "mape": scores.get("mape", float("nan")),
                "mape_threshold": args.mape_threshold,
                "retrain_triggered": retrain,
            }
        ]
    )
    spark.createDataFrame(check).write.mode("append").saveAsTable(f"{prefix}.model_monitoring")

    # Active listings with their estimated price, for "over / under priced" views.
    active = df[df["last_seen_date"] > latest - pd.Timedelta(days=7)].copy()
    active["predicted_price_cop"] = model.predict(active[FEATURES]).round()
    active["price_gap_pct"] = active["last_price_cop"] / active["predicted_price_cop"] - 1
    active["model_version"] = int(champion.version)
    active["train_cutoff"] = pd.to_datetime(train_cutoff) if train_cutoff else pd.NaT
    columns = ["source", "property_id", "url", "first_seen_date", "last_seen_date"]
    columns += FEATURES + ["last_price_cop", "predicted_price_cop", "price_gap_pct"]
    columns += ["model_version", "train_cutoff"]
    (
        spark.createDataFrame(active[columns])
        .write.mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(f"{prefix}.rent_predictions")
    )

    print(f"champion v{champion.version}: {scores}, threshold {args.mape_threshold}")
    if retrain:
        run = WorkspaceClient().jobs.run_now(job_id=args.retrain_job_id)
        print(f"Error above threshold. Started retraining run {run.run_id}.")


if __name__ == "__main__":
    main()
