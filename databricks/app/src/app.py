"""Estimate the monthly rent or the sale price of a property with a serving endpoint, and show similar listings."""

import html
import os
import time
from urllib.parse import quote

import altair as alt
import pandas as pd
import streamlit as st
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service.sql import StatementParameterListItem, StatementState

WAREHOUSE_ID = os.environ["SQL_WAREHOUSE_ID"]
# The training runs of both models: their metrics fill the "How it was built" tab.
EXPERIMENT_ID = os.environ["MLFLOW_EXPERIMENT_ID"]
# Each mode has its own model endpoint, listings table and daily good-deal flags.
MODES = {
    "rent": {"endpoint": os.environ["SERVING_ENDPOINT"], "listings": os.environ["LISTINGS_TABLE"],
             "predictions": os.environ["PREDICTIONS_TABLE"], "label": "Monthly rent", "unit": "COP per month",
             "button": "Estimate rent"},
    "sale": {"endpoint": os.environ["SALE_SERVING_ENDPOINT"], "listings": os.environ["SALE_LISTINGS_TABLE"],
             "predictions": os.environ["SALE_PREDICTIONS_TABLE"], "label": "Sale price", "unit": "COP",
             "button": "Estimate sale price"},
}
CITIES = {"bogota": "Bogota", "medellin": "Medellin", "envigado": "Envigado", "sabaneta": "Sabaneta"}
DEFAULT_ZONES = ["chico norte", "el poblado"]
ANY_ZONE = ""  # the user did not pick a barrio
# What training uses for a listing without a barrio (rent_model.prepare), so the model knows it.
UNKNOWN_ZONE = "unknown"
# Values as they appear for Metrocuadrado, the source the app sends.
PROPERTY_TYPES = [
    "apartamento", "apartaestudio", "casa", "oficina", "local comercial", "consultorio", "bodega"
]
MIN_ZONE_LISTINGS = 3
SQL_TYPES = {int: "INT", float: "DOUBLE"}
# The endpoint scales to zero; waking it can take minutes. The SDK default waits only 60 seconds.
MODEL_WAKE_SECONDS = 600

# Colors shared with the dashboard and .streamlit/config.toml.
INK, MUTED, SURFACE = "#161615", "#52514e", "#fcfcfb"
BLUE, DEEP_BLUE, BAND, ORANGE = "#2a78d6", "#1c5cab", "#b7d3f6", "#eb6834"

