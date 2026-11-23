"""PySpark transformations for the listings pipeline.

The files in transformations/ wrap these functions with Lakeflow decorators.
Keeping the logic here lets the tests run it with a local Spark session.
Only built-in Spark functions are used (no Python UDFs).
"""

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F

RAW_SCHEMA = "url STRING, information STRING, date_time STRING"

# pandas writes a quote inside a field as "" and a field can span several lines.
CSV_OPTIONS = {"header": "true", "multiLine": "true", "escape": '"'}

# Rows that break a rule are dropped from silver. Area may be missing:
# Metrocuadrado did not publish it from Feb to Nov 2026. A price is a monthly rent
# (0.2 M to 200 M COP) or a sale price (20 M to 50,000 M COP).
SILVER_EXPECTATIONS = {
    "valid_property_id": "property_id IS NOT NULL AND property_id != ''",
    "valid_price": (
        "price_cop IS NOT NULL AND CASE WHEN operation = 'venta' "
        "THEN price_cop BETWEEN 20000000 AND 50000000000 ELSE price_cop BETWEEN 200000 AND 200000000 END"
    ),
    "valid_area": "area_m2 IS NULL OR area_m2 BETWEEN 10 AND 5000",
}

FEATURE_COLUMNS = [
    "city",
    "property_type",
    "neighborhood",
    "area_m2",
    "bedrooms",
    "bathrooms",
    "parking",
    "estrato",
    "age_years",
]


def add_file_columns(raw: DataFrame) -> DataFrame:
    """Add the source file, the source site and the load time to raw CSV rows."""
    return (
        raw.select(
            "*",
            F.col("_metadata.file_path").alias("source_file"),
            F.col("_metadata.file_modification_time").alias("file_modified_at"),
        )
        # File names look like metrocuadrado-bogota-leasing_details_20251120_002310.csv
        .withColumn("source", F.regexp_extract("source_file", r"/([a-z]+)-[^/]*$", 1))
        .withColumn("ingested_at", F.current_timestamp())
    )


def _value(info: Column, key: str) -> Column:
    """Read one value from the `information` text.

    The scraper saves a Python dict as text, for example
    {'title': 'Apartamento En Arriendo, Chico', 'area': None, ...}.
    Python quotes a value with " when it contains a ', so both forms are read.
    """
    pattern = rf"""'{key}': (?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")"""
    return F.coalesce(
        F.nullif(F.regexp_extract(info, pattern, 1), F.lit("")),
        F.nullif(F.regexp_extract(info, pattern, 2), F.lit("")),
    )


def _number(text: Column, pattern: str = r"(\d+(?:\.\d+)?)") -> Column:
    """First number in the text that matches the pattern, or null."""
    return F.nullif(F.regexp_extract(text, pattern, 1), F.lit("")).cast("double")


def _area(text: Column) -> Column:
    """Area in m² from mixed number formats.

    The sites write '4.521 m2' (dot for thousands), '60,5 m2' (comma for decimals),
    '445.9m²' and '1.234,5 m2'. A comma is always the decimal mark. A dot followed
    by exactly three digits is a thousands mark; any other dot is a decimal mark.
    """
    token = F.regexp_extract(text, r"(\d[\d.,]*\d|\d)", 1)
    normalized = (
        F.when(token.contains(","), F.regexp_replace(F.regexp_replace(token, r"\.", ""), ",", "."))
        .when(token.rlike(r"^\d{1,3}(\.\d{3})+$"), F.regexp_replace(token, r"\.", ""))
        .otherwise(token)
    )
    return F.nullif(normalized, F.lit("")).cast("double")


def _money(text: Column) -> Column:
    """'$ 3.800.000' -> 3800000. Anything else (several prices, empty) -> null."""
    return F.when(text.rlike(r"^\$ ?[\d.]+$"), F.regexp_replace(text, r"[^\d]", "").cast("long"))


def _age_years(text: Column) -> Column:
    """Turn the age ranges of both sites into an approximate number of years.

    'Entre 5 y 10 años' and '9 a 15 años' -> middle of the range.
    'Más de 20 años' -> 20. 'menor a 1 año' -> 0.5.
    """
    low = _number(text, r"(\d+)")
    high = _number(text, r"\d+\D+(\d+)")
    return (
        F.when(high.isNotNull(), (low + high) / 2)
        .when(F.lower(text).contains("menor"), low / 2)
        .otherwise(low)
    )


def _clean_text(text: Column) -> Column:
    lowered = F.lower(F.trim(text))
    no_accents = F.translate(lowered, "áéíóúüñ", "aeiouun")
    return F.trim(F.regexp_replace(no_accents, r"\s+zona urbana$", ""))


OPERATION = r"(?:[Aa]rriendo|[Vv]enta)"


