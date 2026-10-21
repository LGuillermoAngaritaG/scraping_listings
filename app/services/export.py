"""Conservative numeric parsers for the saved listing information dictionaries."""

import ast
import csv
import math
import os
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


_NUMBER = r"\d+(?:[.,]\d+)*"
_HOMEPAGE_TITLES = {
    "el portal inmobiliario #1 de colombia",
    "lo mejor de buscar es encontrar",
}
EXPORT_COLUMNS = (
    "source", "url", "title", "location", "price", "bedrooms", "bathrooms",
    "area_m2", "estrato", "first_seen", "last_seen",
)


def is_wrong_page(information: dict) -> bool:
    """Identify known homepage responses without rejecting repeated listing titles."""
    return str(information.get("title", "")).strip().casefold() in _HOMEPAGE_TITLES


def _text(value) -> str:
    return " ".join(str(value).replace("|", " ").split()).casefold()


def _number(value, pattern: str, *, count: bool = False, positive: bool = False):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str):
        match = re.fullmatch(pattern, _text(value))
        if not match:
            return None
        token = match.group("number")
        if "," in token and "." in token:
            # Colombian grouping dots and decimal comma.
            token = token.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token):
            token = token.replace(".", "").replace(",", "")
        else:
            token = token.replace(",", ".")
        try:
            number = float(token)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        return None
    if count and not float(number).is_integer():
        return None
    return int(number) if float(number).is_integer() else number


def parse_numeric_fields(information: dict) -> dict:
    """Return numbers or None; reject ranges and units belonging to another field."""
    number = rf"(?P<number>{_NUMBER})"
    bedroom = rf"{number}\s*(?:hab(?:s?\.?|itaci[oó]n(?:es)?))?"
    bathroom = rf"{number}\s*(?:bañ(?:o(?:\s*s)?|\.))?"
    bedrooms = information.get("bedrooms")
    bathrooms = information.get("bathrooms")
    # Metrocuadrado stores counts together rather than in separate keys.
    for spec in str(information.get("main_specs", "")).split("|"):
        if bedrooms is None and "hab" in _text(spec) and re.fullmatch(bedroom, _text(spec)):
            bedrooms = spec
        if bathrooms is None and "bañ" in _text(spec) and re.fullmatch(bathroom, _text(spec)):
            bathrooms = spec
    estrato = _number(information.get("estrato"), rf"(?:estrato\s*)?{number}", count=True)
    return {
        "price": _number(information.get("price", information.get("pricing")),
                         rf"(?:\$\s*|cop\s*)?{number}", positive=True),
        "bedrooms": _number(bedrooms, bedroom, count=True),
        "bathrooms": _number(bathrooms, bathroom, count=True),
        "area_m2": _number(information.get("area"),
                           rf"(?:área construida\s*)?{number}\s*(?:m²|m2)?", positive=True),
        "estrato": estrato if estrato is not None and 1 <= estrato <= 6 else None,
    }


def _seen_at(value: str, path: Path) -> datetime:
    try:
        seen = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        try:
            seen = datetime.strptime(path.stem.rsplit("_details_", 1)[1], "%Y%m%d_%H%M%S")
        except ValueError as error:
            raise ValueError(f"Cannot date rows in {path}; fix date_time or the filename timestamp.") from error
    return seen.replace(tzinfo=timezone.utc) if seen.tzinfo is None else seen.astimezone(timezone.utc)


def _raw_homepage(raw: str) -> bool:
    # Saved information uses dict repr; match the title field, not a description mentioning it.
    folded = raw.casefold()
    return any(f"{quote}title{quote}: {quote}{title}{quote}" in folded
               for title in _HOMEPAGE_TITLES for quote in ("'", '"'))


def export_listings(output: str | Path = "data/listings.csv", data_dir: str | Path = "data") -> dict:
    """Export latest valid values per URL, decoding only candidate value suppliers."""
    started = time.perf_counter()
    output, data_dir = Path(output), Path(data_dir)
    files = sorted(data_dir.glob("*_details_*.csv"),
                   key=lambda path: path.name.rsplit("_details_", 1)[1], reverse=True)
    if not files:
        raise ValueError(f"No details CSV files found in {data_dir}/. Run 'scrape --list', then scrape a name first.")
    raw_paths = {path.resolve() for path in files}
    raw_paths.update(path.resolve() for path in data_dir.glob("*_pages_*.csv"))
    if output.resolve() in raw_paths:
        raise ValueError(f"Output {output} is a raw CSV. Choose a different --output path to preserve it.")

    csv.field_size_limit(sys.maxsize)
    selected, sightings = {}, {}
    dropped = rows_read = decoded = 0
    for path in files:
        source = path.name.rsplit("_details_", 1)[0]
        with path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            if not {"url", "information"}.issubset(reader.fieldnames or []):
                raise ValueError(f"Invalid details CSV {path}; expected url and information columns.")
            try:
                # A later row wins a timestamp tie within the same saved run.
                rows = list(enumerate(reader, start=2))
            except csv.Error as error:
                raise ValueError(f"Cannot read {path}: {error}. Check the raw CSV format.") from error
        for line, row in reversed(rows):
            rows_read += 1
            url, raw = row.get("url"), row.get("information")
            if not url or not url.strip() or raw is None:
                raise ValueError(f"Invalid row {line} in {path}; url and information must be present.")
            seen = _seen_at(row.get("date_time"), path)
            first, last = sightings.get(url, (seen, seen))
            sightings[url] = (min(first, seen), max(last, seen))
            if _raw_homepage(raw):
                dropped += 1
                continue
            if url in selected and selected[url][0] >= seen:
                continue
            try:
                info = ast.literal_eval(raw)
                if not isinstance(info, dict):
                    raise ValueError("information is not a dictionary")
            except (ValueError, SyntaxError) as error:
                raise ValueError(f"Invalid information in {path}, row {line}; expected a Python dictionary. Fix this row before exporting.") from error
            decoded += 1
            if is_wrong_page(info):
                dropped += 1
                continue
            selected[url] = (seen, {
                "source": source, "url": url, "title": info.get("title"),
                "location": info.get("location"), **parse_numeric_fields(info),
            })

    output.parent.mkdir(parents=True, exist_ok=True)
    filled, sources = Counter(), Counter()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8",
                                         dir=output.parent, delete=False) as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=EXPORT_COLUMNS)
            writer.writeheader()
            for url in sorted(selected):
                record = selected[url][1]
                first, last = sightings[url]
                record.update(first_seen=first.isoformat(), last_seen=last.isoformat())
                writer.writerow(record)
                sources[record["source"]] += 1
                filled.update(field for field, value in record.items() if value is not None and value != "")
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"rows": len(selected), "sources": dict(sources), "filled": dict(filled),
            "dropped_homepages": dropped, "rows_read": rows_read, "decoded_rows": decoded,
            "files_read": len(files), "output": str(output),
            "elapsed_seconds": time.perf_counter() - started}


def print_export_summary(stats: dict) -> None:
    total = stats["rows"]
    print(f"Export summary: {total} listings written from {stats['files_read']} details CSVs")
    for source, count in sorted(stats["sources"].items()):
        print(f"  {source}: {count} listings")
    print(f"Dropped {stats['dropped_homepages']} homepage rows (known homepage titles; older valid values used when available).")
    for field in EXPORT_COLUMNS:
        count = stats["filled"].get(field, 0)
        percent = 100 * count / total if total else 0
        print(f"  {field}: {count}/{total} filled ({percent:.1f}%)")
    print(f"Output CSV: {stats['output']}")
    print(f"Export completed in {stats['elapsed_seconds']:.1f}s")
