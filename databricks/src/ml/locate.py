"""Daily: give each scraped neighborhood name a barrio on the map, in each city.

Runs as a Databricks job task after the pipeline refresh. Writes two tables for
the dashboard maps:
- neighborhood_locations: each scraped name (per city), its point and its barrio.
- barrio_shapes: the outline of each barrio (per city), as GeoJSON text.

The listings only have a neighborhood name, typed by each advertiser. The names
are matched, city by city, to public sources stored in the `reference` volume:
- Bogota: SECTOR.geojson, the cadastral sectors (barrios), from
  https://datosabiertos.bogota.gov.co/dataset/sector-catastral, and osm_places.json,
  OpenStreetMap neighbourhood, suburb and quarter points (Overpass query in the README).
- Medellin: medellin_barrios.geojson, the official barrios and veredas
  (Alcaldia de Medellin, layer "Limite Barrio Vereda Catastral").
- Envigado and Sabaneta: envigado_sabaneta_barrios.geojson, approximate barrio areas
  built by scripts/barrio_areas.py from points with a barrio name that the towns publish
  (neither publishes outlines).
- Medellin, Envigado and Sabaneta: aburra_osm.geojson, OpenStreetMap places and town
  outlines of the Aburra valley, built by scripts/aburra_osm.py. A place counts for
  the town it is in.
OpenStreetMap places have many market names; a market name gets the barrio its
point falls in. Names that match nothing stay off the map.
"""

import argparse
import difflib
import json
import re
import unicodedata

ARTICLES = {"el", "la", "los", "las"}
# Words before a name that do not tell barrios apart: "vereda la doctora" is "la doctora",
# "loma del escobero" is "vereda el escobero".
LEAD_WORDS = ARTICLES | {"vereda", "barrio", "sector", "urbanizacion", "loma", "alto", "de", "del"}
# A fuzzy match must not swap these: "santa ana oriental" is not "santa ana occidental".
DIRECTIONS = {"norte", "sur", "oriental", "occidental", "alto", "alta", "bajo", "baja", "central"}
# Bogota localidades, written after a barrio name: "santa barbara occidental usaquen".
LOCALIDADES = ["usaquen", "chapinero", "suba", "engativa", "fontibon", "kennedy", "teusaquillo",
               "barrios unidos", "puente aranda", "santa fe", "candelaria", "bosa"]
# A city name alone says nothing about where in the city.
NOT_A_PLACE = {"", "bogota", "bogota dc", "bogota d c", "medellin", "envigado", "sabaneta", "antioquia"}
# Urban Bogota. Rural sectors (veredas) share names with city barrios.
SOUTH, NORTH, WEST, EAST = 4.45, 4.85, -74.25, -73.98


def norm(name) -> str:
    """Lowercase, no accents or punctuation: "Chicó Norte II" -> "chico norte ii"."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text)).strip()


def bare(key: str) -> str:
    """Without leading articles and lead words: "los rosales" -> "rosales",
    "loma del escobero" -> "escobero"."""
    words = key.split()
    while len(words) > 1 and words[0] in LEAD_WORDS:
        words = words[1:]
    return " ".join(words)


def in_city(lat, lon) -> bool:
    return SOUTH < lat < NORTH and WEST < lon < EAST


def polygons(geometry) -> list:
    return [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]


def centroid(geometry) -> tuple:
    """(lat, lon) of the center of the largest polygon."""
    best = None
    for polygon in polygons(geometry):
        ring = polygon[0]
        area = cx = cy = 0.0
        for (x0, y0), (x1, y1) in zip(ring, ring[1:], strict=False):
            cross = x0 * y1 - x1 * y0
            area += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        if area and (best is None or abs(area) > abs(best[0])):
            best = (area, cx / (3 * area), cy / (3 * area))
    return best[2], best[1]


def contains(geometry, lat, lon) -> bool:
    """Is the point inside the outer ring of any of the polygons (ray casting)?"""
    for polygon in polygons(geometry):
        ring, inside = polygon[0], False
        for (x0, y0), (x1, y1) in zip(ring, ring[1:], strict=False):
            if (y0 > lat) != (y1 > lat) and lon < x0 + (lat - y0) * (x1 - x0) / (y1 - y0):
                inside = not inside
        if inside:
            return True
    return False


def bounding_box(geometry) -> tuple:
    """(south, north, west, east)."""
    lons, lats = zip(*(xy for polygon in polygons(geometry) for xy in polygon[0]), strict=True)
    return min(lats), max(lats), min(lons), max(lons)


def city_sectors(sectors: dict, code: str = "SCACODIGO", name: str = "SCANOMBRE", keep=in_city) -> list:
    """[(code, name, geometry, (lat, lon))] for the sectors whose center passes `keep`.
    The defaults read Bogota's cadastral sectors and keep urban Bogota."""
    out = []
    for feature in sectors["features"]:
        point = centroid(feature["geometry"])
        if keep(*point):
            props = feature["properties"]
            out.append((str(props[code]), props[name], feature["geometry"], point))
    return out


