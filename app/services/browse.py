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

from app.services.export import export_listings
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


def attach_history(data: dict, history: dict) -> dict:
    """Add saved-price histories for catalogue rows only, with notes where they disagree."""
    meta = history["meta"]
    archive_start, archive_end = ((meta[key] or "")[:10] for key in ("first_observation", "last_observation"))
    histories, counts = {}, {"with_history": 0, "price_differs": 0, "after_archive": 0, "before_archive": 0}
    for row in data["rows"]:
        found = history["listings"].get(row["url"])
        notes = []
        if row["last_seen"] > archive_end:
            notes.append("after_archive")
            counts["after_archive"] += 1
        elif row["last_seen"] < archive_start:
            notes.append("before_archive")
            counts["before_archive"] += 1
        if found:
            counts["with_history"] += 1
            latest = next((run for run in reversed(found["runs"]) if run["kind"] == "price"), None)
            if latest and row["price"] is not None and latest["prices"][0] != row["price"]:
                notes.append("price_differs")
                counts["price_differs"] += 1
            histories[row["url"]] = {**found, "notes": notes}
        elif notes:
            histories[row["url"]] = {"state": "absent", "runs": [], "changes": [], "notes": notes}
    summary = {key: meta[key] for key in ("files", "rows", "homepage_rows", "unknown_price_rows", "priced_rows",
                                          "first_observation", "last_observation", "conflict_runs",
                                          "change_events", "changed_listings")}
    return {**data, "history": histories,
            "meta": {**data["meta"], "history": {**summary, **counts,
                                                 "generated_at": data["meta"]["generated_at"]}}}


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
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", help="Export CSV (default: data/listings.csv)")
    source.add_argument("--from-data", metavar="FOLDER",
                        help="Build the page straight from the saved *_details_*.csv snapshots in FOLDER "
                             "(usually data), with no export file and no scraper setup")
    parser.add_argument("--output", default="data/browse/listings.html",
                        help="HTML file to write (default: data/browse/listings.html)")
    parser.add_argument("--history-data", metavar="FOLDER",
                        help="Also show each home's saved asking prices from the raw *_details_*.csv "
                             "snapshots in FOLDER (usually data). Optional; reads every snapshot, "
                             "which can take a minute or more")
    args = parser.parse_args(argv)
    path, output = Path(args.input or "data/listings.csv"), Path(args.output)
    if output.suffix.lower() not in (".html", ".htm"):
        parser.error("--output must end in .html so it cannot replace a CSV")
    if args.from_data:
        data = snapshot_catalogue(Path(args.from_data), output)
        if data is None:
            return 1
    else:
        if output.exists() and output.resolve() == path.resolve():
            parser.error("--output must differ from --input")
        refresh = shlex.join(["scrape", "export"] + (["--output", str(path)] if args.input not in (None, "data/listings.csv") else []))
        try:
            data = catalogue_data(load_listings(path))
        except FileNotFoundError:
            print(f"Error: No export file at {path}. Run '{refresh}' first, or choose an existing CSV with --input. "
                  "Without an export, build the page from saved snapshots with --from-data data.", file=sys.stderr)
            return 1
        except (ValueError, OSError, csv.Error) as error:
            print(f"Error: Cannot read {path}: {error}. Run '{refresh}' to rebuild the export.", file=sys.stderr)
            return 1
    if args.history_data:
        from app.services.history import read_history
        print(f"Reading saved asking prices from {args.history_data}/ (optional; this can take a minute or more)...",
              flush=True)
        try:
            history = read_history(args.history_data, progress=lambda done, total: print(
                f"  {done:,} of {total:,} snapshot files read", flush=True))
        except ValueError as error:
            print(f"Error: {error} No page was written; {output} is unchanged. "
                  "Leave out --history-data to build the page without price history.", file=sys.stderr)
            return 1
        data = attach_history(data, history)
        print_history_summary(data["meta"]["history"], history["meta"]["elapsed_seconds"])
    try:
        write_page(render_page(data), output)
    except OSError as error:
        print(f"Error: Cannot write {output}: {error.strerror or error}. "
              "Choose a writable location with --output.", file=sys.stderr)
        return 1
    meta = data["meta"]
    if meta["data_date"] is None and args.from_data:
        print(f"Wrote {output}: the snapshots in {args.from_data}/ hold no readable listings, so the page will be empty.")
    elif meta["data_date"] is None:
        print(f"Wrote {output}: the export has no listings, so the page will be empty. "
              f"Run '{refresh}' after scraping, then run this again.")
    else:
        print(f"Wrote {output}: {meta['properties']:,} properties, data as of {meta['data_date']} "
              f"({output.stat().st_size / 1e6:.1f} MB).")
    print("Open it in a web browser; it works offline. Choose homes there and use "
          "'Download shortlist' to save or send only those. Have a shortlist or saved search from an "
          "earlier page? Use 'Bring in choices' in this page to carry its homes and notes over.")
    return 0


