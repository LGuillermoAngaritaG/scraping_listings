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

## Installation

### 1. Clone and Setup

```bash
git clone https://github.com/yourusername/scraping_listings.git
cd scraping_listings
```

### 2. Install Dependencies

```bash
# Install uv (recommended)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install project
uv pip install -e .

# Install Playwright browsers (if using Playwright scrapers)
playwright install chromium
playwright install-deps chromium
```

### 3. Verify Installation

```bash
scrape --help
```

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

## Configuration

Scrapers are defined in `config.yaml`. Basic structure:

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
