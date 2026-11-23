from listings import build_listings
from pyspark import pipelines as dp

COLUMNS = (
    "first_seen_date and last_seen_date say when the listing was online. city is bogota, "
    "medellin, envigado or sabaneta, neighborhood is the barrio, estrato is the "
    "socioeconomic level (1 to 6), age_years is the building age."
)


@dp.materialized_view(
    name="gold_rent_listings",
    comment=(
        "One row per rental listing. price_cop is the first monthly asking price in "
        "Colombian pesos, last_price_cop the latest one. " + COLUMNS
    ),
)
def gold_rent_listings():
    return build_listings(spark.read.table("silver_listings"), "arriendo")  # noqa: F821


@dp.materialized_view(
    name="gold_sale_listings",
    comment=(
        "One row per listing for sale. price_cop is the first asking sale price in "
        "Colombian pesos, last_price_cop the latest one. " + COLUMNS
    ),
)
def gold_sale_listings():
    return build_listings(spark.read.table("silver_listings"), "venta")  # noqa: F821