def snapshot_catalogue(folder: Path, output: Path) -> dict | None:
    """Page payload from raw snapshots via a throwaway export; None after printing the error."""
    unchanged = f"No page was written; {output} is unchanged." if output.exists() else "No page was written."
    if not folder.is_dir():
        print(f"Error: No folder {folder}. Run this from the scraping_listings checkout, where the saved "
              "snapshots are in data/, or pass the folder that holds the *_details_*.csv files.", file=sys.stderr)
        return None
    if not any(folder.glob("*_details_*.csv")):
        print(f"Error: No saved snapshots (*_details_*.csv files) in {folder}/. Pass the folder that holds them; "
              "in the scraping_listings checkout that is data.", file=sys.stderr)
        return None
    print(f"Reading the saved snapshots in {folder}/ (this can take a minute or more)...", flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix="browse-") as scratch:
            export = Path(scratch) / "listings.csv"
            stats = export_listings(export, folder)
            data = catalogue_data(load_listings(export))
    except OSError as error:
        name = f" {error.filename}" if error.filename else ""
        print(f"Error: Cannot read{name}: {error.strerror or error}. Check that you can read the files in "
              f"{folder}/. {unchanged}", file=sys.stderr)
        return None
    except (ValueError, csv.Error) as error:
        print(f"Error: {error} {unchanged}", file=sys.stderr)
        return None
    print(f"Read {stats['files_read']:,} snapshot files in {stats['elapsed_seconds']:.1f}s; skipped "
          f"{stats['dropped_homepages']:,} homepage rows; the newest saved row wins for each home.")
    return data


def print_history_summary(meta: dict, elapsed: float) -> None:
    first, last = (meta[key][:10] if meta[key] else "none" for key in ("first_observation", "last_observation"))
    print(f"Saved asking prices: {meta['files']:,} snapshot files, {first} to {last}, read in {elapsed:.1f}s.")
    print(f"  {meta['priced_rows']:,} priced rows used; skipped {meta['homepage_rows']:,} homepage rows and "
          f"{meta['unknown_price_rows']:,} rows without a readable price (shown as gaps).")
    print(f"  {meta['with_history']:,} of the homes on the page are in the archive; "
          f"{meta['change_events']:,} observed price changes across {meta['changed_listings']:,} listings in the archive; "
          f"{meta['conflict_runs']:,} conflicting saved runs are shown but not compared.")
    if meta["price_differs"]:
        print(f"  {meta['price_differs']:,} homes show a page price that differs from their latest saved price in the "
              "archive; the page says so on those homes.")
    if meta["after_archive"]:
        print(f"  {meta['after_archive']:,} homes were last seen after the newest snapshot in this folder; "
              "their history may be incomplete. Point --history-data at the folder the export was made from.")
    if meta["before_archive"]:
        print(f"  {meta['before_archive']:,} homes were last seen before the oldest snapshot in this folder; "
              "their saved prices are from later than the export. Point --history-data at the folder the export was made from.")
