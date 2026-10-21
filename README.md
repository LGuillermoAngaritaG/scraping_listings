# Property Scraper

> **DISCLAIMER**: This project is for academic and educational purposes only. It demonstrates web scraping techniques, config-driven design, and data extraction patterns. Not intended for commercial use or large-scale data harvesting. Users are responsible for complying with applicable laws and website Terms of Service.

## Overview

A config-driven web scraper for extracting property listing data from Colombian real estate websites. Supports two scraping engines:

- **Scrapy**: Fast scraping for static HTML content
- **Playwright**: Browser automation for JavaScript-rendered pages

### Features

- Two-phase scraping: extract URLs from search pages, then details from individual listings
- Config-driven: define scrapers in `config.yaml` with XPath selectors
- Automatic pagination handling
- Shadow DOM support for modern web components
- Incremental CSV saving (data saved continuously)
- GitHub Actions automation with scheduled runs

### Data Analysis

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/LGuillermoAngaritaG/scraping_listings/blob/main/analyze_artifacts.ipynb)

Use the Jupyter notebook to analyze scraped data with pandas, visualizations, and statistics.

## Quickstart

Use Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). Run from the repository
directory so the CLI can find `config.yaml` and the saved `data/` snapshots.

```bash
git clone https://github.com/Andesprit/scraping_listings.git
cd scraping_listings
uv sync --frozen --no-dev
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Needed for Playwright scrapers such as Metrocuadrado; Fincaraiz uses Scrapy
playwright install chromium
scrape --list
```

For a short first scrape, change `number_of_pages` to `1` in the
`fincaraiz-bogota-leasing` entry in `config.yaml`, then run:

```bash
scrape fincaraiz-bogota-leasing
scrape export
scrape search --max-price 3000000 --bedrooms 2 --where chapinero
```

The scrape summary tells you how many listings were saved, where the two raw CSVs are,
and which fields were filled. Search the exported listings directly, or open
`data/listings.csv` in a spreadsheet for numeric prices, room counts and areas.
The export includes the repository's historical snapshots;
it also works offline, so you can run it before your first scrape. A field with no values
makes the scrape exit **1**, but the saved data remains available for export.

On Linux, if Chromium reports missing system libraries, run
`playwright install-deps chromium`. If Chromium itself is missing, the scraper tells you
to run `playwright install chromium` in the activated environment. To install development
tools as well, use `uv sync --frozen --extra dev`.

Mercado Libre is currently **unsupported**: its search redirects to account verification
instead of listings. `scrape --list` shows that reason, and running its name stops with
the same explanation before opening a browser. Use Fincaraiz or Metrocuadrado instead.

## Usage

### Basic Command

```bash
# Discover configured names and descriptions (also shown by `scrape` with no args)
scrape --list

# Run a scraper by name (from config.yaml)
scrape fincaraiz-bogota-leasing
scrape metrocuadrado-bogota-leasing
```

Run these commands from the directory containing `config.yaml`; the CLI reads that file
from the current directory. A wrong name or invalid config explains what to fix before
scraping starts. If a search returns no listing URLs, check `urls_xpath` and the search
URL reported in the error.

### Output

Scraped data is saved to `data/` directory as CSV files:

```
data/
├── fincaraiz-bogota-leasing_pages_20241016_140530.csv
└── fincaraiz-bogota-leasing_details_20241016_140612.csv
```

Every completed run prints the number of listings saved, both CSV paths, and the filled
count for each configured field, for example `pricing: 20/20 filled`. The CSV columns
remain `url`, `information`, and `date_time`; `information` contains a Python dictionary
representation of the extracted fields, as in the historical data and analysis notebook.

A run exits **1** if any configured field is empty in every listing (or no listings were
saved). The summary names those fields and asks you to check their selectors. CSVs are
saved incrementally and remain available even when the run fails. Partially filled fields
are reported but do not by themselves fail the run. Healthy runs exit **0**. Scheduled
workflows still upload artifacts and commit saved data when a scrape fails, while marking
the scrape step as failed.

### Export a table for a spreadsheet or pandas

From the repository root, combine the saved detail snapshots into one CSV:

```bash
scrape export                         # writes data/listings.csv
scrape export --output /tmp/listings.csv
```

The generated `data/listings.csv` is git-ignored; scheduled workflows continue to commit
the raw snapshots only. Custom export paths are still supported.

This works offline and needs no `config.yaml` or browser. It reads
`data/*_details_*.csv`, leaves the raw files intact, and reports listings written,
counts per source, filled counts and percentages for every column, homepage rows
dropped, elapsed time, and the output path. An invalid input or unwritable output
gives a plain error and exits **1**; a completed export exits **0**. An existing
export is replaced only after the new CSV has been written successfully. A raw
pages or details CSV cannot be used as the output path.

| Columns | Meaning |
| --- | --- |
| `source`, `url` | Scraper name and the exact saved listing URL |
| `title`, `location` | Text from the latest valid snapshot |
| `price` | Numeric price in Colombian pesos |
| `bedrooms`, `bathrooms` | Whole-number counts, including Metrocuadrado's `main_specs` |
| `area_m2` | Numeric area in square metres |
| `estrato` | Residential stratum from 1 to 6; commercial stratum stays empty |
| `first_seen`, `last_seen` | Earliest and latest saved sightings of that URL, in UTC |