LINE = "#e2e0d9"
STYLE = f"""<style>
[data-testid="stMainBlockContainer"] {{max-width: 1200px; padding-top: 2.25rem; padding-bottom: 2.5rem;}}
::selection {{background: {BAND}; color: {INK};}}
.label {{font-size: .75rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: {MUTED};}}
.appbar {{display: flex; justify-content: space-between; align-items: flex-end; gap: .75rem 2rem; flex-wrap: wrap;
          margin-bottom: .5rem;}}
.appbar h1 {{font-size: 1.875rem; font-weight: 700; letter-spacing: -.025em; line-height: 1.2; margin: 0; padding: 0;
            color: {INK};}}
.appbar p {{font-size: 1rem; color: {MUTED}; max-width: 46rem; line-height: 1.5; margin: .35rem 0 0;}}
.status {{font-size: .85rem; color: {MUTED}; font-variant-numeric: tabular-nums;}}
.status b {{color: {INK}; font-weight: 600;}}
.stTabs [role="tablist"] {{gap: 1.75rem; border-bottom: 1px solid {LINE};}}
.stTabs [role="tab"] {{height: 2.75rem; padding: 0;}}
.stTabs [role="tab"] p {{font-size: .95rem; font-weight: 600;}}
.stTabs [role="tabpanel"] {{padding-top: 1.5rem;}}
.sub, .note, .card-seen {{color: {MUTED};}}
.estimate {{font-size: 3.5rem; font-weight: 800; letter-spacing: -.04em; line-height: 1;
           font-variant-numeric: tabular-nums; margin: .5rem 0 .6rem; color: {INK};}}
.estimate.empty {{color: #c9c7bf;}}
.sub {{font-size: .95rem;}}
.note {{font-size: .9rem; line-height: 1.55; margin: 0;}}
.note b {{color: {INK};}}
.legend {{display: flex; flex-wrap: wrap; gap: 1.25rem; font-size: .8rem; color: {MUTED};}}
.legend i {{display: inline-block; width: 14px; height: 10px; border-radius: 3px; margin-right: .4rem;
           vertical-align: middle;}}
.legend i.tick {{width: 3px; height: 14px; border-radius: 1px;}}
.legend i.dot {{width: 10px; height: 10px; border-radius: 50%;}}
.card-price {{font-size: 1.35rem; font-weight: 700; letter-spacing: -.02em; font-variant-numeric: tabular-nums;
             color: {INK};}}
.card-seen {{font-size: .8rem;}}
.deal {{display: inline-block; font-size: .75rem; font-weight: 600; color: #006300; background: #e2f3e2;
       border-radius: 999px; padding: .1rem .55rem; margin-top: .3rem;}}
.card-facts {{font-size: .9rem; line-height: 1.5; margin: .6rem 0 .4rem; color: {INK};}}
[class*="st-key-card"] {{background: {SURFACE};
    box-shadow: 0 1px 2px rgba(22, 22, 21, .04), 0 6px 20px rgba(22, 22, 21, .05);}}
.section h2 {{font-size: 1.25rem; font-weight: 700; letter-spacing: -.015em; line-height: 1.3; margin: 0;
             padding: 0; color: {INK};}}
.section p {{font-size: .95rem; color: {MUTED}; max-width: 70ch; line-height: 1.55; margin: .3rem 0 0;}}
dl.facts {{display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 1rem 2rem; margin: 0;
         padding: 0;}}
.facts dt {{font-size: .75rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: {MUTED};}}
.facts dd {{font-size: .9rem; line-height: 1.5; color: {INK}; margin: .2rem 0 0;}}
ul.points {{margin: 0; padding-left: 1.1rem; max-width: 75ch;}}
.points li {{font-size: .95rem; line-height: 1.55; color: {INK}; margin: .35rem 0;}}
.counts {{border-collapse: collapse; font-size: .9rem; font-variant-numeric: tabular-nums; min-width: 22rem;}}
.counts th {{font-size: .75rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: {MUTED};
            text-align: right; padding: 0 0 .5rem 1.5rem; border-bottom: 1px solid {LINE};}}
.counts td {{text-align: right; padding: .45rem 0 .45rem 1.5rem; color: {INK}; border-bottom: 1px solid {LINE};}}
.counts th:first-child, .counts td:first-child {{text-align: left; padding-left: 0;}}
.how input {{position: absolute; opacity: 0; pointer-events: none;}}
.pipe {{display: flex; align-items: stretch; margin: 1rem 0;}}
.pipe label {{flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; gap: .55rem; cursor: pointer;
             background: {SURFACE}; border: 1px solid {LINE}; border-radius: 12px; padding: .9rem;
             transition: border-color 150ms ease-out, background-color 150ms ease-out;}}
.pipe label:hover {{border-color: #c9c7bf;}}
.pipe .icon {{width: 36px; height: 36px; border-radius: 9px; display: grid; place-items: center;
             background: #ecebe6; color: {INK}; transition: background-color 150ms ease-out, color 150ms ease-out;}}
.glyph {{display: block; width: 20px; height: 20px; background-color: currentColor;
         -webkit-mask: var(--glyph) center / contain no-repeat; mask: var(--glyph) center / contain no-repeat;}}
.pipe .link .glyph {{width: 16px; height: 16px;}}
.pipe .name {{font-size: .95rem; font-weight: 600; color: {INK}; line-height: 1.25;}}
.pipe .what {{font-size: .8rem; color: {MUTED}; line-height: 1.4;}}
.pipe .link {{flex: 0 0 1.5rem; display: grid; place-items: center; color: #a3a19a;}}
.panels .panel {{display: none; background: {SURFACE}; border-radius: 12px; padding: 1.5rem 1.75rem;
                box-shadow: 0 1px 2px rgba(22, 22, 21, .04), 0 6px 20px rgba(22, 22, 21, .05);}}
.panel h3 {{font-size: 1.15rem; font-weight: 700; letter-spacing: -.01em; margin: 0; padding: 0; color: {INK};}}
.panel > p {{font-size: .95rem; line-height: 1.55; color: {MUTED}; max-width: 75ch; margin: .4rem 0 1.25rem;}}
.panel ul.points {{margin-bottom: 1.25rem;}}
.panel .counts {{margin-top: 1.25rem;}}
@media (max-width: 860px) {{
  .pipe {{flex-direction: column;}}
  .pipe label {{flex-direction: row; align-items: center;}}
  .pipe .link {{flex-basis: 1.25rem; transform: rotate(90deg);}}
  .panels .panel {{padding: 1.25rem;}}
  .counts {{min-width: 0; width: 100%;}}
  .counts th, .counts td {{padding-left: .75rem;}}
}}
.foot {{display: flex; flex-wrap: wrap; gap: .25rem 1.5rem; margin-top: 3rem; padding-top: 1.25rem;
       border-top: 1px solid {LINE}; font-size: .8rem; color: {MUTED};}}
</style>"""


def run_sql(statement: str, **params) -> pd.DataFrame:
    """Run a query on the SQL warehouse. Numbers come back as text."""
    w = WorkspaceClient()
    parameters = [
        StatementParameterListItem(name=k, value=str(v), type=SQL_TYPES.get(type(v), "STRING"))
        for k, v in params.items()
    ]
    response = w.statement_execution.execute_statement(
        statement=statement, warehouse_id=WAREHOUSE_ID, parameters=parameters, wait_timeout="30s"
    )
    while response.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(2)
        response = w.statement_execution.get_statement(response.statement_id)
    if response.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(response.status.error.message if response.status.error else response.status.state)
    columns = [c.name for c in response.manifest.schema.columns]
    return pd.DataFrame(response.result.data_array or [], columns=columns)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_summary() -> dict:
    """Numbers for the header: listings for rent and sale, neighborhoods and the last scrape date."""
    tables = " UNION ALL ".join(
        f"SELECT city, neighborhood, last_seen_date FROM {mode['listings']}" for mode in MODES.values()
    )
    row = run_sql(
        "SELECT COUNT(*) AS listings, COUNT(DISTINCT city, neighborhood) AS zones, "
        f"MAX(last_seen_date) AS updated FROM ({tables})"
    ).iloc[0]
    return {"listings": int(row["listings"]), "zones": int(row["zones"]),
            "updated": pd.Timestamp(row["updated"]).strftime("%-d %b %Y")}


