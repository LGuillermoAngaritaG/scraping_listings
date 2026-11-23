import glob
from pathlib import Path

import pytest
from pyspark.sql import SparkSession

from listings import CSV_OPTIONS, RAW_SCHEMA, add_file_columns

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(scope="session")
def spark():
    session = (
        SparkSession.builder.master("local[2]")
        .appName("inmuebles-tests")
        .config("spark.sql.ansi.enabled", "true")  # same default as Spark 4 on Databricks
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


def detail_files(*days: str) -> list[str]:
    """Real scraper files for the given days (YYYYMMDD), both sources."""
    files = []
    for day in days:
        files += sorted(glob.glob(str(DATA_DIR / f"*_details_{day}_*.csv")))
    assert files, f"no data files for {days}: restore the CSV history (Local tests in databricks/README.md)"
    return files


def read_bronze(spark, files: list[str]):
    """Same reader options as the pipeline, without Auto Loader."""
    raw = spark.read.options(**CSV_OPTIONS).schema(RAW_SCHEMA).csv(files)
    return add_file_columns(raw)


def one_file_per_day() -> list[str]:
    """First file of each source and day. Faster than all files, same listings."""
    first = {}
    for path in sorted(glob.glob(str(DATA_DIR / "*_details_*.csv"))):
        source, day = Path(path).name.split("_details_")[0], Path(path).name.split("_")[-2]
        first.setdefault((source, day), path)
    return list(first.values())


def vehicle_files() -> list[str]:
    """Real used-car files from the dealer scrapers."""
    files = sorted(glob.glob(str(DATA_DIR / "vehicles-*.csv")))
    assert files, "no vehicle data files: run `scrape-vehicles all` from the repository root"
    return files
