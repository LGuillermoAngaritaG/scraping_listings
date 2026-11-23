"""Train a challenger, compare it with the champion, promote it only if it wins.

Runs as a Databricks job task (spark_python_task), once each for rentals, sales and
vehicles (--listings), each with its own registered model and endpoint. Steps:
1. Copy the gold table into a training table and note its Delta version (time travel).
2. Keep the last `test_days` of each city for testing (a random 1 in 5 for a city with
   less than 2 x test_days of data).
3. Search the challenger on the rest (model_search.py): tune LightGBM, CatBoost and
   XGBoost with Optuna on a validation slice, add their average, keep the best.
4. Score baseline, challenger and current champion on the same test rows.
5. Register the challenger with alias "retador". Move "campeon" to it only if it
   beats the baseline and the champion (lower MAPE).
6. Point the serving endpoint at the new champion. The vehicle model has no endpoint (Free
   Edition allows two, both used by rent and sale): the app loads its champion itself.

The registered model returns a price and a likely range (low, high). The range
comes from the challenger's errors on the test rows, per property type (body type
for vehicles). The columns, baseline and good-deal rule of each kind of listing come
from property_model.py or vehicle_model.py.
"""

import argparse
import inspect
import os
import sys

# Databricks runs this file with exec(), so __file__ is not set. The code object still has the path.
HERE = os.path.dirname(os.path.abspath(inspect.currentframe().f_code.co_filename))
sys.path.insert(0, HERE)

import catboost  # noqa: E402
import lightgbm  # noqa: E402
import mlflow  # noqa: E402
import numpy as np  # noqa: E402
import optuna  # noqa: E402
import pandas as pd  # noqa: E402
import property_model  # noqa: E402
import sklearn  # noqa: E402
import vehicle_model  # noqa: E402
import xgboost  # noqa: E402
from banded_model import BandedRentModel  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.serving import EndpointCoreConfigInput, ServedEntityInput  # noqa: E402
from mlflow import MlflowClient  # noqa: E402
from mlflow.exceptions import MlflowException  # noqa: E402
from model_search import search  # noqa: E402
from property_model import (  # noqa: E402
    CHALLENGER,
    CHAMPION,
    TARGET,
    error_bands,
    metrics,
    metrics_by,
    point_estimate,
    split,
)
from pyspark.sql import SparkSession  # noqa: E402

# Pinned to the training environment so serving unpickles the same objects.
PIP_REQUIREMENTS = [
    f"mlflow=={mlflow.__version__}",
    f"lightgbm=={lightgbm.__version__}",
    f"catboost=={catboost.__version__}",
    f"xgboost=={xgboost.__version__}",
    f"scikit-learn=={sklearn.__version__}",
    f"pandas=={pd.__version__}",
    f"numpy=={np.__version__}",
]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listings", choices=["rent", "sale", "vehicle"], default="rent")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--endpoint-name", help="Serving endpoint to update; none for the vehicle model")
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--trials", type=int, default=30, help="Optuna trials per model type")
    return parser.parse_args()


def snapshot_training_data(spark, catalog: str, schema: str, listings: str):
    """Copy gold into a plain Delta table and return (table, version).

    Materialized views do not support time travel, so the copy is what makes
    `VERSION AS OF` possible later. Old versions are kept for a year.
    """
    gold = f"{catalog}.{schema}.gold_{listings}_listings"
    training = f"{catalog}.{schema}.{listings}_training_data"
    # overwriteSchema: a new gold column (such as city) must reach the copy too.
    spark.read.table(gold).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(training)
    spark.sql(
        f"ALTER TABLE {training} SET TBLPROPERTIES ("
        "'delta.deletedFileRetentionDuration' = 'interval 365 days', "
        "'delta.logRetentionDuration' = 'interval 365 days')"
    )
    version = spark.sql(f"DESCRIBE HISTORY {training} LIMIT 1").first()["version"]
    return training, version


def champion_predictions(client: MlflowClient, model_name: str, test, features: list[str]):
    """Predictions of the current champion, or None if there is no champion yet."""
    try:
        version = client.get_model_version_by_alias(model_name, CHAMPION)
    except MlflowException:
        return None, None
    model = mlflow.pyfunc.load_model(f"models:/{model_name}@{CHAMPION}")
    return point_estimate(model.predict(test[features])), version.version


