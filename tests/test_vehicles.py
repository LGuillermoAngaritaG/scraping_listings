"""Tests for the used-car scrapers (scraping_listings/vehicles), run on small fixtures without network."""

import csv
import json
import re
from pathlib import Path
from urllib.parse import urlencode

import pytest

from scraping_listings.vehicles import (
    autoexpo,
    autonal,
    casatoro,
    cli,
    client,
    madiautos,
    maxiautos,
    metrousados,
)
from scraping_listings.vehicles.common import (
    COLUMNS,
    canon_body_type,
    canon_city,
    canon_fuel,
    canon_transmission,
    cc_from_text,
    plate_digit,
    split_model,
    to_cc,
    to_int,
)

FIXTURES = Path(__file__).parent / "fixtures" / "vehicles"
SCRAPED_AT = "2026-11-23T08:00:00+00:00"
# Fake plates and VIN planted in the fixtures in place of the real ones.
FAKE_SECRETS = ["TST120", "TST121", "TST122", "TST123", "TST450", "TST451", "TESTVIN0000000001"]

TRANSMISSIONS = {"", "automatica", "manual"}
FUELS = {"", "gasolina", "diesel", "hibrido", "electrico", "gas"}
BODY_TYPES = {
    "",
    "suv",
    "sedan",
    "hatchback",
    "pickup",
    "van",
    "coupe",
    "convertible",
    "station wagon",
    "otro",
}


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, text: str, headers: dict | None = None):
        self.text = text
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


