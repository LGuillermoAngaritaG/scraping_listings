-- Datasets for the AI/BI dashboard. Paste each query as a dataset in the
-- dashboard editor, then save the dashboard into the repo with:
--   databricks bundle generate dashboard --existing-id <dashboard_id>

-- 1. Error by zone: only listings the champion did not see in training.
SELECT
  neighborhood,
  COUNT(*) AS listings,
  ROUND(AVG(ABS(last_price_cop - predicted_price_cop) / last_price_cop), 3) AS mape
FROM workspace.inmuebles.rent_predictions
WHERE first_seen_date > train_cutoff
GROUP BY neighborhood
HAVING COUNT(*) >= 10
ORDER BY mape DESC;

-- 2. Price evolution: median rent and rent per m² of new listings per month.
SELECT
  DATE_TRUNC('month', first_seen_date) AS month,
  property_type,
  COUNT(*) AS new_listings,
  MEDIAN(price_cop) AS median_rent_cop,
  MEDIAN(price_per_m2) AS median_rent_per_m2
FROM workspace.inmuebles.gold_rent_listings
WHERE property_type IN ('apartamento', 'apartaestudio', 'casa')
GROUP BY ALL
ORDER BY month;

-- 3. Listings priced far above or below the model estimate.
SELECT
  neighborhood, property_type, area_m2, bedrooms, estrato,
  last_price_cop, predicted_price_cop,
  ROUND(price_gap_pct, 3) AS price_gap_pct,
  CASE WHEN price_gap_pct > 0 THEN 'above estimate' ELSE 'below estimate' END AS position,
  url
FROM workspace.inmuebles.rent_predictions
WHERE ABS(price_gap_pct) >= 0.3
ORDER BY ABS(price_gap_pct) DESC
LIMIT 100;

-- 4. Weekly model error (monitoring job).
SELECT checked_at, model_version, rows, mape, mape_threshold, retrain_triggered
FROM workspace.inmuebles.model_monitoring
ORDER BY checked_at;

-- 5. Backtest: mean error by months after training.
SELECT horizon_months, ROUND(AVG(mape), 3) AS model_mape, ROUND(AVG(baseline_mape), 3) AS baseline_mape
FROM workspace.inmuebles.backtest_results
GROUP BY horizon_months
ORDER BY horizon_months;
