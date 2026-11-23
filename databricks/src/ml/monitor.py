"""Weekly check of the champion model on listings it has never seen.

Runs as a Databricks job task (spark_python_task). Steps:
1. Predict the price of listings first seen in the last `window-days`.
2. Append the error to the monitoring table.
3. If the error is above the threshold, start the retraining job.

Pricing all active listings (rent_predictions) is done daily by score.py.
"""

import argparse
import inspect
import os
import sys
from datetime import datetime, timezone

# Databricks runs this file with exec(), so __file__ is not set. The code object still has the path.
sys.path.insert(0, os.path.dirname(os.path.abspath(inspect.currentframe().f_code.co_filename)))

import mlflow  # noqa: E402
import pandas as pd  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402
from mlflow import MlflowClient  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402

from rent_model import (  # noqa: E402
    CHAMPION,
    DATE,
    FEATURES,
    TARGET,
    metrics,
    point_estimate,
    prepare,
)


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

    df = prepare(spark.read.table(f"{prefix}.gold_rent_listings").toPandas())
    latest = df[DATE].max()

    # New listings: the champion never saw them, because they appeared after training.
    new = df[df[DATE] > latest - pd.Timedelta(days=args.window_days)]
    scores = (
        metrics(new[TARGET], point_estimate(model.predict(new[FEATURES])))
        if len(new)
        else {"rows": 0}
    )
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

    print(f"champion v{champion.version}: {scores}, threshold {args.mape_threshold}")
    if retrain:
        run = WorkspaceClient().jobs.run_now(job_id=args.retrain_job_id)
        print(f"Error above threshold. Started retraining run {run.run_id}.")


if __name__ == "__main__":
    main()