class FakeClient:
    """Answers GETs from fixtures and records every URL (with its query) and headers asked for."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []
        self.headers: list[dict] = []

    def get(self, url, params=None, headers=None):
        full = url + ("?" + urlencode(params) if params else "")
        self.calls.append(full)
        self.headers.append(headers or {})
        route = self.routes[full]
        text, response_headers = route if isinstance(route, tuple) else (route, {})
        return FakeResponse(text, response_headers)


def autoexpo_client():
    return FakeClient({autoexpo.API_URL: fixture_text("autoexpo_cars.json")})


def casatoro_client():
    page2 = f"{casatoro.BASE_URL}/_next/data/testbuild123/catalogo.json?page=2"
    return FakeClient(
        {
            f"{casatoro.BASE_URL}/catalogo": fixture_text("casatoro_catalogo.html"),
            page2: fixture_text("casatoro_page2.json"),
        }
    )


def autonal_client():
    products = json.loads(fixture_text("autonal_products.json"))
    url = autonal.API_URL + "?category=59&per_page=100&page="
    pages = {"X-WP-Total": "3", "X-WP-TotalPages": "2"}
    return FakeClient(
        {
            url + "1": (json.dumps(products[:2]), pages),
            url + "2": (json.dumps(products[2:]), pages),
        }
    )


def maxiautos_client():
    listing = fixture_text("maxiautos_list.json")
    routes = {maxiautos.LIST_URL + "?page=1": listing}
    for product in json.loads(listing)["products"]:
        routes[product["url"]] = fixture_text(f"maxiautos_detail_{product['id_product']}.html")
    return FakeClient(routes)


def madiautos_client():
    urls = json.loads(fixture_text("madiautos_urls.json"))
    routes = {madiautos.SITEMAP_URL: fixture_text("madiautos_sitemap.xml")}
    routes.update({url: fixture_text(f"madiautos_detail_{n}.html") for n, url in urls.items()})
    return FakeClient(routes)


def metrousados_client():
    return FakeClient({metrousados.LIST_URL: fixture_text("metrousados_list.html")})


SITES = {
    "autoexpo": (autoexpo, autoexpo_client),
    "casatoro": (casatoro, casatoro_client),
    "autonal": (autonal, autonal_client),
    "maxiautos": (maxiautos, maxiautos_client),
    "madiautos": (madiautos, madiautos_client),
    "metrousados": (metrousados, metrousados_client),
}


def scrape(site: str) -> list[dict]:
    module, make_client = SITES[site]
    return module.fetch(make_client(), SCRAPED_AT)


def by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["listing_id"]: row for row in rows}


# --- every site --------------------------------------------------------------------------


@pytest.mark.parametrize("site", SITES)
def test_rows_have_the_csv_columns_in_order(site):
    rows = scrape(site)
    assert rows
    assert cli.SITES.keys() == SITES.keys()
    for row in rows:
        assert list(row) == COLUMNS
        assert row["source"] == site
        assert row["scraped_at"] == SCRAPED_AT
        assert row["listing_id"] and row["url"].startswith("https://") and row["title"]
    assert len({row["listing_id"] for row in rows}) == len(rows)


@pytest.mark.parametrize("site", SITES)
def test_values_are_canonical(site):
    for row in scrape(site):
        assert row["transmission"] in TRANSMISSIONS
        assert row["fuel"] in FUELS
        assert row["body_type"] in BODY_TYPES
        for column in ("brand", "line", "color", "city"):
            value = row[column]
            assert value == value.lower() and "  " not in value and value == value.strip()
            assert re.fullmatch(r"[a-z0-9 ./+()-]*", value), (column, value)
        for column in ("year", "km", "price_cop", "engine_cc"):
            assert row[column] == "" or isinstance(row[column], int), (column, row[column])
        assert row["brand"] and row["line"] and row["price_cop"] and row["year"]


@pytest.mark.parametrize("site", SITES)
def test_no_full_plate_or_vin_in_output(site):
    rows = scrape(site)
    everything = " ".join(str(value) for row in rows for value in row.values()).upper()
    for secret in FAKE_SECRETS:
        assert secret not in everything
    for row in rows:
        assert re.fullmatch(r"\d?", row["plate_digit"])


# --- per site ----------------------------------------------------------------------------


def test_autoexpo_mapping_and_new_line_fix():
    rows = by_id(scrape("autoexpo"))
    kia = rows["1622164"]  # Línea "New", the model is in Referencia ("Sportage Zenith HEV.")
    assert (kia["brand"], kia["line"], kia["version"]) == ("kia", "sportage", "Zenith HEV")
    assert kia["url"] == "https://autoexpo.com.co/tu-carro/kia-new-2/"
    assert (kia["year"], kia["km"], kia["price_cop"]) == (2026, 36493, 142800000)
    assert (kia["transmission"], kia["fuel"], kia["body_type"]) == ("automatica", "hibrido", "suv")
    assert (kia["engine_cc"], kia["color"], kia["plate_digit"]) == (1600, "gris", "9")
    assert (kia["city"], kia["branch"]) == ("bogota", "Autoexpo 116")
    toyota = rows["1622166"]  # Línea "Corolla" + Referencia "Cross SEG"
    assert (toyota["line"], toyota["version"]) == ("corolla cross", "SEG")
    sail = rows["1616827"]
    assert (sail["body_type"], sail["transmission"], sail["branch"]) == (
        "sedan",
        "manual",
        "Autoexpo Centro",
    )


def test_casatoro_pages_branches_and_cut_line():
    fake = casatoro_client()
    rows = by_id(casatoro.fetch(fake, SCRAPED_AT))
    assert len(fake.calls) == 2 and fake.calls[1].endswith("/testbuild123/catalogo.json?page=2")
    assert rows["7741"]["line"] == "t-cross"  # the site gives "T"
    spark = rows["7720"]
    assert (
        spark["url"]
        == "https://usados.casatoro.com/vehiculo/chevrolet-spark-gt-m300-ltz-mt-1200cc-5p-7720"
    )
    assert (spark["brand"], spark["line"], spark["engine_cc"]) == ("chevrolet", "spark", 1200)
    assert (spark["body_type"], spark["transmission"], spark["km"]) == (
        "hatchback",
        "manual",
        18696,
    )
    assert (spark["city"], spark["branch"]) == ("bogota", "Bellpi y Premium 170")
    assert (rows["7494"]["city"], rows["7494"]["branch"]) == ("chia", "CasaToro Usados Chía")


def test_autonal_pages_and_line_version_split():
    fake = autonal_client()
    rows = by_id(autonal.fetch(fake, SCRAPED_AT))
    assert len(fake.calls) == 2
    cactus = rows["306285"]  # Línea "NEW C4 CACTUS FEEL 1.6 AUT", Versión "CACTUS FEEL 1.6 AUT"
    assert (cactus["line"], cactus["version"]) == ("c4 cactus", "FEEL 1.6 AUT")
    assert cactus["title"] == "CITROEN – NEW C4 CACTUS FEEL 1.6 AUT – 2023"
    assert (cactus["engine_cc"], cactus["color"], cactus["plate_digit"]) == (
        1587,
        "blanco nacar",
        "7",
    )
    assert (cactus["city"], cactus["branch"]) == ("bogota", "BOGOTA - LA FELICIDAD")
    vitara = rows["306061"]
    assert (vitara["line"], vitara["engine_cc"], vitara["body_type"]) == (
        "grand vitara",
        1462,
        "suv",
    )
    assert rows["306166"]["plate_digit"] == "0"  # the site writes "Ø"


def test_maxiautos_uses_detail_features():
    fake = maxiautos_client()
    rows = by_id(maxiautos.fetch(fake, SCRAPED_AT))
    assert fake.headers[0] == {"Accept": "application/json"}
    assert not any(re.search(r"[?&](n|order)=", url) for url in fake.calls)
    escape = rows["1618"]
    assert (escape["brand"], escape["line"], escape["version"]) == ("ford", "escape", "SE")
    assert (escape["year"], escape["km"], escape["price_cop"]) == (2019, 56800, 60900000)
    assert (escape["transmission"], escape["body_type"], escape["engine_cc"]) == (
        "automatica",
        "suv",
        2000,
    )
    assert (escape["color"], escape["plate_digit"], escape["city"]) == ("azul", "1", "bogota")
    sail = rows["1654"]
    assert (sail["line"], sail["body_type"], sail["transmission"]) == ("sail", "sedan", "manual")


def test_maxiautos_keeps_the_car_when_the_detail_page_fails():
    product = json.loads(fixture_text("maxiautos_list.json"))["products"][1]
    row = maxiautos.parse(product, {}, SCRAPED_AT)
    assert (row["brand"], row["line"], row["year"], row["price_cop"]) == (
        "chevrolet",
        "sail",
        2015,
        32900000,
    )


def test_madiautos_reads_json_ld_and_specs_and_stays_on_its_host():
    fake = madiautos_client()
    rows = by_id(madiautos.fetch(fake, SCRAPED_AT))
    assert len(rows) == 2 and not any("dal-ia.com" in url for url in fake.calls)
    mazda = rows["790bd5b8-828a-4415-8cf9-815b87134ecb"]
    assert (mazda["line"], mazda["version"], mazda["fuel"]) == ("cx-30", "Grand Touring", "hibrido")
    assert (mazda["body_type"], mazda["engine_cc"], mazda["plate_digit"]) == ("suv", 2000, "3")
    assert (mazda["km"], mazda["price_cop"], mazda["city"]) == (38002, 105900000, "bogota")
    logan = rows["565bdea3-3ca0-4be5-aaa0-60b20a3ba597"]
    assert (logan["body_type"], logan["transmission"], logan["engine_cc"]) == (
        "sedan",
        "manual",
        1600,
    )


def test_metrousados_takes_brand_and_engine_from_the_title():
    rows = by_id(scrape("metrousados"))
    bronco = rows["25354"]  # data-marca says "toyota"
    assert (bronco["brand"], bronco["line"]) == ("ford", "bronco sport")
    assert (bronco["version"], bronco["engine_cc"]) == ("2.0 Ecoboost Wildtrak 4×4", 2000)
    assert bronco["url"] == "https://metrousados.com/compra-tu-carro/#25354"
    sportage = rows["21508"]
    assert (sportage["city"], sportage["transmission"], sportage["km"]) == (
        "medellin",
        "automatica",
        30688,
    )
    assert [rows[i]["plate_digit"] for i in ("25088", "21508", "25354")] == ["0", "1", "2"]


# --- helpers -----------------------------------------------------------------------------


def test_number_parsing():
    assert to_int("$120.800.000") == 120800000
    assert to_int("56.185 km") == 56185
    assert to_int(2023) == 2023
    assert to_int("") is None and to_int(None) is None
    assert to_cc(1.8) == 1800 and to_cc(2) == 2000 and to_cc("1,8L") == 1800
    assert to_cc("1.462") == 1462 and to_cc("2.000 cc") == 2000 and to_cc("2000 cc") == 2000
    assert to_cc(1598) == 1598
    assert to_cc(0) is None and to_cc("") is None
    assert cc_from_text("GT [M300] LTZ MT 1200CC 5P") == 1200
    assert cc_from_text("TRITON GLX MT 2400CCC TD 4X4") == 2400
    assert cc_from_text("Kia Tasman 2.2td X-line") == 2200
    assert cc_from_text("Kia Ev9 0.0 Gt Line") is None
    assert cc_from_text("Kia Sonet Zenith At") is None


@pytest.mark.parametrize(
    ("brand", "line", "version", "expected"),
    [
        ("Kia", "New", "Sportage Zenith HEV.", ("sportage", "Zenith HEV")),
        ("VOLKSWAGEN", "T", "TRENDLINE ST", ("t-cross", "TRENDLINE ST")),
        ("Volkswagen", "T Cross", "Trendline Turbo", ("t-cross", "Trendline Turbo")),
        ("Suzuki", "Grand", "Vitara GLX.", ("grand vitara", "GLX")),
        ("VOLKSWAGEN", "NEW GOL", "TRENDLINE 1.6 5P", ("gol", "TRENDLINE 1.6 5P")),
        ("MAZDA", "CX30", "GRAND TOURING", ("cx-30", "GRAND TOURING")),
        ("Peugeot", "3008.", "Active Turbo.", ("3008", "Active Turbo")),
        ("Chevrolet", "Tracker", "", ("tracker", "")),
    ],
)
def test_split_model(brand, line, version, expected):
    assert split_model(brand, line, version) == expected


def test_canonical_helpers():
    assert canon_transmission("Secuencial") == "automatica"
    assert canon_transmission("Mecánico") == "manual"
    assert canon_transmission("Kia Sonet 1.5 Zenith At (ivt)") == "automatica"
    assert canon_transmission("Kia Picanto 1.2 X-line") == ""
    assert canon_fuel("Gasoelec") == "hibrido" and canon_fuel("Hybrid") == "hibrido"
    assert canon_fuel("Eléctrico") == "electrico" and canon_fuel("Díesel") == "diesel"
    assert canon_fuel("GASOLINA") == "gasolina" and canon_fuel("Gas") == "gas"
    assert canon_body_type(body="Sedán") == "sedan"
    assert canon_body_type(body="PICO (PICK UP)") == "pickup"
    assert canon_body_type(body="STATION WAGON", clase="Camioneta") == "suv"
    assert (
        canon_body_type(clase="Camioneta", text="Ram 1000 DC", brand="ram", line="1000") == "pickup"
    )
    assert canon_body_type(clase="Automóvil", text="MHEV 1.2 5p") == "hatchback"
    assert canon_body_type(clase="Automóvil", text="Rocks-e 2 p.") == ""
    assert canon_city("BOGOTÁ. DC. - BOGOTÁ. D.C") == "bogota"
    assert canon_city("Medellín (Antioquia)") == "medellin"
    assert canon_city("bogota_dc") == "bogota"
    assert canon_city("Calle 127 # 60-69 Bogotá - Niza") == "bogota"
    assert canon_city("CENTRO DE ALISTAMIENTO") == ""
    assert plate_digit("XXXXX3") == "3" and plate_digit("Ø") == "0"
    assert plate_digit("Terminada en 3 (impar)") == "3" and plate_digit("1 - Uno") == "1"
    assert plate_digit("par") == ""


# --- command line and HTTP client ----------------------------------------------------------


def test_cli_keeps_going_when_a_site_fails(tmp_path, monkeypatch):
    def good(client, scraped_at):
        return scrape("metrousados")

    def broken(client, scraped_at):
        raise RuntimeError("site down")

    monkeypatch.setattr(cli, "SITES", {"broken": broken, "good": good, "empty": lambda c, s: []})
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path)
    assert cli.main(["all"]) == 1
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert re.fullmatch(r"vehicles-good_\d{8}_\d{6}\.csv", files[0].name)
    with files[0].open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == COLUMNS
        assert len(list(reader)) == 3
    assert cli.main(["good"]) == 0


def test_client_waits_between_requests_to_the_same_site(monkeypatch):
    now = [100.0]
    sleeps = []

    class Response:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(client.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(client.time, "sleep", lambda seconds: sleeps.append(seconds))
    http = client.Client(delay=1.0, timeout=5)
    monkeypatch.setattr(http.session, "get", lambda url, timeout, **kwargs: Response())
    http.get("https://a.example/1")
    http.get("https://b.example/1")  # another site: no wait
    now[0] += 0.25
    http.get("https://a.example/2")
    assert sleeps == [pytest.approx(0.75)]
    assert "Mozilla/5.0" in http.session.headers["User-Agent"]
