# Rent price prediction on Databricks

Predicts the monthly rent of a property in Bogota from the listings this repo scrapes.
Built for Databricks Free Edition as a portfolio project.

```mermaid
flowchart LR
  GA["GitHub Actions<br/>scrapers, 7 runs a day"] -->|databricks fs cp| V[("UC volume<br/>csv_diarios")]
  V -->|Auto Loader| B[bronze_listings]
  B --> S[silver_listings]
  S --> G[gold_rent_listings]
  G --> T["retrain job<br/>monthly"]
  T -->|alias campeon| M[("UC model<br/>rent_price_model")]
  M --> E["Model Serving<br/>rent-price"]
  E --> A[Streamlit app]
  G --> MON["monitor job<br/>weekly"]
  MON -->|error above threshold| T
  MON --> P[rent_predictions]
  P --> D[AI/BI dashboard]
  G --> GN[Genie space]
```

## What is here

| Path | What it does |
|---|---|
| `databricks.yml`, `resources/` | Bundle: schema, volume, MLflow experiment, pipeline, 4 jobs |
| `src/pipeline/listings.py` | All parsing and cleaning logic (plain PySpark, no UDFs) |
| `src/pipeline/transformations/` | Lakeflow pipeline: bronze (Auto Loader), silver, gold |
| `src/ml/rent_model.py` | Features, LightGBM model, baseline, metrics, backtest |
| `src/ml/train.py` | Retrain job: challenger vs champion, promotion, serving endpoint |
| `src/ml/monitor.py` | Weekly error check, predictions table, starts retraining |
| `src/ml/backtest.py` | Error 1, 2 and 3 months after training |
| `app/` | Separate bundle for the Streamlit app |
| `dashboard/queries.sql` | Datasets for the AI/BI dashboard |
| `scripts/backfill.sh` | One-time upload of the CSV history in git |
| `tests/` | Local tests on the real CSV files |

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Ingestion | GitHub Action pushes each new CSV to a volume | Free Edition limits outbound internet |
| Incremental load | Auto Loader into bronze | Reads only new files |
| Catalog | `workspace` (Free Edition default) | Creating catalogs is unreliable on Free Edition |
| Parsing | Regex on the scraper's text field, built-in Spark functions only | No Python UDFs, so nothing to ship to executors |
| Deduplication | One row per source, property and day; a valid row wins, then the latest | Scrapers run 3 to 4 times a day |
| Models | Rent only | The scrapers collect rentals only. A sale model needs a sale scraper first |
| Target | First asking price, split by first seen date | The test never sees a price from the future |
| Algorithm | LightGBM on log(price) | Tabular data, wide price range, runs on CPU |
| Baseline | Median price of the neighborhood | Simple bar the model must beat |
| Promotion | New version gets alias `retador`; `campeon` moves only if MAPE beats both baseline and champion on the same last 30 days | Never promote a worse model. MAPE because rents go from 0.5 M to 30 M COP |
| Data version | Training copies gold into `rent_training_data` and logs its Delta version | Materialized views have no time travel |
| Daily trigger | Daily schedule | File arrival triggers are not confirmed on Free Edition. The swap is one line in `resources/jobs.yml` |
| Retraining | Monthly, plus weekly monitor that starts it when MAPE > 60% | The local backtest shows error grows slowly (48% to 53% in 3 months). Lower the threshold once area data is back |
| Serving | Endpoint created or updated from the training job | The endpoint needs a model version, which exists only after training |
| App | Own bundle, deployed after the first training | Its endpoint must exist at deploy time |

## Setup (once)

1. Create a Databricks Free Edition account. Verify your identity to raise limits.
2. Install the Databricks CLI and log in. Recent Homebrew versions ask you to trust the tap first (`brew trust databricks/tap`):
   ```bash
   brew tap databricks/tap && brew install databricks
   ```
   ```bash
   databricks auth login --host https://<your-workspace>.cloud.databricks.com
   ```
3. From the `databricks/` folder, deploy the bundle. It creates the schema `workspace.inmuebles`, the volume, the pipeline and the jobs. Run all later commands from this folder too:
   ```bash
   databricks bundle deploy
   ```
4. Upload the CSV history already in git:
   ```bash
   scripts/backfill.sh
   ```
5. Create a personal access token (Settings > Developer). Add two repository secrets in GitHub: `DATABRICKS_HOST` and `DATABRICKS_TOKEN`. From then on, each scraper run uploads its new file.
6. Build the tables, train the first model and run the backtest:
   ```bash
   databricks bundle run daily_refresh
   ```
   ```bash
   databricks bundle run retrain
   ```
   ```bash
   databricks bundle run backtest
   ```
7. Deploy the app (after step 6 created the `rent-price` endpoint):
   ```bash
   cd app && databricks bundle deploy && databricks bundle run rent_price_app
   ```
8. In the UI: build the dashboard from `dashboard/queries.sql`, and create a Genie space on `gold_rent_listings` and `rent_predictions`. Example questions: "median rent of apartments in Chico by month", "which neighborhoods are most expensive per m²".

## How to check each phase

| Phase | Check |
|---|---|
| Ingestion | `databricks fs ls dbfs:/Volumes/workspace/inmuebles/csv_diarios` shows today's files |
| Medallion | Pipeline graph shows dropped rows per rule; lineage is visible in Catalog Explorer. `SELECT source, property_id, scrape_date, COUNT(*) FROM workspace.inmuebles.silver_listings GROUP BY ALL HAVING COUNT(*) > 1` returns nothing |
| Daily job | Three days of `inmuebles-daily-refresh` runs with no manual action |
| Backtest | `backtest_results` table and the chart in the MLflow experiment |
| Training | At least three runs in the experiment; `challenger_mape` < `baseline_mape` |
| Retraining | One run with tag `promoted=True` and one with `promoted=False` |
| Monitoring | Set `--mape-threshold=0.01` in `resources/jobs.yml`, deploy, `databricks bundle run monitor`; it must start `inmuebles-retrain`. Set it back |
| Serving | `databricks serving-endpoints query rent-price --json '{"dataframe_records": [...]}'` returns a price |

## Local results (data up to 22 Nov 2026)

From the local tests (one file per source and day, same code as the jobs). Not yet run on Databricks.

| Check | Result |
|---|---|
| Test window | Listings first seen in the last 30 days: 1,424 rows; 7,580 for training |
| Model MAPE / MAE | 38% / 8.8 M COP |
| Baseline (median of the neighborhood) MAPE | 81% |
| MAPE by type | apartaestudio 21%, apartamento 26%, casa 31%, oficina 56%, bodega 60%, local comercial 69% |
| Backtest MAPE, 1 / 2 / 3 months after training | 48% / 51% / 53% (baseline about 100%) |

Most recent rows have no area: Metrocuadrado lost it from Feb to Nov 2026 and Fincaraiz saved 404 pages in the same months. Both scrapers are fixed, so error should drop as new data comes in. Commercial properties (offices, warehouses, shops) are the weak spot.

## Local tests

They run the same parsing, cleaning and model code on the real CSV files with a local Spark (needs Java 17+):

```bash
cd databricks && uv sync && uv run pytest -s
```

## Limits to keep in mind

- Free Edition: serverless only, one active pipeline per type, 5 concurrent job tasks, CPU serving only, up to 3 apps.
- Apps stop 24 hours after start or redeploy. Start it again before a demo: `databricks bundle run rent_price_app`.
- Over the daily quota, compute stops until the next day. Data is kept.
- Free Edition is for non-commercial use only.
- `databricks bundle destroy` deletes the schema with all tables and the volume.
