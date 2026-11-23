"""Autoexpo (Bogota): all used cars come from one WordPress (Vehica) API call."""

from scraping_listings.vehicles.common import canon_city, make_row

API_URL = "https://autoexpo.com.co/wp-json/vehica/v1/cars?condition=usado"


def _attributes(car: dict) -> dict:
    """{attribute name: raw value}, with taxonomy terms joined and price/location unwrapped."""
    values = {}
    for attribute in car.get("attributes") or []:
        value = attribute.get("value")
        if isinstance(value, list):
            value = ", ".join(t.get("name", "") for t in value if isinstance(t, dict))
        elif isinstance(value, dict) and attribute.get("type") == "price":
            value = next(iter(value.values()), None)
        elif isinstance(value, dict) and attribute.get("type") == "location":
            value = value.get("address")
        values[attribute.get("name")] = value
    return values


def parse(data: dict, scraped_at: str) -> list[dict]:
    rows = []
    for car in data.get("results") or []:
        a = _attributes(car)
        reference = a.get("Referencia") or ""
        address = a.get("Location") or ""
        rows.append(
            make_row(
                "autoexpo",
                scraped_at,
                listing_id=car.get("id"),
                url=car.get("url"),
                title=car.get("name"),
                brand=a.get("Marca"),
                # "Línea" is sometimes "New": the model is then the first word of "Referencia".
                line=a.get("Línea"),
                version="" if reference.strip() == "0" else reference,
                year=a.get("Modelo"),
                km=a.get("Kilometraje"),
                price=a.get("Precio"),
                transmission=a.get("Transmisión"),
                fuel=a.get("Combustible"),
                clase=a.get("Clase"),
                body_text=car.get("name"),
                engine_cc=a.get("Cilindraje"),
                color=a.get("Color"),
                plate=a.get("Placa"),  # already masked by the site, e.g. "XXXXX3"
                city=canon_city(address.rsplit(",", 1)[-1]) or "bogota",
                branch=(car.get("user") or {}).get("name"),
            )
        )
    return rows


def fetch(client, scraped_at: str) -> list[dict]:
    return parse(client.get(API_URL).json(), scraped_at)
