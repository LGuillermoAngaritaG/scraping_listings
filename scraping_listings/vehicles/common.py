"""Row format shared by all dealer sites, and the helpers that turn raw values into it."""

import re
import unicodedata

COLUMNS = [
    "source", "listing_id", "url", "title", "brand", "line", "version", "year", "km",
    "price_cop", "transmission", "fuel", "body_type", "engine_cc", "color", "plate_digit",
    "city", "branch", "scraped_at",
]

BRAND_ALIASES = {
    "mercedes benz": "mercedes-benz",
    "mercedes": "mercedes-benz",
    "cherry": "chery",
    "vw": "volkswagen",
    "dfsk/dfm/dfzl": "dfsk",
    "dfm/dfsk": "dfsk",
}

# Brands written with two words, so a title like "Land Rover Discovery" splits right.
MULTI_WORD_BRANDS = ("land rover", "mercedes benz", "alfa romeo", "great wall", "aston martin")

# Spelling variants of the same model -> one name.
LINE_ALIASES = {
    "t cross": "t-cross", "tcross": "t-cross",
    "c elysee": "c-elysee", "celysee": "c-elysee",
    "x trail": "x-trail", "xtrail": "x-trail",
    "cx3": "cx-3", "cx 3": "cx-3", "cx30": "cx-30", "cx 30": "cx-30",
    "cx5": "cx-5", "cx 5": "cx-5", "cx50": "cx-50", "cx 50": "cx-50",
    "cx60": "cx-60", "cx 60": "cx-60", "cx9": "cx-9", "cx90": "cx-90",
    "mx5": "mx-5", "mx 5": "mx-5", "mx30": "mx-30", "mx 30": "mx-30",
    "hrv": "hr-v", "crv": "cr-v", "wrv": "wr-v", "brv": "br-v",
    "bt50": "bt-50", "bt 50": "bt-50", "dmax": "d-max", "d max": "d-max",
    "f150": "f-150", "f 150": "f-150", "rav 4": "rav4", "chr": "c-hr", "c hr": "c-hr",
    "s cross": "s-cross", "scross": "s-cross",
    "cs-55": "cs55", "cs 55": "cs55", "cs-35": "cs35", "cs 35": "cs35",
    "rocks": "rocks-e", "rocks e": "rocks-e",
    "soprtage": "sportage",
}

# Models some sites cut at the hyphen ("T" for T-Cross), by brand.
TRUNCATED_LINES = {
    ("volkswagen", "t"): "t-cross",
    ("honda", "hr"): "hr-v",
    ("honda", "cr"): "cr-v",
    ("honda", "wr"): "wr-v",
    ("honda", "br"): "br-v",
    ("nissan", "x"): "x-trail",
    ("mazda", "bt"): "bt-50",
    ("toyota", "c"): "c-hr",
    ("suzuki", "s"): "s-cross",
    ("chevrolet", "d"): "d-max",
    ("ford", "f"): "f-150",
}

# Models of two or more words. When a site puts the second word in the version
# ("Grand" + "Vitara GLX"), it is moved back into the line.
COMPOUND_LINES = {
    "grand vitara", "grand cherokee", "grand i10", "grand santa fe", "grand caravan",
    "corolla cross", "yaris cross", "k3 cross", "eclipse cross",
    "c4 cactus", "c3 aircross", "c5 aircross",
    "discovery sport", "range rover", "range rover sport", "range rover evoque",
    "range rover velar", "bronco sport", "outlander sport",
    "land cruiser", "land cruiser prado", "santa fe",
    "model 3", "model s", "model x", "model y",
    "tiggo 2", "tiggo 3", "tiggo 4", "tiggo 5", "tiggo 7", "tiggo 8",
    "song plus", "song pro", "qin plus", "yuan plus", "seal u", "dolphin mini",
    "serie 1", "serie 2", "serie 3", "serie 4", "serie 5", "serie 7", "serie 8",
    "clase a", "clase b", "clase c", "clase e", "clase g", "clase s",
}

PICKUP_BRANDS = {"ram", "riddara"}
PICKUP_LINES = {
    "hilux", "ranger", "frontier", "np300", "navara", "d-max", "l200", "amarok", "alaskan",
    "tacoma", "tundra", "f-150", "oroch", "montana", "saveiro", "strada", "toro", "colorado",
    "s10", "bt-50", "tasman", "poer", "t60", "landtrek", "maverick", "gladiator", "triton",
    "tunland", "shark",
}
VAN_LINES = {
    "kangoo", "partner", "berlingo", "fiorino", "h1", "staria", "carnival", "sienna",
    "odyssey", "n300", "sprinter", "transit", "master", "trafic", "hiace", "urvan", "nv350",
    "doblo", "caddy",
}

