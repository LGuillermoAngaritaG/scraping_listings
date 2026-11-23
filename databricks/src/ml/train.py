"""Train a challenger, compare it with the champion, promote it only if it wins.

Runs as a Databricks job task (spark_python_task). Steps:
1. Copy the gold table into a training table and note its Delta version (time travel).
2. Train the challenger on the past, keep the last `test_days` for testing.
3. Score baseline, challenger and current champion on the same test rows.
4. Register the challenger with alias "retador". Move "campeon" to it only if it
   beats the baseline and the champion (lower MAPE).
5. Point the serving endpoint at the new champion.

The registered model returns a price and a likely range (low, high). The range
comes from the challenger's errors on the test rows, per property type.
"""

import argparse
import inspect
import os
import sys

# Databricks runs this file with exec(), so __file__ is not set. The code object still has the path.
HERE = os.path.dirname(os.path.abspath(inspect.currentframe().f_code.co_filename))
sys.path.insert(0, HERE)

import lightgbm  # noqa: E402
import mlflow  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import sklearn  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.serving import EndpointCoreConfigInput, ServedEntityInput  # noqa: E402
from mlflow import MlflowClient  # noqa: E402
from mlflow.exceptions import MlflowException  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402

from banded_model import BandedRentModel  # noqa: E402
from rent_model import (  # noqa: E402
    CHALLENGER,
    CHAMPION,
    FEATURES,
    TARGET,
    baseline_predict,
    build_model,
    error_bands,
    metrics,
    metrics_by,
    point_estimate,
    prepare,
    temporal_split,
)

# Pinned to the training environment so serving unpickles the same objects.
PIP_REQUIREMENTS = [
    f"mlflow=={mlflow.__version__}",
    f"lightgbm=={lightgbm.__version__}",
    f"scikit-learn=={sklearn.__version__}",
    f"pandas=={pd.__version__}",
    f"numpy=={np.__version__}",
]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--endpoint-name", required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--test-days", type=int, default=30)
    return parser.parse_args()


def snapshot_training_data(spark, catalog: str, schema: str):
    """Copy gold into a plain Delta table and return (table, version).

    Materialized views do not support time travel, so the copy is what makes
    `VERSION AS OF` possible later. Old versions are kept for a year.
    """
    gold = f"{catalog}.{schema}.gold_rent_listings"
    training = f"{catalog}.{schema}.rent_training_data"
    spark.read.table(gold).write.mode("overwrite").saveAsTable(training)
    spark.sql(
        f"ALTER TABLE {training} SET TBLPROPERTIES ("
        "'delta.deletedFileRetentionDuration' = 'interval 365 days', "
        "'delta.logRetentionDuration' = 'interval 365 days')"
    )
    version = spark.sql(f"DESCRIBE HISTORY {training} LIMIT 1").first()["version"]
    return training, version


def champion_predictions(client: MlflowClient, model_name: str, test):
    """Predictions of the current champion, or None if there is no champion yet."""
    try:
        version = client.get_model_version_by_alias(model_name, CHAMPION)
    except MlflowException:
        return None, None
    model = mlflow.pyfunc.load_model(f"models:/{model_name}@{CHAMPION}")
    return point_estimate(model.predict(test[FEATURES])), version.version


def deploy_endpoint(endpoint_name: str, model_name: str, version: str):
    """Create the serving endpoint, or move it to the given model version."""
    w = WorkspaceClient()
    served = [
        ServedEntityInput(
            entity_name=model_name,
            entity_version=str(version),
            workload_size="Small",
            scale_to_zero_enabled=True,
        )
    ]
    if endpoint_name in {e.name for e in w.serving_endpoints.list()}:
        w.serving_endpoints.update_config_and_wait(name=endpoint_name, served_entities=served)
    else:
        w.serving_endpoints.create_and_wait(
            name=endpoint_name,
            config=EndpointCoreConfigInput(name=endpoint_name, served_entities=served),
        )


def main():
    args = parse_args()
    spark = SparkSession.builder.getOrCreate()
    client = MlflowClient()
    mlflow.set_experiment(args.experiment)

    training_table, table_version = snapshot_training_data(spark, args.catalog, args.schema)
    df = prepare(spark.sql(f"SELECT * FROM {training_table} VERSION AS OF {table_version}").toPandas())
    train, test, cutoff = temporal_split(df, args.test_days)

    with mlflow.start_run(run_name="retrain") as run:
        mlflow.log_params(
            {
                "training_table": training_table,
                "training_table_version": table_version,
                "train_cutoff": cutoff.date().isoformat(),
                "test_days": args.test_days,
                "train_rows": len(train),
                "test_rows": len(test),
            }
        )

        baseline = metrics(test[TARGET], baseline_predict(train, test))
        model = build_model().fit(train[FEATURES], train[TARGET])
        prediction = model.predict(test[FEATURES])
        challenger = metrics(test[TARGET], prediction)
        mlflow.log_metrics({f"baseline_{k}": v for k, v in baseline.items()})
        mlflow.log_metrics({f"challenger_{k}": v for k, v in challenger.items()})
        mlflow.log_table(metrics_by(test, prediction, "property_type"), "metrics_by_type.json")
        mlflow.log_table(metrics_by(test, prediction, "neighborhood"), "metrics_by_zone.json")
        bands, default_band = error_bands(test, prediction)
        mlflow.log_dict({"by_type": bands, "default": default_band}, "error_bands.json")

        champion_pred, champion_version = champion_predictions(client, args.model_name, test)
        champion = metrics(test[TARGET], champion_pred) if champion_pred is not None else None
        if champion:
            mlflow.log_metrics({f"champion_{k}": v for k, v in champion.items()})

        info = mlflow.pyfunc.log_model(
            name="model",
            python_model=BandedRentModel(model, FEATURES, bands, default_band),
            code_paths=[os.path.join(HERE, "banded_model.py")],
            input_example=train[FEATURES].head(5),
            pip_requirements=PIP_REQUIREMENTS,
            registered_model_name=args.model_name,
        )
        new_version = info.registered_model_version
        client.set_registered_model_alias(args.model_name, CHALLENGER, new_version)
        client.set_model_version_tag(
            args.model_name, new_version, "train_cutoff", cutoff.date().isoformat()
        )

        beats_baseline = challenger["mape"] < baseline["mape"]
        beats_champion = champion is None or challenger["mape"] < champion["mape"]
        promote = beats_baseline and beats_champion
        mlflow.set_tags(
            {
                "champion_version_before": champion_version or "none",
                "challenger_version": new_version,
                "promoted": str(promote),
            }
        )
        print(f"baseline {baseline}\nchallenger {challenger}\nchampion {champion}")

        if promote:
            client.set_registered_model_alias(args.model_name, CHAMPION, new_version)
            deploy_endpoint(args.endpoint_name, args.model_name, new_version)
            print(f"Promoted version {new_version} (run {run.info.run_id}).")
        else:
            print(f"Kept champion version {champion_version}; challenger did not win.")


if __name__ == "__main__":
    main()
