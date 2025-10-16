import pandas as pd
from models.scraper import ScraperOutput, ScraperInput
from datetime import datetime, timezone
import os


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