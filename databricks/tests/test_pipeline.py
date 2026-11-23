import pandas as pd
import pytest
from pyspark.sql import functions as F

from conftest import detail_files, read_bronze
from listings import build_listings, dedupe_daily, parse_listings, valid_rows

GOOD_DAY = "20251120"  # both scrapers worked
BROKEN_DAY = "20260615"  # Fincaraiz saved 404 pages, Metrocuadrado had no area


@pytest.fixture(scope="module")
def bronze(spark):
    return read_bronze(spark, detail_files(GOOD_DAY, BROKEN_DAY)).cache()


@pytest.fixture(scope="module")
def parsed(bronze):
    return parse_listings(bronze).cache()


def test_bronze_keeps_every_csv_row(bronze):
    files = detail_files(GOOD_DAY, BROKEN_DAY)
    expected = sum(len(pd.read_csv(f)) for f in files)
    assert bronze.count() == expected
    assert {r.source for r in bronze.select("source").distinct().collect()} == {
        "fincaraiz",
        "metrocuadrado",
    }


def share(rows, condition) -> float:
    return rows.filter(condition).count() / rows.count()


def test_good_day_fields_are_parsed(parsed):
    day = parsed.filter(F.col("scrape_date") == "2025-11-20")
    for source in ["fincaraiz", "metrocuadrado"]:
        rows = day.filter(F.col("source") == source)
        assert rows.count() > 0
        assert share(rows, valid_rows()) > 0.95, source
        assert share(rows, F.col("area_m2").isNotNull()) > 0.85, source
        assert share(rows, F.col("neighborhood").isNotNull()) > 0.95, source
        assert share(rows, F.col("property_type").isNotNull()) > 0.95, source
        assert share(rows, F.col("bathrooms").isNotNull()) > 0.8, source
        assert share(rows, F.col("estrato").isNotNull()) > 0.6, source


def test_broken_fincaraiz_rows_are_invalid(parsed):
    broken = parsed.filter((F.col("source") == "fincaraiz") & (F.col("scrape_date") == "2026-06-15"))
    assert broken.count() > 0
    assert broken.filter(valid_rows()).count() == 0


def test_silver_has_one_row_per_property_and_day(parsed):
    silver = dedupe_daily(parsed).filter(valid_rows())
    keys = ["source", "property_id", "scrape_date"]
    assert silver.count() == silver.select(*keys).distinct().count()


def test_layer_counts_reconcile(parsed):
    """bronze rows = silver rows + same-day duplicates + days with no valid row."""
    deduped = dedupe_daily(parsed)
    silver = deduped.filter(valid_rows()).count()
    duplicates = parsed.count() - deduped.count()
    dropped = deduped.filter(~valid_rows()).count()
    assert parsed.count() == silver + duplicates + dropped
    assert silver > 0 and duplicates > 0


def test_gold_has_one_row_per_listing(parsed):
    gold = build_listings(dedupe_daily(parsed).filter(valid_rows()), "arriendo")
    assert gold.count() == gold.select("source", "property_id").distinct().count()
    row = gold.filter(F.col("area_m2").isNotNull()).first()
    assert row.price_per_m2 == round(row.price_cop / row.area_m2)


def test_area_number_formats(spark):
    from listings import _area

    cases = [
        ("4.521 m2", 4521.0),
        ("60,5 m2", 60.5),
        ("1.234,5 m2", 1234.5),
        ("999.99  m2", 999.99),
        ("Área Construida 445.9m²", 445.9),
        ("54 |   | m²", 54.0),
        (None, None),
    ]
    df = spark.createDataFrame(cases, "text STRING, expected DOUBLE")
    assert [r.area for r in df.select(_area(F.col("text")).alias("area")).collect()] == [
        expected for _, expected in cases
    ]


SAMPLE_ROWS = {
    # file name -> (url, title, price[, location]): rows of each search kind, written as the scrapers do.
    "fincaraiz-medellin-sale_details_20261122_120000.csv": (
        "https://www.fincaraiz.com.co/apartamento-en-venta-en-calasanz-medellin/1",
        "Apartamento en Venta en Calasanz parte alta, Medellín", "$ 450.000.000",
    ),
    "metrocuadrado-envigado-sale_details_20261122_120000.csv": (
        "https://www.metrocuadrado.com/inmueble/venta-casa-envigado-zuniga/2",
        "Casa En Venta, Zuñiga", "$ 980.000.000",
    ),
    "fincaraiz-bogota-leasing_details_20261122_120000.csv": (
        "https://www.fincaraiz.com.co/casa-en-arriendo-en-lagos-de-torca-bogota/3",
        "Casa en Arriendo en Lagos de Torca, Bogotá", "$ 8.000.000",
    ),
    "metrocuadrado-sabaneta-leasing_details_20261122_120000.csv": (
        "https://www.metrocuadrado.com/inmueble/arriendo-apartamento-sabaneta/4?src_url=%2Finmuebles%2Fventa",
        "Apartamento En Arriendo, Aves Maria", "$ 450.000.000",  # a sale price on a rental
    ),
    # Fincaraiz in Antioquia: the title may name only the city; the barrio is in the location.
    "fincaraiz-sabaneta-leasing_details_20261122_120000.csv": (
        "https://www.fincaraiz.com.co/apartamento-en-arriendo-en-sabaneta/5",
        "Apartamento en Arriendo en Sabaneta", "$ 2.500.000", "Monteazul, Antioquia",
    ),
    # A new building project: its name comes first and the type is plural.
    "fincaraiz-medellin-sale_details_20261122_130000.csv": (
        "https://www.fincaraiz.com.co/proyecto-de-vivienda/6",
        "PATIO LIVING CENTRAL, Apartamentos en Venta en Laureles, Medellín", "$ 390.000.000",
    ),
}


@pytest.fixture(scope="module")
def samples(spark, tmp_path_factory):
    folder = tmp_path_factory.mktemp("samples")
    for name, (url, title, price, *location) in SAMPLE_ROWS.items():
        info = str({"title": title, "price": price, "area": "80 m²", "location": (location or [None])[0]})
        pd.DataFrame([{"url": url, "information": info, "date_time": "2026-11-22 12:00:00+00:00"}]).to_csv(
            folder / name, index=False
        )
    return parse_listings(read_bronze(spark, [str(folder / name) for name in SAMPLE_ROWS])).cache()


@pytest.mark.parametrize("property_id, city, operation, property_type, neighborhood, valid", [
    ("1", "medellin", "venta", "apartamento", "calasanz parte alta", True),
    ("2", "envigado", "venta", "casa", "zuniga", True),
    ("3", "bogota", "arriendo", "casa", "lagos de torca", True),
    ("4", "sabaneta", "arriendo", "apartamento", "aves maria", False),
    ("5", "sabaneta", "arriendo", "apartamento", "monteazul", True),
    ("6", "medellin", "venta", "apartamento", "laureles", True),
])
def test_city_sale_and_rent_rows_are_parsed(samples, property_id, city, operation, property_type, neighborhood, valid):
    row = samples.withColumn("valid", valid_rows()).filter(F.col("property_id") == property_id).first()
    assert (row.city, row.operation, row.property_type, row.neighborhood, row.valid) == (
        city, operation, property_type, neighborhood, valid
    )


def test_gold_tables_split_rent_and_sale(samples):
    silver = samples.filter(valid_rows())
    assert {r.property_id for r in build_listings(silver, "arriendo").collect()} == {"3", "5"}
    sale = build_listings(silver, "venta").collect()
    assert {(r.property_id, r.city) for r in sale} == {("1", "medellin"), ("2", "envigado"), ("6", "medellin")}