@st.cache_data(ttl=6 * 3600, show_spinner="Loading neighborhoods...")
def load_neighborhoods(table: str, city: str) -> list[str]:
    rows = run_sql(
        f"SELECT neighborhood FROM {table} WHERE city = :city AND neighborhood <> '' "
        f"GROUP BY neighborhood HAVING COUNT(*) >= {MIN_ZONE_LISTINGS} ORDER BY neighborhood",
        city=city,
    )
    return rows["neighborhood"].tolist()


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_estratos(table: str, city: str) -> pd.DataFrame:
    """Estratos seen in each neighborhood of the city, with how many listings have each one."""
    rows = run_sql(
        f"SELECT neighborhood, estrato, COUNT(*) AS n FROM {table} "
        "WHERE city = :city AND neighborhood <> '' AND estrato IS NOT NULL GROUP BY neighborhood, estrato",
        city=city,
    )
    return rows.astype({"estrato": int, "n": int})


def estrato_options(table: str, city: str, zone: str) -> list[int]:
    """Estratos common in the neighborhood (or the city if no barrio), most frequent first.

    One-off values are usually data errors, so an estrato needs at least 2 listings
    and 5% of the neighborhood's listings with a known estrato.
    """
    seen = load_estratos(table, city)
    seen = seen.query("neighborhood == @zone") if zone else seen.groupby("estrato", as_index=False)["n"].sum()
    seen = seen.sort_values("n", ascending=False)
    if seen.empty:
        return [1, 2, 3, 4, 5, 6]
    common = seen[(seen["n"] >= 2) & (seen["n"] >= 0.05 * seen["n"].sum())]
    return (common if not common.empty else seen)["estrato"].tolist()


@st.cache_data(ttl=3600, show_spinner=False)
def similar_listings(
    mode: str, city: str, property_type: str, zone: str, estrato: int, bedrooms: int, area: float | None
) -> pd.DataFrame:
    """Up to 5 listings of the same type in the same neighborhood (or city), closest match first.

    The same property posted by two agencies shows up once. good_deal comes from the
    daily scoring job; listings not seen in the last week are never flagged.
    """
    params = {"city": city, "property_type": property_type, "estrato": estrato, "bedrooms": bedrooms}
    zone_filter = ""
    if zone:
        params["zone"] = zone
        zone_filter = "AND g.neighborhood = :zone "
    area_order = ""
    if area is not None:
        params["area"] = area
        area_order = "ABS(g.area_m2 - :area) NULLS LAST, "
    rows = run_sql(
        "SELECT g.area_m2, g.bedrooms, g.bathrooms, g.parking, g.estrato, g.last_price_cop, "
        "g.last_seen_date, g.url, COALESCE(p.good_deal, false) AS good_deal "
        f"FROM {MODES[mode]['listings']} g LEFT JOIN {MODES[mode]['predictions']} p "
        "ON p.source = g.source AND p.property_id = g.property_id "
        f"WHERE g.city = :city AND g.property_type = :property_type {zone_filter}"
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY g.area_m2, g.bedrooms, g.bathrooms, g.parking, g.estrato, "
        "g.last_price_cop ORDER BY g.last_seen_date DESC) = 1 "
        "ORDER BY g.estrato = :estrato DESC NULLS LAST, ABS(g.bedrooms - :bedrooms) NULLS LAST, "
        f"{area_order}g.last_seen_date DESC LIMIT 5",
        **params,
    )
    numbers = ["area_m2", "bedrooms", "bathrooms", "parking", "estrato", "last_price_cop"]
    return rows.assign(**{c: pd.to_numeric(rows[c]) for c in numbers})


def zone_label(zone: str) -> str:
    """'chico norte iii' -> 'Chico Norte III'."""
    return " ".join(w.upper() if set(w) <= set("ivx") else w.capitalize() for w in zone.split())


def cop(value: float) -> str:
    return f"${value:,.0f}"


def rounded(value: float, mode: str) -> float:
    """Estimates to the nearest 10,000 pesos for rent and 1 million for sale: more digits would be false precision."""
    step = 10_000 if mode == "rent" else 1_000_000
    return round(value / step) * step


def millions(value: float) -> str:
    return f"${value / 1e6:,.1f}M"