def osm_elements(features: list) -> list:
    """OpenStreetMap GeoJSON features as Overpass elements: a point (or area center) and tags."""
    out = []
    for feature in features:
        geometry = feature["geometry"]
        lat, lon = (geometry["coordinates"][1], geometry["coordinates"][0]) if geometry["type"] == "Point" \
            else centroid(geometry)
        out.append({"lat": lat, "lon": lon, "tags": feature["properties"]})
    return out


def in_town(features: list, city: str):
    """Test for a point being in the town, from its OpenStreetMap outline (admin level 6)."""
    town = next(f["geometry"] for f in features
                if f["properties"].get("admin_level") == "6" and norm(f["properties"].get("name")) == city)
    return lambda lat, lon: contains(town, lat, lon)


def load_points(sectors: list, osm: dict, keep=in_city) -> dict:
    """{normalized name: (lat, lon, name, sector code)}. Official names first, then OSM names
    whose point passes `keep`; their sector is the one their point falls in (None if none)."""
    points = {}
    boxes = [bounding_box(geometry) for _, _, geometry, _ in sectors]
    for code, name, _, (lat, lon) in sectors:
        points.setdefault(norm(name), (lat, lon, name, code))
    for element in osm["elements"]:
        lat = element.get("lat") or element.get("center", {}).get("lat")
        lon = element.get("lon") or element.get("center", {}).get("lon")
        if lat is None or not keep(lat, lon):
            continue
        names = [n.strip() for tag in ["name", "alt_name", "official_name", "old_name", "short_name"]
                 for n in (element["tags"].get(tag) or "").split(";") if n.strip()]
        new = [n for n in names if norm(n) not in points]
        if new:
            code = next((c for (c, _, geometry, _), (s, n, w, e) in zip(sectors, boxes, strict=True)
                         if s <= lat <= n and w <= lon <= e and contains(geometry, lat, lon)), None)
            for name in new:
                points.setdefault(norm(name), (lat, lon, name, code))
    return points


class Locator:
    def __init__(self, points: dict, suffixes: list = ()):
        self.points = points
        self.suffixes = suffixes
        self.names = sorted(points)
        self.by_bare = {}
        for key in self.names:
            self.by_bare.setdefault(bare(key), key)

    def match(self, name):
        """(key in points, rule) for a scraped name, or (None, "none"). Rules from safest to loosest."""
        key = norm(name)
        if key in NOT_A_PLACE:
            return None, "none"
        if key in self.points:
            return key, "exact"
        if bare(key) in self.by_bare:  # "rosales" -> "los rosales"
            return self.by_bare[bare(key)], "article"
        for suffix in self.suffixes:  # "santa barbara occidental usaquen"
            head = key.removesuffix(" " + suffix)
            if head != key and head in self.points:
                return head, "localidad"
        starts = [x for x in self.names if x.startswith(key + " ")]
        if starts:  # "chico norte ii" -> "chico norte ii sector"
            return starts[0], "prefix"
        for close in difflib.get_close_matches(key, self.names, n=3, cutoff=0.88):
            if set(key.split()) & DIRECTIONS == set(close.split()) & DIRECTIONS:
                return close, "spelling"  # "antiguo contry" -> "antiguo country"
        inside = [x for x in self.names if f" {key} " in f" {x} "]
        if len(inside) == 1:  # "hayuelos" -> "ciudad hayuelos"
            return inside[0], "contains"
        words = key.split()
        if len(words) >= 3 or (len(words) == 2 and words[0] not in ARTICLES):
            # "chico la cabrera" -> "la cabrera", "los rosales chico" -> "los rosales"
            for rest in [" ".join(words[1:]), " ".join(words[:-1])]:
                if rest in self.points:
                    return rest, "word dropped"
                if len(bare(rest).split()) > 1 and bare(rest) in self.by_bare:
                    return self.by_bare[bare(rest)], "word dropped"
        return None, "none"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--sources-dir", required=True, help="Folder with the map sources listed above")
    return parser.parse_args()


