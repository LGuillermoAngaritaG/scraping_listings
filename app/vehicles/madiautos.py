"""Madiautos (Bogota): car URLs from the sitemap, data from each page's JSON-LD and spec table."""

import json
import logging
import re
from urllib.parse import urlsplit

from parsel import Selector

from app.vehicles.common import cc_from_text, clean_text, make_row

SITEMAP_URL = "https://usados.madiautos.com.co/sitemap.xml"
HOST = "usados.madiautos.com.co"

log = logging.getLogger(__name__)


def vehicle_urls(sitemap_xml: str) -> list[str]:
    """Car pages listed in the sitemap. Only this host: other hosts are never called."""
    locs = Selector(text=sitemap_xml, type="xml").xpath("//*[local-name()='loc']/text()").getall()
    urls = [u.strip() for u in locs]
    return [u for u in urls if urlsplit(u).netloc == HOST and "/vehiculo/" in u]


def _car_json_ld(selector: Selector) -> dict:
    for raw in selector.xpath('//script[@type="application/ld+json"]/text()').getall():
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]):
            if isinstance(item, dict) and item.get("@type") == "Car":
                return item
    return {}


def parse(html: str, url: str, scraped_at: str) -> dict | None:
    """One row from a car page, or None when the page has no car data (e.g. sold)."""
    selector = Selector(text=html)
    car = _car_json_ld(selector)
    specs = {
        clean_text(" ".join(dt.css("::text").getall())): clean_text(
            " ".join(dt.xpath("following-sibling::dd[1]//text()").getall())
        )
        for dt in selector.css("dl.specs dt")
    }
    if not car and not specs:
        return None
    listing_id = re.search(r"/vehiculo/usado-([0-9a-f-]+)", url)
    return make_row(
        "madiautos",
        scraped_at,
        listing_id=listing_id.group(1) if listing_id else urlsplit(url).path,
        url=url,
        title=car.get("name") or clean_text(" ".join(selector.css("h1 ::text").getall())),
        brand=specs.get("Marca") or (car.get("brand") or {}).get("name"),
        line=specs.get("Línea") or car.get("model"),
        version=specs.get("Versión", ""),
        year=specs.get("Año") or car.get("vehicleModelDate"),
        km=(car.get("mileageFromOdometer") or {}).get("value") or specs.get("Kilometraje"),
        price=(car.get("offers") or {}).get("price"),
        transmission=specs.get("Transmisión") or car.get("vehicleTransmission"),
        fuel=specs.get("Combustible") or car.get("fuelType"),
        body=specs.get("Carrocería") or car.get("bodyType"),
        body_text=car.get("name"),
        engine_cc=specs.get("Cilindraje") or cc_from_text(car.get("name")),
        color=specs.get("Color") or car.get("color"),
        plate=specs.get("Placa"),  # "Terminada en 3 (impar)"
        city="bogota",  # the site sells in Bogota only and names no branch
    )


def fetch(client, scraped_at: str) -> list[dict]:
    rows = []
    for url in vehicle_urls(client.get(SITEMAP_URL).text):
        try:
            row = parse(client.get(url).text, url, scraped_at)
        except Exception as exc:  # one broken page must not lose the other cars
            log.warning("madiautos: %s failed: %s", url, exc)
            continue
        if row is None:
            log.warning("madiautos: no car data on %s", url)
            continue
        rows.append(row)
    return rows
