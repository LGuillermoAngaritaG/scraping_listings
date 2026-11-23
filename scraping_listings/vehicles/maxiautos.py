"""Maxiautos (Bogota): PrestaShop listing as JSON, plus one detail page per car for the specs."""

import json
import logging
import re

from parsel import Selector

from scraping_listings.vehicles.common import canon_brand, canon_city, clean_text, make_row, norm

LIST_URL = "https://maxiautos.co/2-concesionario-autos"

log = logging.getLogger(__name__)


def parse_detail(html: str) -> dict[str, str]:
    """{feature name: value} from the detail page's data-product JSON."""
    raw = Selector(text=html).xpath("//*[@data-product]/@data-product").get()
    if not raw:
        return {}
    features = json.loads(raw).get("features") or []
    return {clean_text(f.get("name")): clean_text(f.get("value")) for f in features}


def parse(product: dict, features: dict[str, str], scraped_at: str) -> dict:
    """One row from a listing product and its detail features (empty if the page failed)."""
    title = clean_text(product.get("name"))
    brand = product.get("manufacturer_name") or title.split(" ")[0]
    # "Versión" (or "Línea" on some cars) is model + version ("ESCAPE SE"); without either,
    # use the title minus brand and year.
    title_model = re.sub(r"\s\d{4}$", "", title)
    model_text = features.get("Versión") or features.get("Línea") or title_model
    for prefix in {norm(brand), canon_brand(brand)}:
        if norm(model_text).startswith(prefix + " "):
            model_text = model_text[len(prefix) + 1 :]
    line, _, version = clean_text(model_text).partition(" ")
    year_in_title = re.search(r"(\d{4})$", title)
    location = features.get("Localización", "")
    return make_row(
        "maxiautos",
        scraped_at,
        listing_id=product.get("id_product"),
        url=product.get("url") or product.get("link"),
        title=title,
        brand=brand,
        line=line,
        version=version,
        year=features.get("Modelo (Año)") or (year_in_title.group(1) if year_in_title else None),
        km=features.get("Kilometraje"),
        price=product.get("price_amount"),
        transmission=features.get("Caja de cambios"),
        fuel=features.get("Combustible"),
        body=features.get("Tipo de carrocería"),
        clase=features.get("Clase"),
        body_text=title,
        engine_cc=features.get("Cilindraje") or features.get("Motor"),
        color=features.get("Color"),
        plate=features.get("Pico y Placa"),  # "1 - Uno": the last digit of the plate
        city=canon_city(location) or "bogota",
        branch=location,
    )


def fetch(client, scraped_at: str) -> list[dict]:
    products, page, pages = [], 1, 1
    while page <= pages:
        data = client.get(
            LIST_URL, params={"page": page}, headers={"Accept": "application/json"}
        ).json()
        products += data.get("products") or []
        pages = int((data.get("pagination") or {}).get("pages_count") or 1)
        page += 1
    rows = []
    for product in products:
        try:
            features = parse_detail(client.get(product["url"]).text)
        except Exception as exc:  # keep the car with the list fields only
            log.warning("maxiautos: detail page of %s failed: %s", product.get("id_product"), exc)
            features = {}
        rows.append(parse(product, features, scraped_at))
    return rows
