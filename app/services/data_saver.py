import pandas as pd
from app.models.scraper import ScraperOutput, ScraperInput
from datetime import datetime, timezone
import os
import ast
import csv


def save_data(name: str, data: list[ScraperOutput]):
    """
    Save data to CSV file (batch save).
    
    :param name: Name prefix for the output file
    :param data: List of ScraperOutput objects to save
    """
    df = pd.DataFrame([output.model_dump() for output in data])
    df.to_csv(f"data/{name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv", index=False)


def save_data_incremental(filename: str, data: ScraperOutput):
    """
    Save a single ScraperOutput to CSV incrementally.
    Creates file with headers on first write, appends without headers on subsequent writes.
    
    :param filename: Full path to the output CSV file
    :param data: Single ScraperOutput object to save
    """
    file_exists = os.path.exists(filename)
    df = pd.DataFrame([data.model_dump()])
    
    if file_exists:
        df.to_csv(filename, mode='a', header=False, index=False)
    else:
        df.to_csv(filename, mode='w', header=True, index=False)


def _has_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    return True


def print_run_summary(name: str, pages_filename: str, details_filename: str,
                      field_names: list[str]) -> int:
    """Report coverage from the saved CSV; return a failing status for empty fields."""
    filled = dict.fromkeys(field_names, 0)
    total = 0
    # Both engines save incrementally and return no rows, so inspect the saved data.
    if os.path.exists(details_filename):
        with open(details_filename, newline="") as file:
            for row in csv.DictReader(file):
                information = ast.literal_eval(row["information"])
                total += 1
                for field in filled:
                    if _has_value(information.get(field)):
                        filled[field] += 1

    print(f"Run summary for {name}: {total} listings saved")
    print(f"Pages CSV: {pages_filename}")
    print(f"Details CSV: {details_filename}")
    for field, count in filled.items():
        warning = " (WARNING: empty in every listing; check this field's selector)" if count == 0 else ""
        print(f"  {field}: {count}/{total} filled{warning}")
    if total == 0:
        print("WARNING: no listings saved; check the listing URLs and selectors.")
    return int(total == 0 or any(count == 0 for count in filled.values()))
