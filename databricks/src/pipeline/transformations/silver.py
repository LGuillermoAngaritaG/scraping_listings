from listings import SILVER_EXPECTATIONS, dedupe_daily, parse_listings
from pyspark import pipelines as dp


@dp.materialized_view(
    name="silver_listings",
    comment=(
        "Typed listings, one row per source, property and day. Rows with an impossible "
        "price or area are dropped. Prices are in Colombian pesos: monthly rent when "
        "operation is arriendo, sale price when it is venta."
    ),
)
@dp.expect_all_or_drop(SILVER_EXPECTATIONS)
def silver_listings():
    return dedupe_daily(parse_listings(spark.read.table("bronze_listings")))  # noqa: F821
