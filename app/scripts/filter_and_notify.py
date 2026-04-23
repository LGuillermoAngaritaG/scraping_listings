"""Filter property listings and notify via email when new matches appear."""
from __future__ import annotations

import ast
import csv
import logging
import os
import re
import smtplib
import unicodedata
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PRICE_MIN = 1_500_000
PRICE_MAX = 3_000_000
MIN_AREA_M2 = 50.0
REQUIRED_BEDROOMS = 3
REQUIRED_BATHROOMS = 2
APARTMENT_FEATURE_KEYWORDS = ("balcon", "terraza")
HOUSE_FEATURE_KEYWORDS = ("balcon", "patio", "terraza", "jardin")
NEGATIVE_TOKENS = {"ninguno", "ninguna", "sin", "no", "0"}


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@dataclass
class Listing:
    url: str
    canonical_url: str
    title: str
    location: str
    price: Optional[int]
    bedrooms: Optional[int]
    bathrooms: Optional[int]
    area_m2: Optional[float]
    estrato: Optional[int]
    property_type: Optional[str]
    description: str = ""
    details: Dict[str, str] = field(default_factory=dict)
    characteristics: Dict[str, str] = field(default_factory=dict)
    source_file: str = ""
    scraped_at: Optional[str] = None

    def combined_feature_entries(self) -> Iterable[Tuple[str, str]]:
        for key, value in self.details.items():
            yield key, value
        for key, value in self.characteristics.items():
            yield key, value

    def combined_text(self) -> str:
        parts: List[str] = [self.title, self.location, self.description]
        for key, value in self.combined_feature_entries():
            parts.append(f"{key} {value}")
        return " ".join(part for part in parts if part)


