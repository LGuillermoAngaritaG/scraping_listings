import os

import pandas as pd

from scraping_listings.models.scraper import ScraperOutput


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
