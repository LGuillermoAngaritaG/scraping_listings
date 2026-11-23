"""Backtest: how fast does the model get worse as months pass?

Runs as a Databricks job task (spark_python_task). For each month start, it
trains on everything before it and measures the error 1, 2 and 3 months later.
Results go to the backtest_results table and to MLflow with a chart.
"""

import argparse
import inspect
import os
import sys

# Databricks runs this file with exec(), so __file__ is not set. The code object still has the path.
sys.path.insert(0, os.path.dirname(os.path.abspath(inspect.currentframe().f_code.co_filename)))

import matplotlib.pyplot as plt  # noqa: E402
import mlflow  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402

from rent_model import prepare, run_backtest  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--horizons", type=int, default=3)
    parser.add_argument("--min-train-months", type=int, default=3)
    return parser.parse_args()


def error_chart(results):
    """Mean MAPE against months elapsed since training."""
    by_horizon = results.groupby("horizon_months")[["mape", "baseline_mape"]].mean()
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(by_horizon.index, by_horizon["mape"], marker="o", label="model")
    ax.plot(by_horizon.index, by_horizon["baseline_mape"], marker="o", label="baseline")
    ax.set_xlabel("Months after training")
    ax.set_ylabel("Mean MAPE")
    ax.set_xticks(by_horizon.index)
    ax.legend()
    return fig


def main():
    args = parse_args()
    spark = SparkSession.builder.getOrCreate()
    prefix = f"{args.catalog}.{args.schema}"
    mlflow.set_experiment(args.experiment)

    df = prepare(spark.read.table(f"{prefix}.gold_rent_listings").toPandas())
    results = run_backtest(df, args.horizons, args.min_train_months)
    spark.createDataFrame(results).write.mode("overwrite").saveAsTable(f"{prefix}.backtest_results")

    with mlflow.start_run(run_name="backtest"):
        mlflow.log_params({"horizons": args.horizons, "min_train_months": args.min_train_months})
        mlflow.log_table(results, "backtest_results.json")
        mlflow.log_figure(error_chart(results), "error_by_horizon.png")
    print(results.groupby("horizon_months")[["mape", "baseline_mape"]].mean())


if __name__ == "__main__":
    main()
