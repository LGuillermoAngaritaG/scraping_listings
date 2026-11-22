from pyspark import pipelines as dp

from listings import build_rent_listings


@dp.materialized_view(
    name="gold_rent_listings",
    comment=(
        "One row per rental listing in Bogota. price_cop is the first monthly asking "
        "price in Colombian pesos, last_price_cop the latest one. first_seen_date and "
        "last_seen_date say when the listing was online. neighborhood is the barrio, "
        "estrato is the socioeconomic level (1 to 6), age_years is the building age."
    ),
)
def gold_rent_listings():
    return build_rent_listings(spark.read.table("silver_listings"))  # noqa: F821