def range_chart(estimate: float, low: float | None, high: float | None, prices: list[float]) -> alt.LayerChart:
    """One row: the likely range as a band, the estimate as a tick, similar listings as dots."""
    values = [estimate, *prices] + ([low, high] if low is not None else [])
    pad = 0.08 * (max(values) - min(values)) or 0.1 * estimate
    scale = alt.Scale(domain=[max(0, min(values) - pad), max(values) + pad], nice=False)
    axis = alt.Axis(title=None, labelExpr="'$' + format(datum.value / 1e6, '.1f') + 'M'", tickCount=5,
                    labelColor=MUTED, labelFontSize=11, gridColor="#e2e0d9", domain=False, ticks=False)
    layers = []
    if low is not None:
        band = pd.DataFrame({"low": [low], "high": [high]})
        layers += [
            alt.Chart(band).mark_rect(color=BAND, cornerRadius=8).encode(
                x=alt.X("low:Q", scale=scale, axis=axis), x2="high:Q", y=alt.value(34), y2=alt.value(70),
                tooltip=[alt.Tooltip("low:Q", title="Low", format="$,.0f"), alt.Tooltip("high:Q", title="High", format="$,.0f")],
            ),
            alt.Chart(band).mark_text(baseline="top", fontSize=12, color=MUTED, align="left", dx=2).encode(
                x="low:Q", y=alt.value(78), text=alt.value(f"Low {millions(low)}")),
            alt.Chart(band).mark_text(baseline="top", fontSize=12, color=MUTED, align="right", dx=-2).encode(
                x="high:Q", y=alt.value(78), text=alt.value(f"High {millions(high)}")),
        ]
    point = pd.DataFrame({"estimate": [estimate]})
    layers += [
        alt.Chart(point).mark_rule(color=DEEP_BLUE, strokeWidth=3, strokeCap="round").encode(
            x=alt.X("estimate:Q", scale=scale, axis=axis), y=alt.value(26), y2=alt.value(78),
            tooltip=[alt.Tooltip("estimate:Q", title="Estimate", format="$,.0f")]),
        alt.Chart(point).mark_text(baseline="bottom", fontSize=13, fontWeight=700, color=INK).encode(
            x="estimate:Q", y=alt.value(20), text=alt.value(f"Estimate {millions(estimate)}")),
    ]
    if prices:
        layers.append(
            alt.Chart(pd.DataFrame({"price": prices})).mark_circle(
                size=120, color=ORANGE, opacity=1, stroke=SURFACE, strokeWidth=2
            ).encode(x=alt.X("price:Q", scale=scale, axis=axis), y=alt.value(52),
                     tooltip=[alt.Tooltip("price:Q", title="Similar listing", format="$,.0f")])
        )
    return (
        alt.layer(*layers)
        .properties(height=130)
        .configure(font="Inter, sans-serif", background="transparent")
        .configure_view(stroke=None)
    )


def nowrap(text: str) -> str:
    return f'<span style="white-space: nowrap">{html.escape(text)}</span>'


def card(row) -> str:
    """One similar listing as HTML. Text comes from scraped pages, so it is escaped."""
    facts = [
        f"{row.bedrooms:.0f} bed" if pd.notna(row.bedrooms) else None,
        f"{row.bathrooms:.0f} bath" if pd.notna(row.bathrooms) else None,
        f"{row.parking:.0f} parking" if pd.notna(row.parking) else None,
    ]
    size = [
        f"{row.area_m2:.0f} m²" if pd.notna(row.area_m2) else "Area unknown",
        f"estrato {row.estrato:.0f}" if pd.notna(row.estrato) else "estrato unknown",
    ]
    seen = pd.Timestamp(row.last_seen_date).strftime("%-d %b %Y")
    deal = '<div class="deal">Good deal</div>' if row.good_deal == "true" else ""
    return (
        f'<div class="card-price">{cop(row.last_price_cop)}</div>{deal}'
        f'<div class="card-facts">{" · ".join(nowrap(f) for f in facts if f)}<br>'
        f'{" · ".join(nowrap(f) for f in size)}</div>'
        f'<div class="card-seen">Last seen {seen}</div>'
    )


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def listing_counts() -> pd.DataFrame:
    """Listings per city for rent and sale, and the first day each was collected."""
    tables = " UNION ALL ".join(
        f"SELECT '{mode}' AS mode, city, first_seen_date FROM {c['listings']}" for mode, c in MODES.items()
    )
    rows = run_sql(f"SELECT city, mode, COUNT(*) AS n, MIN(first_seen_date) AS since FROM ({tables}) GROUP BY city, mode")
    rows = rows.astype({"n": int}).assign(since=pd.to_datetime(rows["since"]))
    counts = rows.pivot(index="city", columns="mode", values="n").reindex(columns=list(MODES), fill_value=0)
    cities = [c for c in CITIES if c in counts.index]
    return pd.DataFrame({
        "City": [CITIES[c] for c in cities],
        "Rentals": counts.loc[cities, "rent"].fillna(0).astype(int).to_numpy(),
        "Sales": counts.loc[cities, "sale"].fillna(0).astype(int).to_numpy(),
        "Collected since": rows.groupby("city")["since"].min()[cities].dt.strftime("%-d %b %Y").to_numpy(),
    }).set_index("City")


@st.cache_data(ttl=3600, show_spinner=False)
def model_facts(mode: str) -> dict:
    """The model version the endpoint serves, the training run that built it and the latest retrain."""
    w = WorkspaceClient()
    served = w.serving_endpoints.get(MODES[mode]["endpoint"]).config.served_entities[0]
    runs = []
    for run in w.experiments.search_runs(experiment_ids=[EXPERIMENT_ID], order_by=["start_time DESC"]):
        tags = {t.key: t.value for t in run.data.tags or []}
        # Runs from before the sale model have no "listings" tag; they are all rent.
        if "challenger_version" in tags and tags.get("listings", "rent") == mode:
            runs.append({
                "version": tags["challenger_version"],
                "date": pd.Timestamp(run.info.start_time, unit="ms").strftime("%-d %b %Y"),
                "params": {p.key: p.value for p in run.data.params or []},
                "metrics": {m.key: m.value for m in run.data.metrics or []},
                "promoted": tags.get("promoted") == "True",
            })
    built = next(r for r in runs if r["version"] == served.entity_version)
    return {"model": served.entity_name, "version": served.entity_version, "built": built, "latest": runs[0]}


def percent(value: float) -> str:
    return f"{value:.0%}"