class ListingFilter:
    def __init__(self) -> None:
        self._listings_by_url: Dict[str, Listing] = {}

    def process_directory(self, data_dir: Path) -> None:
        for csv_path in sorted(data_dir.glob("*_details_*.csv")):
            self._process_file(csv_path)

    @property
    def filtered(self) -> List[Listing]:
        return sorted(
            self._listings_by_url.values(),
            key=lambda listing: ((listing.price or 0), listing.canonical_url or listing.url),
        )

    def _process_file(self, csv_path: Path) -> None:
        logger.info("Processing %s", csv_path)
        with csv_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                listing = self._parse_row(row, csv_path)
                if listing and self._meets_criteria(listing):
                    self._add_listing(listing)

    def _add_listing(self, listing: Listing) -> None:
        key = listing.canonical_url or listing.url
        existing = self._listings_by_url.get(key)
        if not existing:
            self._listings_by_url[key] = listing
            return
        if is_later(listing.scraped_at, existing.scraped_at):
            self._listings_by_url[key] = listing

    def _parse_row(self, row: Dict[str, str], csv_path: Path) -> Optional[Listing]:
        try:
            info = ast.literal_eval(row.get("information", "") or "{}")
            if not isinstance(info, dict):
                return None
        except (SyntaxError, ValueError):
            logger.debug("Failed to parse information for %s", row.get("url"))
            return None

        details = self._flatten_key_value_list(info.get("details"))
        characteristics = self._flatten_key_value_list(info.get("characteristics"))
        property_type = self._determine_property_type(info, details)
        price = parse_price(info.get("pricing") or info.get("price"))
        bedrooms = self._extract_bedrooms(info, details)
        bathrooms = self._extract_bathrooms(info, details)
        area_m2 = parse_area(info.get("area"), details)
        estrato = parse_estrato(info.get("estrato"), details)

        return Listing(
            url=row.get("url", ""),
            canonical_url=canonicalize_url(row.get("url", "")),
            title=str(info.get("title", "")),
            location=str(info.get("location", "")),
            price=price,
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            area_m2=area_m2,
            estrato=estrato,
            property_type=property_type,
            description=str(info.get("description", "")),
            details=details,
            characteristics=characteristics,
            source_file=csv_path.name,
            scraped_at=row.get("date_time"),
        )

    def _flatten_key_value_list(self, values: Optional[Iterable]) -> Dict[str, str]:
        flattened: Dict[str, str] = {}
        if not values:
            return flattened
        for entry in values:
            if isinstance(entry, dict):
                for key, value in entry.items():
                    if key is None or value is None:
                        continue
                    key_str = str(key).strip()
                    value_str = str(value).strip()
                    if key_str and key_str not in flattened:
                        flattened[key_str] = value_str
        return flattened

    def _determine_property_type(self, info: Dict[str, object], details: Dict[str, str]) -> Optional[str]:
        candidates = [
            info.get("property_type"),
            details.get("Tipo de Inmueble"),
            details.get("Tipo de inmueble"),
            info.get("title"),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            lowered = normalize_text(str(candidate))
            if "apart" in lowered:
                return "apartamento"
            if "casa" in lowered:
                return "casa"
        return None

    def _extract_bedrooms(self, info: Dict[str, object], details: Dict[str, str]) -> Optional[int]:
        candidates = [
            info.get("bedrooms"),
            details.get("Habitaciones"),
            info.get("main_specs"),
        ]
        for candidate in candidates:
            value = parse_specific_number(str(candidate), r"(\d+)(?:\s*hab)")
            if value is not None:
                return value
            generic_value = parse_integer(candidate)
            if generic_value is not None:
                return generic_value
        return None

    def _extract_bathrooms(self, info: Dict[str, object], details: Dict[str, str]) -> Optional[int]:
        candidates = [
            info.get("bathrooms"),
            details.get("Baños"),
            info.get("main_specs"),
        ]
        for candidate in candidates:
            value = parse_specific_number(str(candidate), r"(\d+)(?:\s*ba)")
            if value is not None:
                return value
            generic_value = parse_integer(candidate)
            if generic_value is not None:
                return generic_value
        return None

    def _meets_criteria(self, listing: Listing) -> bool:
        if not listing.url:
            return False
        if listing.price is None or not (PRICE_MIN <= listing.price <= PRICE_MAX):
            return False
        if listing.estrato not in {3, 4}:
            return False
        if listing.bedrooms != REQUIRED_BEDROOMS:
            return False
        if listing.bathrooms != REQUIRED_BATHROOMS:
            return False
        if listing.area_m2 is None or listing.area_m2 < MIN_AREA_M2:
            return False
        if listing.property_type not in {"apartamento", "casa"}:
            return False

        keywords = (
            APARTMENT_FEATURE_KEYWORDS if listing.property_type == "apartamento" else HOUSE_FEATURE_KEYWORDS
        )
        has_feature, _ = detect_feature(listing, keywords)
        if not has_feature:
            return False
        return True


def parse_price(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    digits = re.sub(r"[^0-9]", "", str(raw))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def parse_estrato(primary: Optional[str], details: Dict[str, str]) -> Optional[int]:
    value = primary or details.get("Estrato") or details.get("estrato")
    if not value:
        return None
    digits = re.findall(r"\d+", str(value))
    if not digits:
        return None
    try:
        return int(digits[0])
    except ValueError:
        return None


def parse_area(raw: Optional[str], details: Dict[str, str]) -> Optional[float]:
    candidate = raw or details.get("Área Construida") or details.get("Area construida")
    if not candidate:
        return None
    match = re.search(r"(\d+[\.,]?\d*)", str(candidate))
    if not match:
        return None
    number = match.group(1)
    normalized = normalize_number(number)
    try:
        return float(normalized)
    except ValueError:
        return None


def parse_specific_number(value: Optional[str], pattern: str) -> Optional[int]:
    if not value:
        return None
    match = re.search(pattern, normalize_text(str(value)))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def parse_integer(value: Optional[object]) -> Optional[int]:
    if value is None:
        return None
    match = re.search(r"(\d+)", str(value))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def normalize_number(number: str) -> str:
    number = number.strip()
    if "." in number and "," in number:
        number = number.replace(".", "").replace(",", ".")
    elif "." in number:
        integer, _, fraction = number.partition(".")
        if len(fraction) <= 2:
            number = f"{integer}.{fraction}"
        else:
            number = integer + fraction
    else:
        number = number.replace(",", ".")
    return number


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return without_accents.lower()


def canonicalize_url(url: Optional[str]) -> str:
    if not url:
        return ""
    return str(url).split("?", 1)[0]


def detect_feature(listing: Listing, keywords: Sequence[str]) -> Tuple[bool, Optional[str]]:
    for key, value in listing.combined_feature_entries():
        key_norm = normalize_text(key)
        value_norm = normalize_text(value)
        if contains_negative(key_norm) or contains_negative(value_norm):
            continue
        for keyword in keywords:
            if keyword in key_norm or keyword in value_norm:
                return True, keyword
    combined_text = normalize_text(listing.combined_text())
    for keyword in keywords:
        if keyword in combined_text and not has_negative_context(combined_text, keyword):
            return True, keyword
    return False, None


def contains_negative(text: str) -> bool:
    return any(token in text for token in NEGATIVE_TOKENS)


def has_negative_context(text: str, keyword: str) -> bool:
    window_size = 8
    index = text.find(keyword)
    while index != -1:
        start = max(0, index - window_size)
        snippet = text[start : index + len(keyword)]
        if any(token in snippet for token in NEGATIVE_TOKENS):
            return True
        index = text.find(keyword, index + 1)
    return False


def is_later(current: Optional[str], existing: Optional[str]) -> bool:
    if current is None:
        return False
    if existing is None:
        return True
    return str(current) > str(existing)


def read_previous_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()
    urls: set[str] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            url = row.get("canonical_url") or row.get("url")
            if url:
                urls.add(canonicalize_url(url))
    return urls


def write_filtered_listings(path: Path, listings: Sequence[Listing]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "url",
        "canonical_url",
        "title",
        "location",
        "price_cop",
        "bedrooms",
        "bathrooms",
        "area_m2",
        "estrato",
        "property_type",
        "source_file",
        "scraped_at",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for listing in listings:
            writer.writerow(
                {
                    "url": listing.url,
                    "canonical_url": listing.canonical_url,
                    "title": listing.title,
                    "location": listing.location,
                    "price_cop": listing.price,
                    "bedrooms": listing.bedrooms,
                    "bathrooms": listing.bathrooms,
                    "area_m2": listing.area_m2,
                    "estrato": listing.estrato,
                    "property_type": listing.property_type,
                    "source_file": listing.source_file,
                    "scraped_at": listing.scraped_at,
                }
            )


def send_email_notification(listings: Sequence[Listing]) -> None:
    email_enabled = os.getenv("FILTER_EMAIL_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    sender = os.getenv("FILTER_EMAIL_SENDER")
    password = os.getenv("FILTER_EMAIL_PASSWORD")
    recipients = [addr.strip() for addr in os.getenv("FILTER_EMAIL_RECIPIENTS", "").split(",") if addr.strip()]
    smtp_server = os.getenv("FILTER_EMAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("FILTER_EMAIL_SMTP_PORT", "587"))
    subject = os.getenv(
        "FILTER_EMAIL_SUBJECT",
        "Nuevas propiedades que cumplen los filtros",
    )

    if not email_enabled:
        logger.info("Email notifications are disabled")
        return
    if not sender or not password or not recipients:
        logger.info("Email credentials or recipients not configured; skipping notification")
        return
    if not listings:
        logger.info("No new listings to notify")
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    body_lines = [
        "Se encontraron nuevas propiedades que cumplen los filtros establecidos:",
        "",
    ]
    for listing in listings:
        body_lines.append(
            " - {title} ({location})\n   Precio: ${price:,.0f} COP\n   Habitaciones: {bedrooms}, Baños: {bathrooms}, Área: {area:.1f} m²\n   URL: {url}".format(
                title=listing.title or "Sin título",
                location=listing.location or "Sin ubicación",
                price=listing.price or 0,
                bedrooms=listing.bedrooms or 0,
                bathrooms=listing.bathrooms or 0,
                area=listing.area_m2 or 0.0,
                url=listing.url,
            )
        )
    message.set_content("\n".join(body_lines))

    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(message)
        logger.info("Email notification sent to %s", ", ".join(recipients))
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send email notification: %s", exc)


def main() -> None:
    configure_logging()
    data_dir = Path(os.getenv("FILTER_DATA_DIR", "data"))
    output_dir = Path(os.getenv("FILTER_OUTPUT_DIR", "analysis"))
    output_filename = os.getenv("FILTER_OUTPUT_FILENAME", "filtered_listings.csv")
    output_path = output_dir / output_filename

    listing_filter = ListingFilter()
    listing_filter.process_directory(data_dir)

    previous_urls = read_previous_urls(output_path)
    filtered_listings = listing_filter.filtered
    current_urls = {listing.canonical_url or listing.url for listing in filtered_listings}
    new_urls = current_urls - previous_urls
    new_listings = [
        listing
        for listing in filtered_listings
        if (listing.canonical_url or listing.url) in new_urls
    ]

    write_filtered_listings(output_path, filtered_listings)

    send_email_notification(new_listings)


if __name__ == "__main__":
    main()
