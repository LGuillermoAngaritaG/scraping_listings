"""CasaToro (Bogota, Chia, Funza): Next.js catalog, read page by page from its JSON data route."""

import json

from parsel import Selector

from app.vehicles.common import cc_from_text, clean_text, make_row

BASE_URL = "https://usados.casatoro.com"


def next_data(html: str) -> dict:
    """The __NEXT_DATA__ JSON of a Next.js page."""
    raw = Selector(text=html).css("script#__NEXT_DATA__::text").get()
    if not raw:
        raise ValueError("casatoro: __NEXT_DATA__ not found on the catalog page")
    return json.loads(raw)


def branch_cities(page_props: dict) -> dict[str, str]:
    """{branch name: city text} from the catalog's location filter ('Name, City')."""
    cities = {}
    for location in (page_props.get("options") or {}).get("locations") or []:
        name, _, city = (location.get("label") or "").rpartition(",")
        cities[clean_text(name)] = city
    return cities


def parse(page_props: dict, scraped_at: str, cities: dict[str, str] | None = None) -> list[dict]:
    cities = branch_cities(page_props) if cities is None else cities
    rows = []
    for car in page_props.get("vehicles") or []:
        branch = clean_text(car.get("locationName"))
        rows.append(
            make_row(
                "casatoro",
                scraped_at,
                listing_id=car.get("id"),
                url=f"{BASE_URL}/vehiculo/{car.get('slug')}",
                title=car.get("name"),
                brand=car.get("brand"),
                line=car.get("line"),  # sometimes cut at the hyphen ("T" for T-Cross)
                version=car.get("version"),
                year=car.get("year"),
                km=car.get("mileage"),
                price=car.get("price"),
                transmission=car.get("transmission"),
                fuel=car.get("fuel"),
                body=car.get("type"),
                body_text=car.get("name"),
                engine_cc=cc_from_text(car.get("version")),
                city=cities.get(branch, ""),
                branch=branch,
            )
        )
    return rows


def fetch(client, scraped_at: str) -> list[dict]:
    data = next_data(client.get(f"{BASE_URL}/catalogo").text)
    build_id = data["buildId"]
    props = data["props"]["pageProps"]
    cities = branch_cities(props)
    rows = parse(props, scraped_at, cities)
    for page in range(2, int(props.get("totalPages") or 1) + 1):
        url = f"{BASE_URL}/_next/data/{build_id}/catalogo.json"
        page_props = client.get(url, params={"page": page}).json()["pageProps"]
        rows += parse(page_props, scraped_at, cities)
    return rows