MODEL_NAMES = {"lightgbm": "LightGBM", "catboost": "CatBoost", "xgboost": "XGBoost",
               "blend": "Average of LightGBM, CatBoost and XGBoost"}


def model_choice(params: dict, metrics: dict) -> list[tuple[str, str]]:
    """Which model the search kept and how each candidate did. Runs from before the search have neither."""
    if "model" not in params:
        return [("Model", "LightGBM with fixed settings")]
    # One decimal: the candidates are often less than a point apart.
    scores = ", ".join(f"{MODEL_NAMES[k] if k != 'blend' else 'their average'} {metrics[f'valid_mape_{k}']:.1%}"
                       for k in ["lightgbm", "catboost", "xgboost", "blend"])
    return [("Model", MODEL_NAMES[params["model"]]),
            ("Picked from", f"Errors on {int(params['valid_rows']):,} validation listings, after "
                            f"{params['trials']} tuning trials per model: {scores}.")]


def facts_list(items: list[tuple[str, str]]) -> str:
    """Label and value pairs as a definition list. Values are written here, not scraped."""
    return '<dl class="facts">' + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in items) + "</dl>"


def model_card(mode: str):
    facts = model_facts(mode)
    built, params, metrics = facts["built"], facts["built"]["params"], facts["built"]["metrics"]
    st.html(f'<div class="label">{MODES[mode]["label"]} model</div>')
    col1, col2 = st.columns(2)
    col1.metric("Average error (MAPE)", percent(metrics["challenger_mape"]),
                delta=f"{(metrics['challenger_mape'] - metrics['baseline_mape']) * 100:+.0f} points vs barrio median",
                delta_color="inverse")
    col2.metric("Barrio median error", percent(metrics["baseline_mape"]),
                help="The simple rule the model must beat: the median price of the barrio in the training data.")
    cutoff = pd.Timestamp(params["train_cutoff"]).strftime("%-d %b %Y")
    test_rows = int(params["test_rows"])
    # Runs from before the split parameter always used the last 30 days.
    kind = params.get("split", "temporal")
    if kind == "temporal":
        test = f"The {test_rows:,} listings first seen in the 30 days after {cutoff}."
    elif kind == "mixed":
        test = (f"{test_rows:,} listings: those first seen after {cutoff} in cities with 60 days of data or more, "
                "and a random 1 in 5 in newer cities.")
    else:
        test = (f"A random 1 in 5 of the listings ({test_rows:,}). With under 60 days of data there is no later "
                "month to test on, so this error is likely too low.")
    st.html(facts_list([
        ("Version", f"{facts['version']}, trained on {built['date']}"),
        *model_choice(params, metrics),
        ("Trained on", f"{int(params['train_rows']):,} listings"),
        ("Tested on", test),
        ("Average miss (MAE)", f"{cop(metrics['challenger_mae'])}. A few very expensive listings pull it up."),
    ]))
    latest = facts["latest"]
    if latest["version"] != facts["version"] and "champion_mape" in latest["metrics"]:
        outcome = "replaced this one" if latest["promoted"] else "did not beat this one, so this one stayed"
        st.caption(
            f"Last retrain, {latest['date']}: a new version scored {percent(latest['metrics']['challenger_mape'])} "
            f"against {percent(latest['metrics']['champion_mape'])} for this one on newer listings, and {outcome}."
        )


# Authored line icons, one stroke weight. st.html drops inline SVG, so each one is a CSS mask.
ICONS = {
    "sources": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "scrape": '<path d="M12 3v12M7 10l5 5 5-5M4 20h16"/>',
    "land": '<ellipse cx="12" cy="5.5" rx="8" ry="2.5"/><path d="M4 5.5v13c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5v-13'
            'M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5"/>',
    "clean": '<path d="m12 3 9 4.5-9 4.5-9-4.5z"/><path d="m3 12 9 4.5 9-4.5M3 16.5 12 21l9-4.5"/>',
    "train": '<path d="M4 4v16h16"/><path d="m8 15 3.5-4 3 2.5L19 8"/>',
    "serve": '<rect x="3.5" y="4" width="17" height="7" rx="2"/><rect x="3.5" y="13" width="17" height="7" rx="2"/>'
             '<path d="M7.5 7.5h.01M7.5 16.5h.01"/>',
    "arrow": '<path d="M4 12h15M14 7l5 5-5 5"/>',
}


def glyph_css() -> str:
    """One --glyph mask per icon: a 24 px line drawing, colored by the text color of its tile."""
    rules = []
    for name, paths in ICONS.items():
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" '
               f'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>')
        rules.append(f'.glyph-{name} {{--glyph: url("data:image/svg+xml,{quote(svg)}");}}')
    return " ".join(rules)


def counts_table() -> str:
    try:
        counts = listing_counts()
    except Exception as error:  # noqa: BLE001 - show the reason instead of breaking the page
        return f'<p class="note">Could not load the listing counts: {html.escape(str(error))}</p>'
    rows = "".join(
        f"<tr><td>{city}</td><td>{rent:,}</td><td>{sale:,}</td><td>{since}</td></tr>"
        for city, (rent, sale, since) in zip(counts.index, counts.to_numpy(), strict=True)
    )
    return ('<table class="counts"><thead><tr><th>City</th><th>Rentals</th><th>Sales</th><th>Collected since</th>'
            f"</tr></thead><tbody>{rows}</tbody></table>")


