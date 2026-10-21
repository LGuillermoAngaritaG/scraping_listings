"""Filter the tidy export without loading the scraping engines."""

import math
import re
import unicodedata
from datetime import date, datetime, timezone
from urllib.parse import urlsplit, urlunsplit


def _normalized(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFD", text.casefold())
                   if not unicodedata.combining(char))


def _seen(row: dict[str, str]) -> datetime:
    seen = datetime.fromisoformat(row["last_seen"])
    return seen.replace(tzinfo=timezone.utc) if seen.tzinfo is None else seen.astimezone(timezone.utc)


def prepare_listings(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Repair historical links and keep the newest row per property, only for search."""
    selected = {}
    for row in rows:
        parts = urlsplit(row["url"])
        url = urlunsplit((parts.scheme, parts.netloc, re.sub(r"/{2,}", "/", parts.path), "", ""))
        if url not in selected or _seen(row) >= _seen(selected[url]):
            selected[url] = {**row, "url": url}
    return list(selected.values())


def filter_listings(rows: list[dict[str, str]], *, min_price: float | None = None,
                    max_price: float | None = None, bedrooms: float | None = None,
                    bathrooms: float | None = None, min_area: float | None = None,
                    where: str | None = None, source: str | None = None,
                    recent: int | None = 30) -> list[dict[str, str]]:
    """Select export rows, using calendar days from the newest last_seen; sort by price.

    Numeric bounds are inclusive. Unknown numbers cannot satisfy a numeric filter,
    and sort after known prices when price is unconstrained. Input rows are unchanged.
    """
    if not rows:
        return []
    newest = max(date.fromisoformat(row["last_seen"][:10]) for row in rows)
    needle = _normalized(where) if where else ""

    def number(row, field):
        value = float(row[field]) if row[field] else math.inf
        return value if math.isfinite(value) else math.inf

    matches = []
    for row in rows:
        if recent is not None and (newest - date.fromisoformat(row["last_seen"][:10])).days > recent:
            continue
        if source and row["source"] != source:
            continue
        if needle and not any(needle in _normalized(row[field]) for field in ("location", "title")):
            continue
        bounds = (("price", min_price, max_price), ("bedrooms", bedrooms, None),
                  ("bathrooms", bathrooms, None), ("area_m2", min_area, None))
        if any((lower is not None or upper is not None) and
               (not math.isfinite(number(row, field)) or
                (lower is not None and number(row, field) < lower) or
                (upper is not None and number(row, field) > upper))
               for field, lower, upper in bounds):
            continue
        matches.append(row)
    return sorted(matches, key=lambda row: number(row, "price"))
