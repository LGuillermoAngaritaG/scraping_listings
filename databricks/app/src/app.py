"""Estimate the monthly rent of a property with the serving endpoint, and show similar listings."""

import html
import os
import time

import altair as alt
import pandas as pd
import streamlit as st
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service.sql import StatementParameterListItem, StatementState

ENDPOINT = os.environ["SERVING_ENDPOINT"]
WAREHOUSE_ID = os.environ["SQL_WAREHOUSE_ID"]
LISTINGS = os.environ["LISTINGS_TABLE"]
PREDICTIONS = os.environ["PREDICTIONS_TABLE"]  # daily prices and good-deal flags
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

STYLE = f"""<style>
[data-testid="stMainBlockContainer"] {{max-width: 1200px; padding-top: 3rem; padding-bottom: 4rem;}}
.eyebrow, .label {{font-size: .75rem; font-weight: 600; letter-spacing: .12em; text-transform: uppercase;}}
.eyebrow {{color: {DEEP_BLUE};}}
.label {{color: {MUTED};}}
.hero h1 {{font-size: 2.75rem; font-weight: 800; letter-spacing: -.035em; line-height: 1.05;
          margin: .4rem 0 .75rem; padding: 0; color: {INK};}}
.lead {{font-size: 1.075rem; color: {MUTED}; max-width: 44rem; line-height: 1.55; margin: 0;}}
.stats {{display: flex; gap: 2.5rem; margin: 1.5rem 0 .5rem;}}
.stats b {{display: block; font-size: 1.4rem; font-weight: 700; letter-spacing: -.02em;
          font-variant-numeric: tabular-nums; color: {INK};}}
.stats span, .sub, .note, .card-seen {{color: {MUTED};}}
.stats span {{font-size: .8rem;}}
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
    """Numbers for the header: listings, neighborhoods and the last scrape date."""
    row = run_sql(
        f"SELECT COUNT(*) AS listings, COUNT(DISTINCT neighborhood) AS zones, "
        f"MAX(last_seen_date) AS updated FROM {LISTINGS}"
    ).iloc[0]
    return {"listings": int(row["listings"]), "zones": int(row["zones"]),
            "updated": pd.Timestamp(row["updated"]).strftime("%-d %b %Y")}


@st.cache_data(ttl=6 * 3600, show_spinner="Loading neighborhoods...")
def load_neighborhoods() -> list[str]:
    rows = run_sql(
        f"SELECT neighborhood FROM {LISTINGS} WHERE neighborhood <> '' "
        f"GROUP BY neighborhood HAVING COUNT(*) >= {MIN_ZONE_LISTINGS} ORDER BY neighborhood"
    )
    return rows["neighborhood"].tolist()


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_estratos() -> pd.DataFrame:
    """Estratos seen in each neighborhood, with how many listings have each one."""
    rows = run_sql(
        f"SELECT neighborhood, estrato, COUNT(*) AS n FROM {LISTINGS} "
        "WHERE neighborhood <> '' AND estrato IS NOT NULL GROUP BY neighborhood, estrato"
    )
    return rows.astype({"estrato": int, "n": int})


def estrato_options(zone: str) -> list[int]:
    """Estratos common in the neighborhood, most frequent first.

    One-off values are usually data errors, so an estrato needs at least 2 listings
    and 5% of the neighborhood's listings with a known estrato.
    """
    seen = load_estratos().query("neighborhood == @zone").sort_values("n", ascending=False)
    if seen.empty:
        return [1, 2, 3, 4, 5, 6]
    common = seen[(seen["n"] >= 2) & (seen["n"] >= 0.05 * seen["n"].sum())]
    return (common if not common.empty else seen)["estrato"].tolist()


@st.cache_data(ttl=3600, show_spinner=False)
def similar_listings(
    property_type: str, zone: str, estrato: int, bedrooms: int, area: float | None
) -> pd.DataFrame:
    """Up to 5 listings of the same type in the same neighborhood, closest match first.

    The same property posted by two agencies shows up once. good_deal comes from the
    daily scoring job; listings not seen in the last week are never flagged.
    """
    params = {"property_type": property_type, "zone": zone, "estrato": estrato, "bedrooms": bedrooms}
    area_order = ""
    if area is not None:
        params["area"] = area
        area_order = "ABS(g.area_m2 - :area) NULLS LAST, "
    rows = run_sql(
        "SELECT g.area_m2, g.bedrooms, g.bathrooms, g.parking, g.estrato, g.last_price_cop, "
        "g.last_seen_date, g.url, COALESCE(p.good_deal, false) AS good_deal "
        f"FROM {LISTINGS} g LEFT JOIN {PREDICTIONS} p "
        "ON p.source = g.source AND p.property_id = g.property_id "
        "WHERE g.property_type = :property_type AND g.neighborhood = :zone "
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
    return f"$ {value:,.0f}"


def millions(value: float) -> str:
    return f"${value / 1e6:.1f}M"


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


st.set_page_config(page_title="Bogota rent estimate", page_icon=":material/apartment:", layout="wide")
st.html(STYLE)

summary = load_summary()
st.html(
    '<div class="hero"><div class="eyebrow">Bogota rent estimator</div>'
    "<h1>What should this place rent for?</h1>"
    '<p class="lead">A price model trained on rental listings from Fincaraiz and Metrocuadrado. '
    "Describe the property to get a fair monthly rent, a likely range and real listings like it.</p>"
    f'<div class="stats"><div><b>{summary["listings"]:,}</b><span>listings</span></div>'
    f'<div><b>{summary["zones"]:,}</b><span>neighborhoods</span></div>'
    f'<div><b>{summary["updated"]}</b><span>last scrape</span></div></div></div>'
)

zones = load_neighborhoods()
inputs, results = st.columns([5, 7], gap="large")

with inputs, st.container(border=True, key="card-inputs"):
    st.html('<div class="label">The property</div>')
    property_type = st.selectbox("Property type", PROPERTY_TYPES, format_func=str.capitalize)
    zone = st.selectbox(
        "Neighborhood (barrio)", zones, index=zones.index("chico norte") if "chico norte" in zones else 0,
        format_func=zone_label, help="Type to search. Lists barrios with at least 3 listings.",
    )
    common = estrato_options(zone)
    estrato = st.segmented_control(
        "Estrato", sorted(common), default=common[0], required=True, format_func=str,
        help="Estratos found in listings of this barrio. The most common one is selected.",
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
    clicked = st.button("Estimate rent", type="primary", width="stretch", icon=":material/query_stats:")

if not clicked:
    with results, st.container(border=True, key="card-empty"):
        st.html(
            '<div class="label">Estimated monthly rent</div><div class="estimate empty">$ &mdash;</div>'
            '<p class="note">Describe the property and press <b>Estimate rent</b>. You get a fair monthly '
            "rent, the range most similar places fall in, and five real listings from the same barrio.</p>"
            '<p class="note" style="margin-top:.75rem">The first estimate of the day can take a few minutes '
            "while the model wakes up.</p>"
        )
    st.stop()

record = {
    "source": "metrocuadrado",  # the model also learned which site a listing came from
    "property_type": property_type,
    "neighborhood": zone,
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
        name=ENDPOINT, dataframe_records=[record]
    )
    examples = similar_listings(property_type, zone, int(estrato), int(bedrooms), area)
result = response.predictions[0]
if isinstance(result, dict):
    estimate, low, high = result["prediction"], result["low"], result["high"]
else:  # a model version registered before the range existed
    estimate, low, high = result, None, None

per_m2 = f" ({cop(estimate / area)} per m²)" if area else ""
with results, st.container(border=True, key="card-result"):
    st.html(
        f'<div class="label">Estimated monthly rent</div><div class="estimate">{cop(estimate)}</div>'
        f'<div class="sub">COP per month{per_m2} · {html.escape(property_type.capitalize())} in '
        f"{html.escape(zone_label(zone))}, estrato {estrato}</div>"
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

st.html(f'<div class="label" style="margin-top:2rem">Similar listings in {html.escape(zone_label(zone))}</div>')
if examples.empty:
    st.info(f"No {property_type} listings in {zone_label(zone)} yet.")
else:
    st.caption(
        "Same type in this barrio; same estrato first, then closest bedrooms and area. Asking price when last "
        "seen. Good deal: asks less than the low end of the range the model expects for that listing."
    )
    for i, (column, row) in enumerate(zip(st.columns(5), examples.itertuples(), strict=False)):
        with column, st.container(border=True, key=f"card-listing-{i}"):
            st.html(card(row))
            st.link_button("Open listing", row.url, type="tertiary", icon=":material/open_in_new:")