def pipeline_steps() -> list[dict]:
    """The six steps of the diagram: tile text and the panel shown when the step is picked."""
    return [
        {"key": "sources", "name": "Sources", "what": "Fincaraiz and Metrocuadrado",
         "title": "Sources: two listing websites",
         "lead": "Listings come from two Colombian property websites, Fincaraiz and Metrocuadrado. Sixteen "
                 "searches cover each site, four cities, and rent and sale.",
         "facts": [("Websites", "Fincaraiz, Metrocuadrado"), ("Cities", "Bogota, Medellin, Envigado, Sabaneta"),
                   ("Searches", "16: site, city, and rent or sale")],
         "extra": counts_table()},
        {"key": "scrape", "name": "Scraping", "what": "GitHub Actions, every 6 to 8 hours",
         "title": "Scraping: GitHub Actions, several times a day",
         "lead": "Each search runs as its own job. A scraper reads the first result pages to collect listing "
                 "links, then opens each listing and reads its title, price, area, rooms, location and technical "
                 "sheet (estrato, parking, age).",
         "facts": [("Schedule", "Fincaraiz every 6 hours, Metrocuadrado every 8 hours"),
                   ("Depth", "5 result pages on Fincaraiz, 2 on Metrocuadrado"),
                   ("Tools", "Scrapy for Fincaraiz (plain HTML); Playwright, a real browser, for Metrocuadrado "
                             "(pages built with JavaScript)"),
                   ("Output", "One CSV file per search and run")]},
        {"key": "land", "name": "Landing", "what": "Files in a Databricks volume",
         "title": "Landing: every file kept twice",
         "lead": "Each run commits its CSV files to the Git repository, which keeps the full history, and uploads "
                 "them to a Unity Catalog volume in Databricks.",
         "facts": [("Storage", "Unity Catalog volume in workspace.inmuebles"),
                   ("Read by", "Auto Loader, which reads each new file once"),
                   ("History", "Every file since October 2025 is in Git")]},
        {"key": "clean", "name": "Cleaning", "what": "Lakeflow pipeline, daily",
         "title": "Cleaning: a Lakeflow pipeline, every morning",
         "lead": "At 7:00 Bogota time a pipeline turns the raw files into one clean row per listing, in three "
                 "layers.",
         "points": ["<b>Bronze:</b> the raw files, each read once.",
                    "<b>Silver:</b> the text becomes fields: price, area, rooms, estrato, barrio and property type. "
                    "The city and rent or sale come from the search that found the listing. One row per listing "
                    "and day. Rows with an impossible price or no id are dropped.",
                    "<b>Gold:</b> one row per listing, with its first asking price and the first and last day a "
                    "scraper saw it. One table for rentals, one for sales.",
                    "<b>Maps:</b> barrio names are matched to official barrio outlines for the dashboard maps."]},
        {"key": "train", "name": "Training", "what": "Model search, monthly",
         "title": "Training: three model types compete, every month",
         "lead": "One model estimates monthly rent and one estimates sale prices, with the same code. Each month "
                 "LightGBM, CatBoost and XGBoost are tuned and compared with their average, and the best one is "
                 "kept. All three are boosted trees: each tree corrects the errors of the ones before.",
         "points": ["<b>Predicts</b> the first asking price, on a log scale, because prices go from half a million "
                    "to billions of pesos.",
                    "<b>Uses</b> the website, city, property type, barrio, area, bedrooms, bathrooms, parking, "
                    "estrato and building age. Barrio, area and age can be missing.",
                    "<b>Tested</b> on listings it did not see: in each city with 60 days of data or more, the "
                    "last 30 days; in a newer city, a random 1 in 5 of its listings, so the model still learns "
                    "the rest.",
                    "<b>Tuned</b> with Optuna: 30 settings per model type, each try steered by how the earlier "
                    "ones did, and each model stops adding trees once it stops improving. Tuning is scored on a "
                    "validation slice of the training listings, cut the same way as the test.",
                    "<b>Picked</b> by the lowest validation error among the three tuned models and their "
                    "average. The test listings are used once, after, to compare with the version in use. "
                    "Random forest, a linear model and a small neural net were tried too and did worse.",
                    "<b>Must beat</b> the median price of the barrio, and the version in use, on the same test "
                    "listings. Otherwise the version in use stays.",
                    "<b>Likely range</b> from the 10th and 90th percentile of real price divided by estimate on "
                    "the test listings, per property type. About 3 in 4 real prices fall inside."],
         "facts": [("Retrained", "The 1st of each month. A weekly check on new rentals starts one early if the "
                                 "error goes above 60%."),
                   ("Tracked in", "MLflow: every run with its data version, settings, tuning trials and errors")]},
        {"key": "serve", "name": "Serving", "what": "Endpoints, this app, dashboard",
         "title": "Serving: endpoints, this app and a dashboard",
         "lead": "The winning version is registered in Unity Catalog as the champion and served by a Model "
                 "Serving endpoint. The endpoint sleeps when idle, so the first estimate of the day can take a "
                 "few minutes.",
         "points": ["<b>Daily scoring:</b> every morning the champion prices the listings seen in the last 7 days. "
                    "A good deal asks less than the low end of its likely range.",
                    "<b>Dashboard:</b> prices by month, barrio maps and every listing with its model price.",
                    "<b>This app:</b> an estimate, its likely range and similar real listings."]},
    ]


