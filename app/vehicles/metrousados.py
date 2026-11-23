"""Metrousados (mostly Bogota): every car is a card on one listing page; no detail pages."""

from parsel import Selector

from app.vehicles.common import cc_from_text, clean_text, make_row, split_brand

LIST_URL = "https://metrousados.com/compra-tu-carro/"


def parse(html: str, scraped_at: str) -> list[dict]:
    rows = []
    for card in Selector(text=html).css("div.usado"):
        title = clean_text(" ".join(card.css("h4 ::text").getall()))
        # Brand, line and engine come from the title; data-marca is sometimes wrong.
        brand, model_text = split_brand(title)
        line, _, version = model_text.partition(" ")
        post_id = card.css("[data-post-id]::attr(data-post-id)").get("")
        rows.append(
            make_row(
                "metrousados",
                scraped_at,
                listing_id=post_id,
                url=f"{LIST_URL}#{post_id}",
                title=title,
                brand=brand,
                line=line,
                version=version,
                year=card.css(".ano::text").get(),
                km=card.css(".km::text").get(),
                price=card.css(".precio::text").get(),
                transmission=title,
                fuel=card.css(".combustible::text").get() or card.attrib.get("data-combustible"),
                body_text=title,
                engine_cc=cc_from_text(title),
                plate=card.css(".placa::text").get(),  # full plate: only its last digit is kept
                city=card.css(".ciudad::text").get() or card.attrib.get("data-ciudad"),
            )
        )
    return rows


def fetch(client, scraped_at: str) -> list[dict]:
    return parse(client.get(LIST_URL).text, scraped_at)
