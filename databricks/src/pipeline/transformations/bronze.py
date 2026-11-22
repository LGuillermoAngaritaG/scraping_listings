from pyspark import pipelines as dp

from listings import CSV_OPTIONS, RAW_SCHEMA, add_file_columns

VOLUME_PATH = spark.conf.get("volume_path")  # noqa: F821 (spark is provided by the pipeline)


@dp.table(
    name="bronze_listings",
    comment="Raw scraper rows as they arrive in the volume, plus source file and load time.",
)
def bronze_listings():
    raw = (
        spark.readStream.format("cloudFiles")  # noqa: F821
        .option("cloudFiles.format", "csv")
        .option("pathGlobFilter", "*_details_*.csv")
        .options(**CSV_OPTIONS)
        .schema(RAW_SCHEMA)
        .load(VOLUME_PATH)
    )
    return add_file_columns(raw)
