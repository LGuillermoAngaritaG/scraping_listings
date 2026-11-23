"""PySpark transformations for the used-car listings, in the same pipeline as the properties.

The vehicle scrapers (app/vehicles in the repo root) already map each dealer site to the
same columns and values, so silver only casts the types, checks the rules and keeps one
row per car and day. The files in transformations/ wrap these functions with Lakeflow
decorators; the tests run them with a local Spark session.
"""

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F

# The scraper's CSV columns, read as text: silver casts them.
VEHICLE_SCHEMA = (
    "source STRING, listing_id STRING, url STRING, title STRING, brand STRING, line STRING, "
    "version STRING, year STRING, km STRING, price_cop STRING, transmission STRING, fuel STRING, "
    "body_type STRING, engine_cc STRING, color STRING, plate_digit STRING, city STRING, "
    "branch STRING, scraped_at STRING"
)

# Rows that break a rule are dropped from silver. Used cars at these dealers cost from
# about 15 M to 1,000 M COP; the range leaves room on both sides.
VEHICLE_EXPECTATIONS = {
    "valid_listing_id": "listing_id IS NOT NULL AND listing_id != ''",
    "valid_price": "price_cop BETWEEN 5000000 AND 3000000000",
    "valid_year": "year BETWEEN 1980 AND year(scraped_at) + 1",
    "valid_km": "km IS NULL OR km BETWEEN 0 AND 1000000",
}

TEXT_COLUMNS = ["brand", "line", "transmission", "fuel", "body_type", "color", "city"]
FEATURE_COLUMNS = [
    "city", "brand", "line", "version", "year", "km", "transmission", "fuel", "body_type",
    "engine_cc", "color", "branch", "title",
]


def _whole(column: str, kind: str = "bigint") -> Column:
    """A whole number from text ("2023" or "2023.0"), or null when it is not a number."""
    return F.expr(f"try_cast(try_cast(trim({column}) AS DOUBLE) AS {kind})")


def _clean(text: Column) -> Column:
    lowered = F.regexp_replace(F.lower(F.trim(text)), r"\s+", " ")
    return F.nullif(F.translate(lowered, "áéíóúüñ", "aeiouun"), F.lit(""))


def parse_vehicles(bronze: DataFrame) -> DataFrame:
    """One typed row per scraped car."""
    scraped_at = F.try_to_timestamp("scraped_at")
    return bronze.select(
        _clean(F.col("source")).alias("source"),
        F.trim("listing_id").alias("listing_id"),
        scraped_at.alias("scraped_at"),
        F.to_date(scraped_at).alias("scrape_date"),
        *[_clean(F.col(column)).alias(column) for column in TEXT_COLUMNS],
        F.nullif(F.trim("version"), F.lit("")).alias("version"),
        _whole("year", "int").alias("year"),
        _whole("km").alias("km"),
        _whole("price_cop").alias("price_cop"),
        _whole("engine_cc", "int").alias("engine_cc"),
        F.nullif(F.trim("branch"), F.lit("")).alias("branch"),
        F.nullif(F.trim("title"), F.lit("")).alias("title"),
        "url",
        "source_file",
    )


def valid_vehicle_rows() -> Column:
    """True when a row passes every silver rule. Null counts as a failure."""
    rules = " AND ".join(f"({rule})" for rule in VEHICLE_EXPECTATIONS.values())
    return F.coalesce(F.expr(rules), F.lit(False))


def dedupe_vehicles_daily(vehicles: DataFrame) -> DataFrame:
    """Keep one row per car and day: a valid row wins over an invalid one, then the latest scrape."""
    order = Window.partitionBy("source", "listing_id", "scrape_date").orderBy(
        valid_vehicle_rows().desc(), F.col("scraped_at").desc()
    )
    return vehicles.withColumn("_rank", F.row_number().over(order)).filter("_rank = 1").drop("_rank")


def build_vehicle_listings(silver: DataFrame) -> DataFrame:
    """One row per car, ready for training and dashboards.

    price_cop is the first asking price, so it matches first_seen_date. The other
    columns take the latest known value, because a field can be missing on some days.
    """

    def latest_known(column: str) -> Column:
        when_known = F.when(F.col(column).isNotNull(), F.col("scraped_at"))
        return F.max_by(column, when_known).alias(column)

    return (
        silver.groupBy("source", "listing_id")
        .agg(
            F.min("scrape_date").alias("first_seen_date"),
            F.max("scrape_date").alias("last_seen_date"),
            F.count("*").alias("days_seen"),
            F.min_by("price_cop", "scraped_at").alias("price_cop"),
            F.max_by("price_cop", "scraped_at").alias("last_price_cop"),
            F.max_by("url", "scraped_at").alias("url"),
            *[latest_known(column) for column in FEATURE_COLUMNS],
        )
    )