def deploy_endpoint(endpoint_name: str, model_name: str, version: str):
    """Create the serving endpoint, or move it to the given model version if it serves another one."""
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
        current = w.serving_endpoints.get(endpoint_name).config.served_entities
        if current and current[0].entity_version == str(version):
            return
        w.serving_endpoints.update_config_and_wait(name=endpoint_name, served_entities=served)
    else:
        w.serving_endpoints.create_and_wait(
            name=endpoint_name,
            config=EndpointCoreConfigInput(name=endpoint_name, served_entities=served),
        )


def main():
    args = parse_args()
    spec = vehicle_model if args.listings == "vehicle" else property_model
    spark = SparkSession.builder.getOrCreate()
    client = MlflowClient()
    mlflow.set_experiment(args.experiment)
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    training_table, table_version = snapshot_training_data(spark, args.catalog, args.schema, args.listings)
    df = spec.prepare(spark.sql(f"SELECT * FROM {training_table} VERSION AS OF {table_version}").toPandas())
    train, test, cutoff, split_kind = split(df, args.test_days)

    with mlflow.start_run(run_name=f"retrain-{args.listings}") as run:
        mlflow.set_tag("listings", args.listings)
        mlflow.log_params(
            {
                "split": split_kind,
                "training_table": training_table,
                "training_table_version": table_version,
                "train_cutoff": cutoff.date().isoformat(),
                "test_days": args.test_days,
                "train_rows": len(train),
                "test_rows": len(test),
            }
        )

        baseline = metrics(test[TARGET], spec.baseline_predict(train, test))
        model, search_report = search(train, args.test_days, args.trials,
                                      features=spec.FEATURES, categorical=spec.CATEGORICAL)
        mlflow.log_params({"model": search_report["winner"], "trials": args.trials,
                           "valid_rows": search_report["valid_rows"]})
        for family, params in search_report["params"].items():
            mlflow.log_params({f"{family}.{k}": v for k, v in params.items()})
            mlflow.log_table(search_report["trials"][family], f"trials_{family}.json")
        mlflow.log_metrics({f"valid_mape_{k}": v for k, v in search_report["valid_mape"].items()})
        prediction = model.predict(test[spec.FEATURES])
        challenger = metrics(test[TARGET], prediction)
        mlflow.log_metrics({f"baseline_{k}": v for k, v in baseline.items()})
        mlflow.log_metrics({f"challenger_{k}": v for k, v in challenger.items()})
        for artifact, column in spec.BREAKDOWNS.items():
            mlflow.log_table(metrics_by(test, prediction, column), artifact)
        bands, default_band = error_bands(test, prediction, group=spec.BAND_GROUP)
        mlflow.log_dict({"by_type": bands, "default": default_band}, "error_bands.json")

        champion_pred, champion_version = champion_predictions(client, args.model_name, test, spec.FEATURES)
        champion = metrics(test[TARGET], champion_pred) if champion_pred is not None else None
        if champion:
            mlflow.log_metrics({f"champion_{k}": v for k, v in champion.items()})

        info = mlflow.pyfunc.log_model(
            name="model",
            python_model=BandedRentModel(model, spec.FEATURES, bands, default_band, spec.BAND_GROUP),
            # property_model.py holds Blend, which the average of the three model types is.
            code_paths=[os.path.join(HERE, "banded_model.py"), os.path.join(HERE, "property_model.py")],
            input_example=train[spec.FEATURES].head(5),
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
            print(f"Promoted version {new_version} (run {run.info.run_id}).")
        else:
            print(f"Kept champion version {champion_version}; challenger did not win.")
        # Also when the champion stays: an endpoint update that failed in an earlier run
        # (a Free Edition quota, Nov 2026) would otherwise leave it on an older version.
        if args.endpoint_name and (promote or champion_version):
            deploy_endpoint(args.endpoint_name, args.model_name, new_version if promote else champion_version)


if __name__ == "__main__":
    main()
