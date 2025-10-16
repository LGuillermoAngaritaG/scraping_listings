# Property Scraper - Academic Research Project

> **ACADEMIC PURPOSE DISCLAIMER**
>
> This project was developed **solely for academic and educational purposes** to demonstrate web scraping techniques, software architecture patterns, and data extraction methodologies. This tool is intended to help students and researchers understand:
> - Web scraping fundamentals using Scrapy and Playwright
> - Config-driven application design
> - Handling modern web technologies (JavaScript rendering, Shadow DOM)
> - Data extraction and persistence patterns
> - CI/CD automation with GitHub Actions
>
> **This project is NOT intended for commercial use, large-scale data harvesting, or any activity that violates websites' Terms of Service.**

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [GitHub Actions Automation](#github-actions-automation)
- [Project Structure](#project-structure)
- [Ethical Considerations](#ethical-considerations)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

This is a **config-driven web scraper** designed to extract property listing data from Colombian real estate websites for academic research and market analysis studies. The scraper supports two powerful engines:

- **Scrapy**: Fast, efficient scraping for static HTML content
- **Playwright**: Browser automation for JavaScript-rendered pages and complex interactions

### What This Project Does

1. **Two-Phase Scraping Process**:
   - **Phase 1 (Pages)**: Crawls search result pages to collect listing URLs
   - **Phase 2 (Details)**: Visits each listing URL to extract detailed information

2. **Shadow DOM Support**: Advanced extraction from modern web components using custom elements (e.g., `<pt-main-specs>`)

3. **Flexible Configuration**: Define scrapers in YAML without modifying code

4. **Incremental Data Saving**: Streams data to CSV files as it scrapes (prevents data loss on failures)

5. **Automated Scheduling**: GitHub Actions workflows run scrapers automatically on schedules

### Currently Configured Sites

- **Fincaraiz** (Scrapy): Property listings in Bogota
- **Metrocuadrado** (Playwright): Property listings with modern UI components
- **Mercado Libre** (Playwright): Classified property ads

---

## Features

### Core Capabilities

- **Dual Engine Support**: Choose between Scrapy (speed) or Playwright (JavaScript support)
- **Config-Driven**: Define scrapers in `config.yaml` with XPath selectors
- **Pagination Handling**: Automatic page traversal with configurable limits
- **Shadow DOM Piercing**: Extract data from web components with encapsulated DOM
- **Incremental Saving**: Data saved continuously to prevent loss
- **Type Safety**: Pydantic models for validation
- **CI/CD Ready**: GitHub Actions workflows included
- **Artifact Storage**: Automatic upload of scraped data (30-day retention)

### Technical Highlights

- **Memory Efficient**: Iterator-based design for large datasets
- **Resilient**: Error handling with graceful degradation
- **Lazy Loading**: Automatic page scrolling to trigger dynamic content
- **Multi-Value Extraction**: Joins multiple XPath matches intelligently
- **Timestamp Tracking**: All scraped data includes UTC timestamps

---

## Architecture

### High-Level Design

```
config.yaml --> YamlLoader --> Scraper (main.py)
                                   |
                    +--------------+--------------+
                    |                             |
              Phase 1: Pages              Phase 2: Details
              Extract URLs                Extract Info
                    |                             |
            ScrapyScraper or              ScrapyScraper or
            PlaywrightScraper            PlaywrightScraper
                    |                             |
                    v                             v
        data/<name>_pages_<timestamp>.csv
        data/<name>_details_<timestamp>.csv
```

### Component Breakdown

1. **Models** (`app/models/scraper.py`)
   - `ScraperInput`: Configuration model (URLs, XPaths, pagination)
   - `ScraperOutput`: Output model (URL, extracted data, timestamp)
   - `SimpleInfo`: Single XPath to single value
   - `DynamicInfo`: XPath pairs to key-value lists

2. **Scrapers** (`app/scrapers/`)
   - `BaseScraper`: Abstract interface
   - `ScrapyScraper`: Scrapy implementation with crochet reactor management
   - `PlaywrightScraper`: Playwright with async/await, auto-scrolling

3. **Services** (`app/services/`)
   - `YamlLoaderListings`: Parses config.yaml
   - `data_saver`: Incremental CSV writing

4. **Utils** (`app/utils/`)
   - `shadow_dom_utils`: Shadow DOM-aware XPath resolution

---

## Prerequisites

- **Python 3.11+** (3.13 recommended)
- **uv** (fast Python package manager) or pip
- **Git** (for cloning and GitHub Actions)
- **Internet connection** (for scraping)

---

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/scraping_listings.git
cd scraping_listings
```

### 2. Install uv (Recommended)

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 3. Create Virtual Environment and Install Dependencies

```bash
# Using uv (recommended)
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -e .

# Or using pip
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 4. Install Playwright Browsers (if using Playwright scrapers)

```bash
playwright install chromium
playwright install-deps chromium
```

### 5. Verify Installation

```bash
scrape --help
```

---

## Configuration

Scrapers are defined in `config.yaml`. Each scraper has two phases:

### Example Configuration

```yaml
scrapers:
  - name: fincaraiz-bogota-leasing
    scrapying_engine: scrapy  # or playwright
    description: Scraping leasing properties from Fincaraiz in Bogota
    number_of_pages: 5  # 0 = infinite, 1 = no pagination, N = N pages
    base_url: https://www.fincaraiz.com.co
    pages_url: https://www.fincaraiz.com.co/arriendo/bogota/bogota-dc
    next_xpath: //*[@id="__next"]//li[last()]/a/@href  # Pagination link
    urls_xpath: //a[@class="lc-data"]/@href  # Listing URLs

    # Information to extract from detail pages
    information:
      - name: title
        xpath: //*[@id="__next"]//h1/text()

      - name: pricing
        xpath: //div[@class="property-price-tag"]//p[@class="main-price"]/text()

      - name: bedrooms
        xpath: //*[@id="__next"]//div[contains(@class,"property-typology")]//div[@class="typology-item-container"][1]/span/text()

      # Dynamic key-value extraction
      - name: details
        xpath_names: //*[@id="__next"]//div[contains(@class,"technical-sheet")]//span[@class="ant-typography"]/text()
        xpath_values: //*[@id="__next"]//div[2]/div/div/div/div[3]//text()
```

### Configuration Fields

| Field | Description | Required |
|-------|-------------|----------|
| `name` | Unique scraper identifier | Yes |
| `scrapying_engine` | `scrapy` or `playwright` | Yes |
| `description` | Human-readable description | No |
| `number_of_pages` | Pagination limit (0=infinite, 1=none, N=limit) | Yes |
| `base_url` | Base URL for relative links | No |
| `pages_url` | Starting URL for search pages | Yes |
| `next_xpath` | XPath to "next page" link/button | Yes |
| `urls_xpath` | XPath to extract listing URLs | Yes |
| `information` | List of fields to extract from detail pages | Yes |

### Information Types

**SimpleInfo** (single value):
```yaml
- name: title
  xpath: //h1/text()
```

**DynamicInfo** (key-value pairs):
```yaml
- name: features
  xpath_names: //dt/text()    # Keys: ["Bedrooms", "Bathrooms"]
  xpath_values: //dd/text()   # Values: ["3", "2"]
  # Result: [{"Bedrooms": "3"}, {"Bathrooms": "2"}]
```

---

## Usage

### Command Line

```bash
# Run a scraper by name (from config.yaml)
scrape fincaraiz-bogota-leasing

# Run another scraper
scrape metrocuadrado-bogota-leasing
```

### Programmatic Usage

```python
from app.main import Scraper

scraper = Scraper()

# Run scraper by name
scraper.scrape_from_yaml('fincaraiz-bogota-leasing')

# Or use custom configuration
from app.models.scraper import ScraperInput, SimpleInfo

config = ScraperInput(
    name="custom-scraper",
    urls=["https://example.com/search"],
    scrapying_engine="scrapy",
    number_of_pages=1,
    next_url_xpath="//a[@rel='next']/@href",
    information=[
        SimpleInfo(name="title", xpath="//h1/text()")
    ]
)

results = scraper.scrape_urls(config)
```

### Output Files

Scraped data is saved to the `data/` directory and automatically committed to the repository:

```
data/
├── fincaraiz-bogota-leasing_pages_20241016_140530.csv
├── fincaraiz-bogota-leasing_details_20241016_140612.csv
├── metrocuadrado-bogota-leasing_pages_20241016_150000.csv
└── metrocuadrado-bogota-leasing_details_20241016_150145.csv
```

**CSV Structure**:
- `url`: Scraped page URL
- `information`: JSON-serialized extracted data
- `date_time`: UTC timestamp of extraction

**Note**: When GitHub Actions runs the scrapers, CSV files are automatically committed and pushed to the repository for easy access.

---

## GitHub Actions Automation

Two workflows are included for automated scraping:

### Analyze Data in Colab

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/yourusername/scraping_listings/blob/main/analyze_artifacts.ipynb)

Use the Jupyter notebook above to analyze all scraped data from the repository. The notebook includes:
- Automatic repository cloning (in Colab) or local data access (in Jupyter)
- Data loading and merging with pandas
- Visualizations and statistics
- Export merged datasets

**Note**: Replace `yourusername` in the URL with your actual GitHub username.

### 1. Fincaraiz Scraper (`scrape-fincaraiz.yml`)
- **Schedule**: Every 6 hours (0:00, 6:00, 12:00, 18:00 UTC)
- **Triggers**: Push to main/master, schedule, manual
- **Timeout**: 30 minutes

### 2. Metrocuadrado Scraper (`scrape-metrocuadrado.yml`)
- **Schedule**: Every 8 hours (0:00, 8:00, 16:00 UTC)
- **Triggers**: Push to main/master, schedule, manual
- **Timeout**: 45 minutes

### Using GitHub Actions

1. **Enable Workflows**: Push `.github/workflows/*.yml` to your repo

2. **Manual Trigger**:
   - Go to **Actions** tab
   - Select workflow
   - Click **Run workflow**

3. **Access Data**:
   - **From Repository**: Scraped data is automatically committed to the `data/` directory
   - **From Artifacts**: Go to completed workflow run and download from **Artifacts** section (30-day retention)
   - **Analysis**: Use the Jupyter notebook to load and analyze all scraped data

### Workflow Features

- Automatic dependency installation (uv, Python, Playwright)
- Data uploaded as artifacts (always, even on failure)
- Scraped data committed and pushed to repository automatically
- Error resilience with `if: always()`

---

## Project Structure

```
scraping_listings/
├── .github/
│   └── workflows/           # GitHub Actions workflows
│       ├── scrape-fincaraiz.yml
│       └── scrape-metrocuadrado.yml
├── app/
│   ├── __init__.py
│   ├── main.py             # Entry point and orchestration
│   ├── models/
│   │   └── scraper.py      # Pydantic data models
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── base.py         # Abstract base scraper
│   │   ├── scrapy_scraper.py   # Scrapy implementation
│   │   └── playwright_scraper.py  # Playwright implementation
│   ├── services/
│   │   ├── yaml_loader.py  # Config parser
│   │   └── data_saver.py   # CSV writer
│   └── utils/
│       ├── __init__.py
│       └── shadow_dom_utils.py  # Shadow DOM utilities
├── data/                    # Output directory for scraped CSV files
│   └── .gitkeep            # Ensures directory exists in git
├── config.yaml              # Scraper configurations
├── pyproject.toml           # Project metadata and dependencies
├── analyze_artifacts.ipynb  # Jupyter notebook for data analysis
├── CLAUDE.md                # AI assistant guide
├── README.md                # This file
├── LICENSE                  # MIT License with academic notice
└── .gitignore
```

---

## Ethical Considerations

### Best Practices

1. **Respect robots.txt**: Always check the site's robots.txt file
   ```bash
   curl https://example.com/robots.txt
   ```

2. **Rate Limiting**: Use reasonable delays between requests
   - Scrapy: Configure `DOWNLOAD_DELAY` in settings
   - Playwright: Add `await asyncio.sleep()` between pages

3. **User-Agent**: Both scrapers identify themselves:
   - Scrapy: `Mozilla/5.0 (compatible; ScrapyScraper/1.0)`
   - Playwright: `Mozilla/5.0 (compatible; PlaywrightScraper/1.0)`

4. **Terms of Service**: Review each website's Terms of Service before scraping

5. **Data Privacy**: Do not scrape or store personal information (phone numbers, emails, names)

6. **Academic Use Only**: This project is for educational/research purposes

### Legal Disclaimer

**Important Legal Notice**:

- Web scraping may violate a website's Terms of Service
- Some jurisdictions have laws regulating automated access to websites
- The project maintainers are not responsible for misuse of this tool
- Users are solely responsible for ensuring their use complies with applicable laws
- Always seek permission from website owners when possible
- Respect copyright and intellectual property rights

**If you plan to use this tool, consult with a legal professional to ensure compliance with applicable laws in your jurisdiction.**

---

## Troubleshooting

### Common Issues

#### 1. Scrapy Reactor Error

```
Error: twisted.internet.error.ReactorAlreadyRunning
```

**Solution**: The crochet setup in `scrapy_scraper.py` should prevent this, but if it occurs:
- Restart your Python process
- Ensure you're not running multiple Scrapy scrapers simultaneously in the same process

#### 2. Playwright Browser Not Found

```
Error: Executable doesn't exist at /path/to/playwright
```

**Solution**:
```bash
playwright install chromium
playwright install-deps chromium
```

#### 3. XPath Returns No Results

**Debug steps**:
1. Open the page in a browser
2. Inspect the element
3. Test XPath in browser console:
   ```javascript
   $x("//your/xpath/here")
   ```
4. For Shadow DOM, check if element is inside `#shadow-root`
5. Update XPath in `config.yaml`

#### 4. GitHub Actions Workflow Fails

**Common causes**:
- Missing `data/` directory (Fixed by `mkdir -p data` step)
- Timeout too short (Increase `timeout-minutes` in workflow)
- Dependency installation fails (Check Python version should be 3.11+)

---

## Development

### Running Tests

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_scrapers.py

# Exclude slow tests
pytest -m "not slow"
```

### Code Quality

```bash
# Lint
ruff check .

# Format
ruff format .

# Type check
mypy app/
```

### Adding a New Scraper

1. Add configuration to `config.yaml`:
   ```yaml
   scrapers:
     - name: new-scraper
       scrapying_engine: scrapy
       # ... rest of config
   ```

2. Test XPaths in browser console

3. Run scraper:
   ```bash
   scrape new-scraper
   ```

4. (Optional) Create GitHub Actions workflow in `.github/workflows/`

---

## Contributing

Contributions are welcome for educational purposes! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/new-feature`)
3. Commit changes (`git commit -m 'Add new feature'`)
4. Push to branch (`git push origin feature/new-feature`)
5. Open a Pull Request

### Contribution Guidelines

- Maintain academic/educational focus
- Add tests for new features
- Follow existing code style (ruff format)
- Update documentation
- Respect ethical scraping practices

---

## License

This project is licensed under the **MIT License** - see the LICENSE file for details.

### MIT License Summary

- Free to use for academic/educational purposes
- Can modify and distribute
- No warranty or liability
- Must include copyright notice

---

## Acknowledgments

- **Scrapy**: Fast and powerful web scraping framework
- **Playwright**: Browser automation for modern web apps
- **Pydantic**: Data validation and settings management
- **uv**: Ultra-fast Python package installer

---

## Contact & Support

For academic collaboration or questions:

- **Issues**: [GitHub Issues](https://github.com/yourusername/scraping_listings/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/scraping_listings/discussions)

**Please remember**: This tool is for educational purposes. Use responsibly and ethically.

---

## Changelog

### v1.0.0 (2024-10-16)
- Initial release
- Scrapy and Playwright engine support
- Shadow DOM extraction utilities
- GitHub Actions automation
- Config-driven scraper design
- Incremental CSV saving
- Academic/educational focus

---

**Built with care for academic research and education**