Each exact URL gets one row with the latest valid snapshot's values; missing fields
stay empty rather than being filled from older snapshots. Known Fincaraiz homepage
responses are counted and excluded. If a URL's newest response is a homepage, the
export uses its most recent valid snapshot instead. The sighting dates include
homepage responses, so `last_seen` records observation, not guaranteed availability.
Dates come from `date_time`, falling back to the filename timestamp when needed.
The export is a historical table, not a list of currently available properties.

Missing values, ambiguous ranges, and values with the wrong units stay empty.
For example, old Fincaraiz rows sometimes have an area in the bathrooms field;
the export does not turn `253.39 m²` into a bathroom count. It also does not infer
bedrooms from commercial `Ambiente` counts. Empty fields left by broken selectors
remain visible in the fill-rate summary. The original CSVs and analysis notebook
continue to use their existing format.

### Search saved listings

After `scrape export`, find matching properties without opening a spreadsheet:

```bash
scrape search --max-price 3000000 --bedrooms 2 --where chapinero
scrape search --max-price 2500000 --bedrooms 1 --where usaquen
scrape search --source fincaraiz-bogota-leasing --bathrooms 2 --min-area 60 --recent 365 --limit 5
```

Search prints **Data as of** the newest saved date, the total number of matches, and
up to 20 results sorted by price in COP, with rooms, area, title, location and links.
`--min-price`, `--max-price`, `--bedrooms`, `--bathrooms` and `--min-area` use inclusive
bounds; room counts and area are minimums. A missing number cannot satisfy that filter
and appears as `—` otherwise. `--where` matches a literal substring in location or title,
ignoring accents and case: `usaquen` also finds `Usaquén` and `USAQUEN`. `--source` uses
the exact scraper name. Use `scrape search --help` for all options.

`--recent` defaults to 30 days **before the newest date in the export**, including both
end dates; `--recent 0` searches only the newest day. This keeps historical snapshots
searchable even months later. The date describes saved observations, not current
availability. Location matches are text mentions, not geographic boundary checks.

No matches is a successful search (exit **0**). Search shows how many listings would match
if each active filter alone were removed, including the default recency filter, so you
can decide what to widen. Multiple restrictions may need widening together.

Search reads `data/listings.csv` by default; use `--input /tmp/listings.csv` for a custom
export. A missing export gives a one-line instruction to run `scrape export` and exits
**1**. If raw snapshots are newer than the export's modification time, search warns and
still uses that export. It never rebuilds automatically; exporting all historical data
can take several minutes. Search alone collapses repeated slashes in link paths and
tracking-query variants of the same property, keeping the newest row before filtering
and counting. Raw CSVs and the exported table retain their original URLs and rows.

## Configuration

Scrapers are defined in `config.yaml`. Basic structure:

An optional `unsupported: <reason>` marks a scraper as unavailable. The reason must be a
non-empty string; discovery displays it and runs stop immediately. Remove the key only
after verifying that the scraper works again.

```yaml
scrapers:
  - name: fincaraiz-bogota-leasing
    scrapying_engine: scrapy  # or playwright
    number_of_pages: 5  # 0=infinite, 1=no pagination, N=limit
    base_url: https://www.fincaraiz.com.co
    pages_url: https://www.fincaraiz.com.co/arriendo/bogota/bogota-dc
    next_xpath: //*[@id="__next"]//li[last()]/a/@href
    urls_xpath: //a[@class="lc-data"]/@href

    information:
      - name: title
        xpath: //*[@id="__next"]//h1/text()

      - name: pricing
        xpath: //div[@class="property-price-tag"]//p[@class="main-price"]/text()
```

## GitHub Actions Automation

Two workflows run scrapers automatically:

- **Fincaraiz**: Every 6 hours (see `.github/workflows/scrape-fincaraiz.yml`)
- **Metrocuadrado**: Every 8 hours (see `.github/workflows/scrape-metrocuadrado.yml`)

Scraped data is:
- Automatically committed and pushed to the `data/` directory
- Uploaded as artifacts (30-day retention)

### Manual Trigger

1. Go to **Actions** tab on GitHub
2. Select workflow
3. Click **Run workflow**



## Project Structure

```
scraping_listings/
├── .github/workflows/       # GitHub Actions
├── app/
│   ├── main.py             # Entry point
│   ├── models/             # Pydantic models
│   ├── scrapers/           # Scrapy & Playwright implementations
│   ├── services/           # Config loader, data saver
│   └── utils/              # Shadow DOM utilities
├── data/                   # Output CSV files
├── config.yaml             # Scraper configurations
├── analyze_artifacts.ipynb # Data analysis notebook
└── pyproject.toml          # Dependencies
```

## Ethical Considerations

- Always check `robots.txt` before scraping
- Use reasonable rate limiting
- Review website Terms of Service
- Do not scrape personal information
- This tool is for educational/research purposes only

**Users are solely responsible for ensuring their use complies with applicable laws.**

## License

MIT License - see LICENSE file for details.