def parse_listings(bronze: DataFrame) -> DataFrame:
    """One typed row per scraped listing. Both sites end up with the same columns."""
    info = F.col("information")
    title = _value(info, "title")
    specs = _value(info, "main_specs")  # Metrocuadrado: '2 hab. | 2 bañ. | 1 par.'

    # Fincaraiz keeps most fields in a list of {'name': 'value'} pairs, which
    # _value also reads. Metrocuadrado uses lowercase keys.
    # A project title starts with its name and uses plurals:
    # 'PATIO LIVING CENTRAL, Apartamentos en Venta en Laureles, Medellín'.
    property_type = F.coalesce(
        _value(info, "Tipo de Inmueble"),
        F.regexp_replace(
            F.regexp_extract(title, rf"^(?:.*,\s*)?(.+?)\s+[Ee]n\s+{OPERATION}", 1),
            r"^(Apartamento|Apartaestudio|Casa|Oficina|Bodega|Lote)s$", "$1",
        ),
    )
    # Metrocuadrado: 'Casa En Venta, Zuñiga'. Fincaraiz: 'Casa en Arriendo en Lagos de Torca, Bogotá'.
    # Fincaraiz in Antioquia may name only the city ('Apartamento en Arriendo en Sabaneta');
    # then the barrio is in the location: 'Monteazul, Antioquia'.
    neighborhood = F.coalesce(
        F.nullif(F.regexp_extract(title, rf"[Ee]n\s+{OPERATION},\s*(.+)$", 1), F.lit("")),
        F.nullif(F.regexp_extract(title, rf"[Ee]n\s+{OPERATION}\s+en\s+(.+?),[^,]*$", 1), F.lit("")),
        F.nullif(F.regexp_extract(_value(info, "location"), r"^([^,]+),\s*Antioquia$", 1), F.lit("")),
    )
    # First value that is a number: Fincaraiz may write '¡Pregúntale!' for one of them.
    area = F.coalesce(
        _area(_value(info, "Área Construida")),
        _area(_value(info, "Área Privada")),
        _area(_value(info, "area")),
    )
    estrato = _number(F.coalesce(_value(info, "estrato"), _value(info, "Estrato")), r"(\d)")

    # The file name says which search found the listing: fincaraiz-medellin-sale_details_...csv.
    # The city is the city searched; each search is rentals (leasing) or sales.
    file = F.col("source_file")
    return bronze.select(
        "source",
        F.regexp_extract(file, r"/[a-z]+-([a-z]+)-[^/]*$", 1).alias("city"),
        F.regexp_extract("url", r"/([^/?]+)(?:\?.*)?$", 1).alias("property_id"),
        F.when(file.rlike(r"-sale_[^/]*$"), "venta").when(file.rlike(r"-leasing_[^/]*$"), "arriendo").alias("operation"),
        F.try_to_timestamp("date_time").alias("scraped_at"),
        F.to_date(F.try_to_timestamp("date_time")).alias("scrape_date"),
        F.nullif(_clean_text(property_type), F.lit("")).alias("property_type"),
        F.nullif(_clean_text(neighborhood), F.lit("")).alias("neighborhood"),
        _money(F.coalesce(_value(info, "price"), _value(info, "pricing"))).alias("price_cop"),
        area.alias("area_m2"),
        F.coalesce(
            _number(specs, r"(\d+) hab"), _number(_value(info, "Habitaciones"), r"(\d+)")
        ).cast("int").alias("bedrooms"),
        F.coalesce(
            _number(specs, r"(\d+) bañ"), _number(_value(info, "Baños"), r"(\d+)")
        ).cast("int").alias("bathrooms"),
        F.coalesce(
            _number(specs, r"(\d+) par"), _number(_value(info, "Parqueaderos"), r"(\d+)")
        ).cast("int").alias("parking"),
        F.when(estrato.between(1, 6), estrato).cast("int").alias("estrato"),
        _age_years(F.coalesce(_value(info, "antiguedad"), _value(info, "Antigüedad"))).alias(
            "age_years"
        ),
        "url",
        "source_file",
    )


def valid_rows() -> Column:
    """True when a row passes every silver rule. Null counts as a failure."""
    rules = " AND ".join(f"({rule})" for rule in SILVER_EXPECTATIONS.values())
    return F.coalesce(F.expr(rules), F.lit(False))


def dedupe_daily(listings: DataFrame) -> DataFrame:
    """Keep one row per property and day.

    The scrapers run 3 to 4 times a day. A valid row wins over an invalid one,
    then the latest scrape wins.
    """
    order = Window.partitionBy("source", "property_id", "scrape_date").orderBy(
        valid_rows().desc(), F.col("scraped_at").desc()
    )
    return listings.withColumn("_rank", F.row_number().over(order)).filter("_rank = 1").drop("_rank")


def build_listings(silver: DataFrame, operation: str) -> DataFrame:
    """One row per listing of one operation (arriendo or venta), ready for training and dashboards.

    price_cop is the first asking price, so it matches first_seen_date and the
    temporal split never uses a price from the future. Features take the latest
    known value, because a field can be missing on some days.
    """

    def latest_known(column: str) -> Column:
        when_known = F.when(F.col(column).isNotNull(), F.col("scraped_at"))
        return F.max_by(column, when_known).alias(column)

    return (
        silver.filter(F.col("operation") == operation)
        .groupBy("source", "property_id")
        .agg(
            F.min("scrape_date").alias("first_seen_date"),
            F.max("scrape_date").alias("last_seen_date"),
            F.count("*").alias("days_seen"),
            F.min_by("price_cop", "scraped_at").alias("price_cop"),
            F.max_by("price_cop", "scraped_at").alias("last_price_cop"),
            F.max_by("url", "scraped_at").alias("url"),
            *[latest_known(column) for column in FEATURE_COLUMNS],
        )
        .withColumn("price_per_m2", F.round(F.col("price_cop") / F.col("area_m2")))
    )