# Values of a site's own body-type or class field.
BODY_FIELD = {
    "sedan": "sedan", "hatchback": "hatchback", "hatch back": "hatchback", "hb": "hatchback",
    "pickup": "pickup", "pick up": "pickup", "doble cabina": "pickup",
    "furgon": "van", "van": "van", "minivan": "van", "microbus": "van", "panel": "van",
    "monovolumen": "van",
    "coupe": "coupe", "convertible": "convertible", "cabriolet": "convertible",
    "suv": "suv", "campero": "suv", "crossover": "suv", "off-road": "suv", "todo terreno": "suv",
    "estacas": "otro", "camion": "otro", "cuadriciclo": "otro", "bus": "otro", "chasis": "otro",
}
# Words in a title or version that give the body type away, checked in this order.
BODY_WORDS = [
    ("pickup", r"\b(pick ?up|doble cabina|cabina doble|dc|d/c)\b"),
    ("van", r"\b(furgon|van|minivan|cargo|carrier)\b"),
    ("convertible", r"\b(convertible|cabriolet|roadster)\b"),
    ("coupe", r"\bcoupe\b"),
    ("station wagon", r"\b(station wagon|avant|variant|sw)\b"),
    ("sedan", r"\bsedan\b"),
    ("hatchback", r"\b(hatchback|hatch|hb|sportback)\b"),
]
# "5P" is five doors. Autoexpo's "7 p." (with a space) counts seats, so it does not match.
DOOR_WORDS = [("sedan", r"\b4p\b"), ("hatchback", r"\b[35]p\b"), ("coupe", r"\b2p\b")]

KNOWN_CITIES = {
    "bogota", "medellin", "cali", "barranquilla", "cartagena", "bucaramanga", "pereira",
    "manizales", "armenia", "ibague", "villavicencio", "neiva", "pasto", "cucuta", "tunja",
    "santa marta", "monteria", "valledupar", "popayan", "sincelejo", "yopal", "duitama",
    "sogamoso", "girardot", "fusagasuga", "palmira", "floridablanca", "chia", "cota", "funza",
    "mosquera", "madrid", "soacha", "zipaquira", "cajica", "envigado", "sabaneta", "itagui",
    "bello", "rionegro", "la estrella",
}


def norm(value) -> str:
    """Lowercase, no accents, single spaces."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def clean_text(value) -> str:
    """Original text with whitespace collapsed (titles, versions, branches)."""
    return " ".join(str(value or "").split())


def to_int(value) -> int | None:
    """Integer from 120800000, '$120.800.000' or '56.185 km'. None when there are no digits."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"\D", "", str(value))
    return int(digits) if digits else None


