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
# Run a scraper by name (from config.yaml)
scrape fincaraiz-bogota-leasing
scrape metrocuadrado-bogota-leasing
```

### Output

Scraped data is saved to `data/` directory as CSV files:

```
data/
├── fincaraiz-bogota-leasing_pages_20241016_140530.csv
└── fincaraiz-bogota-leasing_details_20241016_140612.csv
```

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

Each site has one full entry. The other searches (other cities, sales) reuse its XPaths with a YAML merge key and change only the name and the URL. Names follow `<site>-<city>-<leasing|sale>`; the pipeline reads the city and the operation from them.

```yaml
  - <<: *fincaraiz
    name: fincaraiz-medellin-sale
    pages_url: https://www.fincaraiz.com.co/venta/medellin/antioquia
```

## GitHub Actions Automation

Two workflows run scrapers automatically, each with 8 searches (rent and sale in Bogota, Medellin, Envigado and Sabaneta). Each search runs as its own parallel job:

- **Fincaraiz**: Every 6 hours (see `.github/workflows/scrape-fincaraiz.yml`)
- **Metrocuadrado**: Every 8 hours (see `.github/workflows/scrape-metrocuadrado.yml`)

Scraped data is:
- Committed and pushed to the `data/` directory by one final job per run
- Uploaded as artifacts (30-day retention)
- Uploaded to a Databricks volume, when the `DATABRICKS_HOST` and `DATABRICKS_TOKEN` secrets exist

## Rent and Sale Price Prediction on Databricks

The scraped listings feed a Databricks pipeline (bronze, silver, gold), two price models (rent and
sale, each the best of three tuned model types or their average) with champion/challenger promotion,
two serving endpoints, a dashboard and a Streamlit app.
See [databricks/README.md](databricks/README.md) for the architecture, decisions, setup and
[screenshots of it running on Databricks](databricks/README.md#running-on-databricks).

![Databricks App estimate](databricks/docs/screenshots/app-estimate.png)

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
├── databricks/             # Databricks bundle: pipeline, model jobs, app
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
