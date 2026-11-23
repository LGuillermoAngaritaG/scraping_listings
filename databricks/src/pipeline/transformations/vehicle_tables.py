from pyspark import pipelines as dp
from pyspark.sql import functions as F

from listings import CSV_OPTIONS
from vehicles import (
    VEHICLE_EXPECTATIONS,
    VEHICLE_SCHEMA,
    build_vehicle_listings,
    dedupe_vehicles_daily,
    parse_vehicles,
)

VEHICLE_VOLUME_PATH = spark.conf.get("vehicle_volume_path")  # noqa: F821 (spark is provided by the pipeline)


@dp.table(
    name="bronze_vehicles",
    comment="Raw used-car rows from the dealer scrapers as they arrive in the volume, plus source file and load time.",
)
def bronze_vehicles():
    raw = (
        spark.readStream.format("cloudFiles")  # noqa: F821
        .option("cloudFiles.format", "csv")
        .option("pathGlobFilter", "vehicles-*.csv")
        .options(**CSV_OPTIONS)
        .schema(VEHICLE_SCHEMA)
        .load(VEHICLE_VOLUME_PATH)
    )
    return raw.select(
        "*",
        F.col("_metadata.file_path").alias("source_file"),
        F.col("_metadata.file_modification_time").alias("file_modified_at"),
        F.current_timestamp().alias("ingested_at"),
    )


@dp.materialized_view(
    name="silver_vehicles",
    comment=(
        "Typed used-car listings, one row per dealer, car and day. Rows with an impossible "
        "price, year or mileage are dropped. Prices are asking prices in Colombian pesos."
    ),
)
@dp.expect_all_or_drop(VEHICLE_EXPECTATIONS)
def silver_vehicles():
    return dedupe_vehicles_daily(parse_vehicles(spark.read.table("bronze_vehicles")))  # noqa: F821


@dp.materialized_view(
    name="gold_vehicle_listings",
    comment=(
        "One row per used car. price_cop is the first asking price in Colombian pesos, "
        "last_price_cop the latest one. first_seen_date and last_seen_date say when the car "
        "was online. source is the dealer, brand and line the make and model, km the mileage, "
        "engine_cc the engine size, city where the car is."
    ),
)
def gold_vehicle_listings():
    return build_vehicle_listings(spark.read.table("silver_vehicles"))  # noqa: F821
