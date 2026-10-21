"""Conservative numeric parsers for the saved listing information dictionaries."""

import math
import re


_NUMBER = r"\d+(?:[.,]\d+)*"
_HOMEPAGE_TITLES = {
    "el portal inmobiliario #1 de colombia",
    "lo mejor de buscar es encontrar",
}


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
