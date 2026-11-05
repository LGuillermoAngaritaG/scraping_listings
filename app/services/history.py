"""Saved asking-price observations per listing, read from raw details snapshots.

One point per listing per saved details file (a "saved run"). A run whose rows give two
different valid prices, or two files that give different prices at the same UTC time,
is a conflict: it is shown but never ordered into a change. Rows without a valid price
are gaps. Homepage responses are dropped.
"""

import ast
import csv
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.services.export import _raw_homepage, _seen_at, is_wrong_page, parse_numeric_fields


def canonical_url(url: str) -> str:
    """Same identity as search.prepare_listings: no query, no repeated path slashes."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, re.sub(r"/{2,}", "/", parts.path), "", ""))


def _points(by_file: dict) -> list[dict]:
    """by_file: file name -> [(seen, price or None, line)] for one listing."""
    prices_at = defaultdict(set)
    for observations in by_file.values():
        for seen, price, _ in observations:
            if price is not None:
                prices_at[seen].add(price)
    points = []
    for name, observations in by_file.items():
        priced = [o for o in observations if o[1] is not None]
        prices = sorted({o[1] for o in priced})
        same_time = any(len(prices_at[o[0]]) > 1 for o in priced)
        if not priced:
            first = last = min(observations)
            kind = "unknown"
        else:
            first, last = min(priced), max(priced)
            kind = "conflict" if len(prices) > 1 or same_time else "price"
            if same_time:
                prices = sorted(set().union(*(prices_at[o[0]] for o in priced)))
        points.append({"kind": kind, "prices": prices, "first": first, "last": last, "file": name})
    return sorted(points, key=lambda point: (point["first"][0], point["file"]))


def _runs(points: list[dict]) -> list[dict]:
    """Collapse consecutive points with the same kind and prices."""
    runs = []
    for point in points:
        ref = lambda observation: f"{point['file']}:{observation[2]}"
        if runs and (runs[-1]["kind"], runs[-1]["prices"]) == (point["kind"], point["prices"]) \
                and point["kind"] != "conflict":
            run = runs[-1]
            run["saved_runs"] += 1
        else:
            run = {"kind": point["kind"], "prices": point["prices"], "saved_runs": 1,
                   "first": point["first"][0].isoformat(), "first_ref": ref(point["first"])}
            runs.append(run)
        run.update(last=point["last"][0].isoformat(), last_ref=ref(point["last"]))
    return runs


def _changes(runs: list[dict]) -> list[dict]:
    """Compare neighbouring price runs; a conflict between them blocks the comparison."""
    changes, previous, gap = [], None, False
    for run in runs:
        if run["kind"] == "conflict":
            previous, gap = None, False
        elif run["kind"] == "unknown":
            gap = previous is not None
        else:
            if previous is not None and previous["prices"] != run["prices"]:
                changes.append({"from": previous["prices"][0], "from_at": previous["last"],
                                "to": run["prices"][0], "to_at": run["first"], "across_gap": gap})
            previous, gap = run, False
    return changes


def listing_history(by_file: dict) -> dict:
    runs = _runs(_points(by_file))
    valid = sum(run["saved_runs"] for run in runs if run["kind"] == "price")
    return {"state": "no_price" if valid == 0 else "single" if valid == 1 else "observed",
            "valid_runs": valid, "runs": runs, "changes": _changes(runs)}


def read_history(data_dir: str | Path, progress=None) -> dict:
    """Return {"listings": {canonical url: history}, "meta": coverage and counts}.

    Raises ValueError with a user-facing message for any input the history cannot trust.
    """
    started = time.perf_counter()
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise ValueError(f"History folder {data_dir} does not exist. Pass the folder with the saved "
                         "*_details_*.csv snapshots, usually --history-data data.")
    files = sorted(data_dir.glob("*_details_*.csv"))
    if not files:
        raise ValueError(f"No *_details_*.csv snapshots in {data_dir}. Pass the folder the scrapers "
                         "write to, usually --history-data data.")
    csv.field_size_limit(sys.maxsize)
    cache, observed = {}, defaultdict(lambda: defaultdict(list))
    counts = {"rows": 0, "homepage_rows": 0, "unknown_price_rows": 0, "priced_rows": 0}
    seen_range = []
    for number, path in enumerate(files, start=1):
        try:
            with path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                if not {"url", "information"}.issubset(reader.fieldnames or []):
                    raise ValueError(f"{path} is not a details snapshot; expected url and information columns.")
                for line, row in enumerate(reader, start=2):
                    url, raw = row.get("url"), row.get("information")
                    if not url or not url.strip() or raw is None:
                        raise ValueError(f"{path}, row {line}: url and information must be present.")
                    seen = _seen_at(row.get("date_time"), path)
                    counts["rows"] += 1
                    if raw not in cache:
                        if _raw_homepage(raw):
                            cache[raw] = None
                        else:
                            try:
                                info = ast.literal_eval(raw)
                            except (ValueError, SyntaxError, MemoryError, RecursionError):
                                info = None
                            if not isinstance(info, dict):
                                raise ValueError(f"{path}, row {line}: information is not a saved dictionary.")
                            cache[raw] = None if is_wrong_page(info) else (parse_numeric_fields(info)["price"],)
                    if cache[raw] is None:
                        counts["homepage_rows"] += 1
                        continue
                    price = cache[raw][0]
                    counts["unknown_price_rows" if price is None else "priced_rows"] += 1
                    observed[canonical_url(url)][path.name].append((seen, price, line))
                    seen_range = [min(seen_range[0], seen), max(seen_range[1], seen)] if seen_range else [seen, seen]
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(f"Cannot read {path}: {getattr(error, 'strerror', None) or error}.") from error
        except csv.Error as error:
            raise ValueError(f"Cannot read {path}: {error}. The file is not valid CSV.") from error
        if progress and (number % 250 == 0 or number == len(files)):
            progress(number, len(files))
    listings = {url: listing_history(by_file) for url, by_file in observed.items()}
    meta = {**counts, "files": len(files), "listings": len(listings),
            "first_observation": seen_range[0].isoformat() if seen_range else None,
            "last_observation": seen_range[1].isoformat() if seen_range else None,
            "conflict_runs": sum(run["kind"] == "conflict" for h in listings.values() for run in h["runs"]),
            "change_events": sum(len(h["changes"]) for h in listings.values()),
            "changed_listings": sum(bool(h["changes"]) for h in listings.values()),
            "elapsed_seconds": round(time.perf_counter() - started, 1)}
    return {"listings": listings, "meta": meta}