def pipeline_diagram() -> str:
    """Clickable diagram with no JavaScript: each tile is the label of a hidden radio button, and CSS shows
    the panel of the checked one. Arrow keys move between steps like any radio group."""
    steps = pipeline_steps()
    inputs, tiles, panels, rules = [], [], [], []
    for i, step in enumerate(steps):
        k = step["key"]
        inputs.append(f'<input type="radio" name="how-step" id="how-{k}"{" checked" if i == 0 else ""}>')
        if i:
            tiles.append('<span class="link" aria-hidden="true"><i class="glyph glyph-arrow"></i></span>')
        tiles.append(
            f'<label for="how-{k}"><span class="icon" aria-hidden="true"><i class="glyph glyph-{k}"></i></span>'
            f'<span class="name">{step["name"]}</span><span class="what">{step["what"]}</span></label>'
        )
        body = f'<h3>{step["title"]}</h3><p>{step["lead"]}</p>'
        if step.get("points"):
            body += '<ul class="points">' + "".join(f"<li>{p}</li>" for p in step["points"]) + "</ul>"
        if step.get("facts"):
            body += facts_list(step["facts"])
        body += step.get("extra", "")
        panels.append(f'<div class="panel panel-{k}">{body}</div>')
        rules += [
            f'#how-{k}:checked ~ .pipe label[for="how-{k}"] {{border-color: {BLUE}; background: #eef4fc;}}',
            f'#how-{k}:checked ~ .pipe label[for="how-{k}"] .icon {{background: {BLUE}; color: #fff;}}',
            f'#how-{k}:focus-visible ~ .pipe label[for="how-{k}"] {{outline: 2px solid {BLUE}; outline-offset: 2px;}}',
            f"#how-{k}:checked ~ .panels .panel-{k} {{display: block;}}",
        ]
    return (f"<style>{glyph_css()} {' '.join(rules)}</style>"
            f'<div class="how">{"".join(inputs)}<div class="pipe">{"".join(tiles)}</div>'
            f'<div class="panels">{"".join(panels)}</div></div>')


def section(title: str, intro: str = ""):
    st.html(f'<div class="section"><h2>{title}</h2>{f"<p>{intro}</p>" if intro else ""}</div>')


def render_about():
    section("How the data flows", "From two listing websites to the estimate on this page. Pick a step to see "
                                  "what it does.")
    st.html(pipeline_diagram())
    st.html('<div style="height: 1.5rem"></div>')
    section("How accurate the models are", "The error is the average gap between the estimate and the real asking "
                                           "price, as a share of that price, on listings the model did not see in "
                                           "training. Lower is better.")
    for column, mode in zip(st.columns(2, gap="medium"), MODES, strict=True):
        with column, st.container(border=True, key=f"card-model-{mode}"):
            try:
                model_card(mode)
            except Exception as error:  # noqa: BLE001
                st.warning(f"Could not load the {MODES[mode]['label'].lower()} model metrics: {error}")
    st.html('<div style="height: 1.5rem"></div>')
    section("Limits")
    st.html(
        '<ul class="points">'
        "<li>These are <b>asking prices</b>, not the prices people finally pay or rent for.</li>"
        "<li>The city is the city searched. About 3 in 100 listings are in a neighboring city.</li>"
        "<li>Barrio names are typed by agencies, so the same place can have several names.</li>"
        "<li>Medellin, Envigado and Sabaneta have been collected since November 2026, so their estimates rest on "
        "a few hundred listings each.</li>"
        "<li>The sale model started in November 2026 with one day of data. Its error will be clearer once there "
        "are a few months of history.</li></ul>"
    )


st.set_page_config(page_title="Property price estimator", page_icon=":material/apartment:", layout="wide")
st.html(STYLE)

summary = load_summary()
st.html(
    '<header class="appbar"><div><h1>Property price estimator</h1>'
    "<p>Describe a property in Bogota, Medellin, Envigado or Sabaneta to get a fair monthly rent or sale price, "
    "a likely range and real listings like it.</p></div>"
    f'<div class="status">Updated <b>{summary["updated"]}</b> · <b>{summary["listings"]:,}</b> listings · '
    f'<b>{summary["zones"]:,}</b> barrios</div></header>'
)
FOOTER = (
    '<footer class="foot"><span>Asking prices from Fincaraiz and Metrocuadrado, collected several times a day.'
    "</span><span>Estimates are a guide, not an appraisal.</span><span>Built on Databricks.</span></footer>"
)

estimate_tab, about_tab = st.tabs(["Estimate a price", "How it was built"], key="tab", on_change="rerun")
if about_tab.open:
    with about_tab:
        render_about()
    st.html(FOOTER)
    st.stop()

inputs, results = estimate_tab.columns([5, 7], gap="large")

