"""Filter the tidy export without loading the scraping engines."""

import argparse
import csv
import math
import re
import shlex
import sys
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.services.export import EXPORT_COLUMNS


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
    bounds = (("price", min_price, max_price), ("bedrooms", bedrooms, None),
              ("bathrooms", bathrooms, None), ("area_m2", min_area, None))

    def number(row, field):
        value = float(row[field]) if row[field] else math.inf
        return value if math.isfinite(value) else math.inf

    matches = []
    for row in rows:
        if recent is not None and (newest - date.fromisoformat(row["last_seen"][:10])).days > recent:
            continue
        if source and row["source"] != source:
            continue
        if any((lower is not None or upper is not None) and
               (not math.isfinite(number(row, field)) or
                (lower is not None and number(row, field) < lower) or
                (upper is not None and number(row, field) > upper))
               for field, lower, upper in bounds):
            continue
        if needle and not any(needle in _normalized(row[field]) for field in ("location", "title")):
            continue
        matches.append(row)
    return sorted(matches, key=lambda row: number(row, "price"))


def load_listings(path: Path) -> list[dict[str, str]]:
    """Read the export and reject incomplete or invalid data with a rebuild hint."""
    rows = []
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file, strict=True)
        if not set(EXPORT_COLUMNS).issubset(reader.fieldnames or []):
            raise ValueError("expected the columns produced by 'scrape export'")
        for line, row in enumerate(reader, start=2):
            try:
                if None in row or any(value is None for value in row.values()) or not row["url"].strip():
                    raise ValueError("missing cells or listing URL")
                row["last_seen"] = _seen(row).isoformat()
                for field in ("price", "bedrooms", "bathrooms", "area_m2"):
                    if row[field] and (not math.isfinite(float(row[field])) or float(row[field]) < 0):
                        raise ValueError(f"{field} must be a finite non-negative number or blank")
            except ValueError as error:
                raise ValueError(f"invalid row {line}: {error}") from error
            rows.append(row)
    return prepare_listings(rows)


def _nonnegative_number(text: str) -> float:
    try:
        number = float(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a finite non-negative number") from error
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite non-negative number")
    return number


def _nonnegative_int(text: str) -> int:
    try:
        number = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from error
    if number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return number


def _display_number(value: str) -> str:
    return f"{float(value):,.2f}".rstrip("0").rstrip(".") if value else "—"


def print_search_results(rows: list[dict[str, str]], filters: dict, limit: int) -> None:
    newest = max((row["last_seen"][:10] for row in rows), default="unavailable (empty export)")
    print(f"Data as of {newest}; recent means the last {filters['recent']} days in saved data.")
    matches = filter_listings(rows, **filters)
    if not matches:
        print("No listings matched (0).")
        print("Matches if you remove one filter (all other filters stay):")
        for name, value in filters.items():
            if value is not None and value != "":
                count = len(filter_listings(rows, **{**filters, name: None}))
                print(f"  Without --{name.replace('_', '-')}: {count} listings")
        print("Widen a filter or use --recent with more days to include older observations.")
        return
    print(f"{len(matches)} listings matched; showing {min(limit, len(matches))} (lowest price first).")
    print(f"{'Price (COP)':>14}  {'Beds':>4}  {'Baths':>5}  {'Area (m²)':>10}  Title")
    for row in matches[:limit]:
        values = [_display_number(row[field]) for field in ("price", "bedrooms", "bathrooms", "area_m2")]
        print(f"{values[0]:>14}  {values[1]:>4}  {values[2]:>5}  {values[3]:>10}  {' '.join(row['title'].split()) or '—'}")
        print(f"  Location: {' '.join(row['location'].split()) or '—'}")
        print(f"  {row['url']}")


def search_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="scrape search", description="Find listings in the saved tidy export")
    parser.add_argument("--input", default="data/listings.csv", help="Export CSV (default: data/listings.csv)")
    parser.add_argument("--min-price", type=_nonnegative_number, help="Minimum price in COP (inclusive)")
    parser.add_argument("--max-price", type=_nonnegative_number, help="Maximum price in COP (inclusive)")
    parser.add_argument("--bedrooms", type=_nonnegative_int, help="Minimum bedrooms (inclusive)")
    parser.add_argument("--bathrooms", type=_nonnegative_int, help="Minimum bathrooms (inclusive)")
    parser.add_argument("--min-area", type=_nonnegative_number, help="Minimum area in square metres (inclusive)")
    parser.add_argument("--where", help="Text in location or title; ignores accents and case")
    parser.add_argument("--source", help="Exact scraper source name from the export, e.g. fincaraiz-bogota-leasing")
    parser.add_argument("--recent", type=_nonnegative_int, default=30,
                        help="Days before the newest data date, inclusive (default: 30; 0 = newest day)")
    parser.add_argument("--limit", type=_nonnegative_int, default=20, help="Maximum rows to show (default: 20)")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.min_price is not None and args.max_price is not None and args.min_price > args.max_price:
        parser.error("--min-price must be less than or equal to --max-price")
    path = Path(args.input)
    refresh = shlex.join(["scrape", "export"] + (["--output", str(path)] if args.input != "data/listings.csv" else []))
    try:
        rows = load_listings(path)
        newest_raw = max((raw.stat().st_mtime for raw in Path("data").glob("*_details_*.csv")), default=0)
        if path.stat().st_mtime < newest_raw:
            print(f"Note: {path} is older than saved raw data; searching this export. Run '{refresh}' to refresh.", file=sys.stderr)
        filters = {name: value for name, value in vars(args).items() if name not in ("input", "limit")}
        print_search_results(rows, filters, args.limit)
        return 0
    except FileNotFoundError:
        print(f"Error: No export file at {path}. Run '{refresh}' first, or choose an existing CSV with --input.", file=sys.stderr)
    except (ValueError, OSError, csv.Error) as error:
        print(f"Error: Cannot search {path}: {error}. Run '{refresh}' to rebuild the export.", file=sys.stderr)
    return 1
