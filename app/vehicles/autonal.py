"""Autonal (Bogota, Chia, Villavicencio): WooCommerce Store API, category 59 = used cars."""

import html

from app.vehicles.common import make_row

API_URL = "https://autonal.com/wp-json/wc/store/v1/products"
USED_CATEGORY = 59


def _line_and_version(line_text: str, version: str) -> tuple[str, str]:
    """'Línea' holds model + version ('NEW C4 CACTUS FEEL 1.6 AUT'): cut the version off."""
    line_text, version = line_text.strip(), version.strip()
    if version and line_text.endswith(version) and len(line_text) > len(version):
        return line_text[: -len(version)], version
    first, _, rest = line_text.partition(" ")
    return first, rest


def parse(products: list[dict], scraped_at: str) -> list[dict]:
    rows = []
    for product in products:
        # The "VIN" attribute and the SKU (it holds the full plate) are never read.
        a = {
            attribute.get("name"): ", ".join(
                t.get("name", "") for t in attribute.get("terms") or []
            )
            for attribute in product.get("attributes") or []
            if attribute.get("name") != "VIN"
        }
        line, version = _line_and_version(a.get("Línea", ""), a.get("Versión", ""))
        location = a.get("Ubicación", "")
        doors = a.get("Puertas", "")
        rows.append(
            make_row(
                "autonal",
                scraped_at,
                listing_id=product.get("id"),
                url=product.get("permalink"),
                title=html.unescape(product.get("name") or ""),
                brand=a.get("Marca"),
                line=line,
                version=version,
                year=a.get("Año"),
                km=a.get("Kilometraje"),
                price=(product.get("prices") or {}).get("price"),
                transmission=a.get("Transmisión"),
                fuel=a.get("Tipo de combustible"),
                clase=a.get("Clase"),
                body_text=f"{a.get('Línea', '')} {doors}p" if doors else a.get("Línea", ""),
                engine_cc=a.get("Cilindraje"),
                color=a.get("Color"),
                plate=a.get("Placa"),  # the site gives only the last digit ("Ø" = 0)
                city=location.split(" - ")[0],
                branch=location,
            )
        )
    return rows


def fetch(client, scraped_at: str) -> list[dict]:
    rows, page, pages = [], 1, 1
    while page <= pages:
        params = {"category": USED_CATEGORY, "per_page": 100, "page": page}
        response = client.get(API_URL, params=params)
        pages = int(response.headers.get("X-WP-TotalPages") or 1)
        rows += parse(response.json(), scraped_at)
        page += 1
    return rows