def main():
    import pandas as pd
    from pyspark.sql import SparkSession

    args = parse_args()
    spark = SparkSession.builder.getOrCreate()
    prefix = f"{args.catalog}.{args.schema}"
    def read(name):
        with open(f"{args.sources_dir}/{name}") as source:
            return json.load(source)

    def anywhere(lat, lon):
        return True

    # Each city: its barrio outlines, the extra names, where those must fall, name suffixes.
    aburra = read("aburra_osm.geojson")["features"]
    places = {"elements": osm_elements([f for f in aburra if f["properties"].get("admin_level") != "6"])}
    medellin = read("medellin_barrios.geojson")
    medellin["features"].sort(key=lambda f: f["properties"]["indicador_ur"] != "U")  # urban name first
    approximate = read("envigado_sabaneta_barrios.geojson")["features"]
    cities = {
        "bogota": (city_sectors(read("SECTOR.geojson")), read("osm_places.json"), in_city, LOCALIDADES),
        "medellin": (city_sectors(medellin, code="codigo", name="nombre_barrio", keep=anywhere),
                     places, in_town(aburra, "medellin"), ()),
    }
    for city in ["envigado", "sabaneta"]:
        mine = {"features": [f for f in approximate if f["properties"]["city"] == city]}
        cities[city] = (city_sectors(mine, code="code", name="name", keep=anywhere), places, in_town(aburra, city), ())

    counts = spark.sql(
        f"SELECT city, neighborhood, COUNT(*) AS n FROM (SELECT city, neighborhood FROM {prefix}.gold_rent_listings "
        f"UNION ALL SELECT city, neighborhood FROM {prefix}.gold_sale_listings) GROUP BY 1, 2"
    ).toPandas()
    rows, shapes = [], []
    for city, (sectors, osm, keep, suffixes) in cities.items():
        locator = Locator(load_points(sectors, osm, keep), suffixes)
        sector_names = {code: name for code, name, _, _ in sectors}
        shapes += [(city, code, name, json.dumps(geometry)) for code, name, geometry, _ in sectors]
        mine = counts[counts["city"] == city]
        found = 0
        for name, n in zip(mine["neighborhood"], mine["n"], strict=True):
            key, rule = locator.match(name)
            if key:
                lat, lon, official, code = locator.points[key]
                rows.append((city, name, official, round(lat, 6), round(lon, 6), rule, code,
                             sector_names.get(code)))
                found += n if code else 0
        print(f"{city}: {len(sectors)} barrio outlines, {found / mine['n'].sum():.0%} of listings in one")
    columns = ["city", "neighborhood", "map_name", "latitude", "longitude", "match_rule", "sector_code", "sector_name"]
    for table, frame in [
        ("neighborhood_locations", pd.DataFrame(rows, columns=columns)),
        ("barrio_shapes", pd.DataFrame(shapes, columns=["city", "sector_code", "sector_name", "geojson"])),
    ]:
        (
            spark.createDataFrame(frame)
            .write.mode("overwrite")
            .option("overwriteSchema", "true")
            .saveAsTable(f"{prefix}.{table}")
        )


if __name__ == "__main__":
    main()