def to_cc(value) -> int | None:
    """Engine size in cc from 1.8, '1,8L', '1.462', '2.000 cc', '2000 cc' or 1598."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().lower().replace(" ", "")
    thousands = re.fullmatch(r"(\d{1,2})[.,](\d{3})(cc)?", text)
    if thousands:
        cc = int(thousands.group(1) + thousands.group(2))
    else:
        number = re.search(r"\d+(?:[.,]\d+)?", text)
        if not number:
            return None
        amount = float(number.group().replace(",", "."))
        cc = round(amount * 1000) if amount < 20 else round(amount)
    return cc or None


def cc_from_text(text) -> int | None:
    """Engine size from a title or version: '1200CC', '2400CCC', '1.6 AUT', '2.2td'."""
    t = norm(text)
    in_cc = re.search(r"(\d{3,4})\s*c{2,3}\b", t)
    if in_cc:
        return int(in_cc.group(1)) or None
    liters = re.search(r"(?<![\d.,])(\d[.,]\d)(?![\d.,])", t)
    return to_cc(liters.group(1)) if liters else None


def plate_digit(value) -> str:
    """Last digit of a plate or plate hint ('ABC123', 'XXXXX3', 'Terminada en 3', 'Ø' = 0)."""
    text = str(value or "").replace("Ø", "0").replace("ø", "0")
    digits = re.findall(r"\d", text)
    return digits[-1] if digits else ""


def canon_brand(value) -> str:
    brand = norm(value)
    return BRAND_ALIASES.get(brand, brand)


def split_brand(title) -> tuple[str, str]:
    """('Land Rover', 'Discovery Sport 2.0') from 'Land Rover Discovery Sport 2.0'."""
    words = clean_text(title).split()
    size = 2 if norm(" ".join(words[:2])).replace("-", " ") in MULTI_WORD_BRANDS else 1
    return " ".join(words[:size]), " ".join(words[size:])


def _word(value) -> str:
    return norm(value).strip(".,;:- ")


def split_model(brand, line, version="") -> tuple[str, str]:
    """(line, version): the model alone, and the rest of the name as text.

    Fixes seen on the dealer sites: 'New' or 'New Gol' as the line, models cut at the
    hyphen ('T' for T-Cross), spelling variants ('CX30', 'T Cross') and two-word models
    whose second word ended up in the version ('Grand' + 'Vitara GLX').
    """
    brand = canon_brand(brand)
    words = [w for w in (_word(x) for x in str(line or "").split()) if w]
    rest = str(version or "").split()
    while words and words[0] in ("new", "nuevo", "nueva"):
        words = words[1:]
    while not words and rest:
        first, rest = _word(rest[0]), rest[1:]
        words = [first] if first and first not in ("new", "nuevo", "nueva") else []
    model = " ".join(words)
    model = TRUNCATED_LINES.get((brand, model), model)
    model = LINE_ALIASES.get(model, model)
    while rest and f"{model} {_word(rest[0])}" in COMPOUND_LINES:
        model, rest = f"{model} {_word(rest[0])}", rest[1:]
    return model, clean_text(" ".join(rest)).strip(" .")


def canon_transmission(value) -> str:
    words = re.findall(r"[a-z]+", norm(value))
    auto = {
        "at", "aut", "secuencial", "tiptronic", "steptronic", "cvt", "ecvt", "ivt", "dct", "dsg",
    }
    manual = {"mt", "mec", "manual", "estandar"}
    if any(w.startswith("autom") or w in auto for w in words):
        return "automatica"
    if any(w.startswith("mecan") or w in manual for w in words):
        return "manual"
    return ""


def canon_fuel(value) -> str:
    t = norm(value)
    words = set(re.findall(r"[a-z]+", t))
    hybrid_words = {"hev", "mhev", "phev", "erev"}
    if re.search(r"hibrid|hybrid|gasoelec|enchufable", t) or words & hybrid_words:
        return "hibrido"
    if "electr" in t or words & {"bev", "ev"}:
        return "electrico"
    if "diesel" in t:
        return "diesel"
    if "gasolin" in t:
        return "gasolina"
    if words & {"gas", "gnv", "glp"}:
        return "gas"
    return ""


def _body_from_words(text, rules) -> str:
    t = norm(text)
    return next((body for body, pattern in rules if re.search(pattern, t)), "")


def canon_body_type(body="", clase="", text="", brand="", line="") -> str:
    """One of suv, sedan, hatchback, pickup, van, coupe, convertible, station wagon, otro.

    `body` is the site's body-type field, `clase` its class field (Camioneta, Automovil...),
    `text` the title or version. Empty when nothing gives it away.
    """
    b, c = norm(body).strip(". "), norm(clase).strip(". ")
    if b in BODY_FIELD:
        return BODY_FIELD[b]
    if b in ("wagon", "station wagon"):
        # The Colombian registry calls most SUVs "wagon"; a car-class wagon is a real one.
        return "station wagon" if c == "automovil" else "suv"
    if b and b != "camioneta" and _body_from_words(b, BODY_WORDS):  # e.g. "PICO (PICK UP)"
        return _body_from_words(b, BODY_WORDS)
    hint = _body_from_words(text, BODY_WORDS)
    is_pickup = brand in PICKUP_BRANDS or line in PICKUP_LINES
    if "camioneta" in (b, c):
        if hint in ("pickup", "van"):
            return hint
        return "pickup" if is_pickup else "van" if line in VAN_LINES else "suv"
    if c in BODY_FIELD:
        return BODY_FIELD[c]
    if hint:
        return hint
    if is_pickup:
        return "pickup"
    if line in VAN_LINES:
        return "van"
    return _body_from_words(text, DOOR_WORDS)


def canon_city(value) -> str:
    """'bogota' from 'Bogotá D.C.', 'BOGOTÁ. DC. - BOGOTÁ. D.C', 'bogota_dc' or an address."""
    t = norm(value).replace("_", " ")
    first = re.split(r"\s-\s|,|\(", t)[0]
    words = [w for w in re.sub(r"[^a-z ]", " ", first).split() if w not in ("d", "c", "dc")]
    if " ".join(words) in KNOWN_CITIES:
        return " ".join(words)
    # An address or a branch name: take the first known city in it.
    letters = " " + re.sub(r"[^a-z ]", " ", t) + " "
    found = [(letters.find(f" {city} "), city) for city in KNOWN_CITIES if f" {city} " in letters]
    return min(found)[1] if found else ""


def make_row(
    source: str,
    scraped_at: str,
    *,
    listing_id,
    url,
    title,
    brand,
    line,
    version="",
    year=None,
    km=None,
    price=None,
    transmission="",
    fuel="",
    body="",
    clase="",
    body_text="",
    engine_cc=None,
    color="",
    plate="",
    city="",
    branch="",
) -> dict:
    """Build one CSV row from raw site values. `plate` is only used for its last digit."""
    brand_name = canon_brand(brand)
    model, rest = split_model(brand_name, line, version)
    row = {
        "source": source,
        "listing_id": str(listing_id),
        "url": url or "",
        "title": clean_text(title),
        "brand": brand_name,
        "line": model,
        "version": rest,
        "year": to_int(year),
        "km": to_int(km),
        "price_cop": to_int(price),
        "transmission": canon_transmission(transmission),
        "fuel": canon_fuel(fuel),
        "body_type": canon_body_type(body, clase, body_text, brand_name, model),
        "engine_cc": to_cc(engine_cc),
        "color": norm(color),
        "plate_digit": plate_digit(plate),
        "city": canon_city(city),
        "branch": clean_text(branch),
        "scraped_at": scraped_at,
    }
    return {column: "" if row[column] is None else row[column] for column in COLUMNS}