with inputs, st.container(border=True, key="card-inputs"):
    st.html('<div class="label">The property</div>')
    mode = st.segmented_control(
        "Price to estimate", list(MODES), default="rent", required=True, format_func=lambda m: MODES[m]["label"]
    )
    config = MODES[mode]
    city = st.selectbox("City", list(CITIES), format_func=CITIES.get)
    zones = load_neighborhoods(config["listings"], city)
    if not zones:
        st.info(f"No {config['label'].lower()} listings in {CITIES[city]} yet.")
        st.html(FOOTER)
        st.stop()
    property_type = st.selectbox("Property type", PROPERTY_TYPES, format_func=str.capitalize)
    default_zone = next((zones.index(z) for z in DEFAULT_ZONES if z in zones), 0)
    zone = st.selectbox(
        "Neighborhood (barrio)", [ANY_ZONE, *zones], index=default_zone + 1,
        format_func=lambda z: zone_label(z) if z else "Any barrio (not sure)",
        help="Type to search. Lists barrios with at least 3 listings. Pick \"Any barrio\" if you don't know it; "
        "the model then works from the city, estrato and the rest.",
    )
    place = f"{zone_label(zone)}, {CITIES[city]}" if zone else CITIES[city]
    common = estrato_options(config["listings"], city, zone)
    estrato = st.segmented_control(
        "Estrato", sorted(common), default=common[0], required=True, format_func=str,
        help="Estratos found in listings of this barrio (or city). The most common one is selected.",
    )
    col1, col2, col3 = st.columns(3)
    bedrooms = col1.number_input("Bedrooms", min_value=0, max_value=10, value=2)
    bathrooms = col2.number_input("Bathrooms", min_value=0, max_value=10, value=2)
    parking = col3.number_input("Parking", min_value=0, max_value=10, value=1)

    st.html('<div class="label" style="margin-top:.75rem">Optional</div>')
    st.caption("Leave them off if you don't know them; many listings in the training data have no area or age either.")
    col4, col5 = st.columns(2)
    area = col4.number_input("Area (m²)", min_value=10.0, max_value=2000.0, value=70.0, format="%.0f") if col4.toggle("Use area", value=True) else None
    age = col5.number_input("Building age (years)", min_value=0.0, max_value=100.0, value=10.0, format="%.0f") if col5.toggle("Use building age") else None
    clicked = st.button(config["button"], type="primary", width="stretch", icon=":material/query_stats:")

if not clicked:
    with results, st.container(border=True, key="card-empty"):
        st.html(
            f'<div class="label">Estimated {config["label"].lower()}</div><div class="estimate empty">$&mdash;</div>'
            f'<p class="note">Describe the property and press <b>{config["button"]}</b>. You get a fair '
            f"{config['label'].lower()}, the range most similar places fall in, and five real listings like "
            "it.</p>"
            '<p class="note" style="margin-top:.75rem">The first estimate of the day can take a few minutes '
            "while the model wakes up.</p>"
        )
    st.html(FOOTER)
    st.stop()

record = {
    "source": "metrocuadrado",  # the model also learned which site a listing came from
    "city": city,
    "property_type": property_type,
    "neighborhood": zone or UNKNOWN_ZONE,
    "area_m2": area,
    "bedrooms": float(bedrooms),
    "bathrooms": float(bathrooms),
    "parking": float(parking),
    "estrato": float(estrato),
    "age_years": age,
}
with results, st.spinner("Estimating... the first request can take a few minutes while the model wakes up."):
    client = WorkspaceClient(config=Config(http_timeout_seconds=MODEL_WAKE_SECONDS))
    response = client.serving_endpoints.query(
        name=config["endpoint"], dataframe_records=[record]
    )
    examples = similar_listings(mode, city, property_type, zone, int(estrato), int(bedrooms), area)
result = response.predictions[0]
if isinstance(result, dict):
    estimate, low, high = result["prediction"], result["low"], result["high"]
else:  # a model version registered before the range existed
    estimate, low, high = result, None, None
estimate = rounded(estimate, mode)
if low is not None:
    low, high = rounded(low, mode), rounded(high, mode)

per_m2 = f" · {cop(round(estimate / area, -2))} per m²" if area else ""
with results, st.container(border=True, key="card-result"):
    st.html(
        f'<div class="label">Estimated {config["label"].lower()}</div><div class="estimate">{cop(estimate)}</div>'
        f'<div class="sub">{config["unit"]}{per_m2} · {html.escape(property_type.capitalize())} in '
        f"{html.escape(place)} · estrato {estrato}</div>"
    )
    st.altair_chart(range_chart(estimate, low, high, examples["last_price_cop"].dropna().tolist()),
                    width="stretch", theme=None)
    legend = [f'<span><i style="background:{BAND}"></i>Likely range</span>'] if low is not None else []
    legend += [f'<span><i class="tick" style="background:{DEEP_BLUE}"></i>Estimate</span>']
    legend += [f'<span><i class="dot" style="background:{ORANGE}"></i>Similar listings</span>'] if len(examples) else []
    st.html(f'<div class="legend">{"".join(legend)}</div>')
    if low is not None:
        st.html(
            f'<p class="note" style="margin-top:.75rem">Likely range <b>{cop(low)}</b> to <b>{cop(high)}</b>. '
            "About 3 in 4 recent listings of this property type had a real price in this range.</p>"
        )

estimate_tab.html(f'<div class="label" style="margin-top:2rem">Similar listings in {html.escape(place)}</div>')
if examples.empty:
    estimate_tab.info(f"No {property_type} listings in {place} yet.")
else:
    estimate_tab.caption(
        f"Same type in this {'barrio' if zone else 'city'}; same estrato first, then closest bedrooms and area. "
        "Asking price when last seen. Good deal: asks less than the low end of the range the model expects for "
        "that listing."
    )
    for i, (column, row) in enumerate(zip(estimate_tab.columns(5), examples.itertuples(), strict=False)):
        with column, st.container(border=True, key=f"card-listing-{i}"):
            st.html(card(row))
            st.link_button("Open listing", row.url, type="tertiary", icon=":material/open_in_new:")
st.html(FOOTER)
