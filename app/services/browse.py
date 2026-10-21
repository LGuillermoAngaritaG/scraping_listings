"""Write the tidy export as one offline HTML page for choosing and sharing homes."""

import argparse
import csv
import json
import os
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.services.search import load_listings

TEMPLATE = Path(__file__).with_name("browse.html")
FIELDS = ("source", "url", "title", "location", "price", "bedrooms", "bathrooms", "area_m2")


def catalogue_data(rows: list[dict[str, str]], now: datetime | None = None) -> dict:
    """Page payload: numbers as floats or None, last sighting as a UTC calendar date."""
    items = [{**{field: row[field] for field in FIELDS},
              **{field: float(row[field]) if row[field] else None
                 for field in ("price", "bedrooms", "bathrooms", "area_m2")},
              "last_seen": row["last_seen"][:10]} for row in rows]
    now = now or datetime.now(timezone.utc)
    return {"mode": "catalogue", "rows": items,
            "meta": {"properties": len(items),
                     "data_date": max((item["last_seen"] for item in items), default=None),
                     "generated_at": now.strftime("%Y-%m-%d %H:%M UTC")}}


def render_page(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # Inside <script type="application/json">, escaped markup characters cannot end the element.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload, 1)


def write_page(text: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent,
                                         suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(text)
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def browse_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="scrape browse",
        description="Write saved listings as one HTML page to filter, compare and share homes offline")
    parser.add_argument("--input", default="data/listings.csv", help="Export CSV (default: data/listings.csv)")
    parser.add_argument("--output", default="data/browse/listings.html",
                        help="HTML file to write (default: data/browse/listings.html)")
    args = parser.parse_args(argv)
    path, output = Path(args.input), Path(args.output)
    if output.suffix.lower() not in (".html", ".htm"):
        parser.error("--output must end in .html so it cannot replace a CSV")
    if output.exists() and output.resolve() == path.resolve():
        parser.error("--output must differ from --input")
    refresh = shlex.join(["scrape", "export"] + (["--output", str(path)] if args.input != "data/listings.csv" else []))
    try:
        data = catalogue_data(load_listings(path))
    except FileNotFoundError:
        print(f"Error: No export file at {path}. Run '{refresh}' first, or choose an existing CSV with --input.",
              file=sys.stderr)
        return 1
    except (ValueError, OSError, csv.Error) as error:
        print(f"Error: Cannot read {path}: {error}. Run '{refresh}' to rebuild the export.", file=sys.stderr)
        return 1
    try:
        write_page(render_page(data), output)
    except OSError as error:
        print(f"Error: Cannot write {output}: {error.strerror or error}. "
              "Choose a writable location with --output.", file=sys.stderr)
        return 1
    meta = data["meta"]
    if meta["data_date"] is None:
        print(f"Wrote {output}: the export has no listings, so the page will be empty. "
              f"Run '{refresh}' after scraping, then run this again.")
    else:
        print(f"Wrote {output}: {meta['properties']:,} properties, data as of {meta['data_date']} "
              f"({output.stat().st_size / 1e6:.1f} MB).")
    print("Open it in a web browser; it works offline. Choose homes there and use "
          "'Download shortlist' to save or send only those.")
    return 0
