"""Daily: give each scraped neighborhood name a barrio on the map.

Runs as a Databricks job task after the pipeline refresh. Writes two tables for
the dashboard map:
- neighborhood_locations: each scraped name, its point and its official barrio.
- barrio_shapes: the outline of each official barrio, as GeoJSON text.

The listings only have a neighborhood name, typed by each advertiser. The names
are matched to two public sources, stored in the `reference` volume:
- SECTOR.geojson: Bogota cadastral sectors (barrios), from
  https://datosabiertos.bogota.gov.co/dataset/sector-catastral (GeoJSON resource).
- osm_places.json: OpenStreetMap neighbourhood, suburb and quarter points in Bogota,
  which also have many market names (Overpass query in the README). A market name
  gets the official barrio its point falls in.
Names that match nothing (about a quarter of the listings) stay off the map.
"""

import argparse
import difflib
import json
import re
import unicodedata

ARTICLES = {"el", "la", "los", "las"}
# A fuzzy match must not swap these: "santa ana oriental" is not "santa ana occidental".
DIRECTIONS = {"norte", "sur", "oriental", "occidental", "alto", "alta", "bajo", "baja", "central"}
LOCALIDADES = ["usaquen", "chapinero", "suba", "engativa", "fontibon", "kennedy", "teusaquillo",
               "barrios unidos", "puente aranda", "santa fe", "candelaria", "bosa"]
NOT_A_PLACE = {"", "bogota", "bogota dc", "bogota d c"}
# Urban Bogota. Rural sectors (veredas) share names with city barrios.
SOUTH, NORTH, WEST, EAST = 4.45, 4.85, -74.25, -73.98


def norm(name) -> str:
    """Lowercase, no accents or punctuation: "Chicó Norte II" -> "chico norte ii"."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text)).strip()


def bare(key: str) -> str:
    """Without a leading article: "los rosales" -> "rosales"."""
    words = key.split()
    return " ".join(words[1:]) if len(words) > 1 and words[0] in ARTICLES else key


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


def city_sectors(sectors: dict) -> list:
    """[(code, name, geometry, (lat, lon))] for the cadastral sectors in the city."""
    out = []
    for feature in sectors["features"]:
        point = centroid(feature["geometry"])
        if in_city(*point):
            props = feature["properties"]
            out.append((props["SCACODIGO"], props["SCANOMBRE"], feature["geometry"], point))
    return out


def load_points(sectors: list, osm: dict) -> dict:
    """{normalized name: (lat, lon, name, sector code)}. Cadastral names first, then OSM names,
    whose sector is the one their point falls in (None if in no sector)."""
    points = {}
    boxes = [bounding_box(geometry) for _, _, geometry, _ in sectors]
    for code, name, _, (lat, lon) in sectors:
        points.setdefault(norm(name), (lat, lon, name, code))
    for element in osm["elements"]:
        lat = element.get("lat") or element.get("center", {}).get("lat")
        lon = element.get("lon") or element.get("center", {}).get("lon")
        if lat is None or not in_city(lat, lon):
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
    def __init__(self, points: dict):
        self.points = points
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
        for localidad in LOCALIDADES:  # "santa barbara occidental usaquen"
            head = key.removesuffix(" " + localidad)
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
    parser.add_argument("--sources-dir", required=True, help="Folder with SECTOR.geojson and osm_places.json")
    return parser.parse_args()


def main():
    import pandas as pd
    from pyspark.sql import SparkSession

    args = parse_args()
    spark = SparkSession.builder.getOrCreate()
    prefix = f"{args.catalog}.{args.schema}"
    with open(f"{args.sources_dir}/SECTOR.geojson") as sector_file:
        sectors = city_sectors(json.load(sector_file))
    with open(f"{args.sources_dir}/osm_places.json") as osm_file:
        locator = Locator(load_points(sectors, json.load(osm_file)))
    sector_names = {code: name for code, name, _, _ in sectors}

    counts = spark.sql(f"SELECT neighborhood, COUNT(*) AS n FROM {prefix}.gold_rent_listings GROUP BY 1").toPandas()
    rows = []
    for name, n in zip(counts["neighborhood"], counts["n"], strict=True):
        key, rule = locator.match(name)
        if key:
            lat, lon, official, code = locator.points[key]
            rows.append((name, official, round(lat, 6), round(lon, 6), rule, code, sector_names.get(code), int(n)))
    columns = ["neighborhood", "map_name", "latitude", "longitude", "match_rule", "sector_code", "sector_name", "n"]
    found = pd.DataFrame(rows, columns=columns)
    shapes = pd.DataFrame(
        [(code, name, json.dumps(geometry)) for code, name, geometry, _ in sectors],
        columns=["sector_code", "sector_name", "geojson"],
    )
    for table, frame in [("neighborhood_locations", found.drop(columns="n")), ("barrio_shapes", shapes)]:
        (
            spark.createDataFrame(frame)
            .write.mode("overwrite")
            .option("overwriteSchema", "true")
            .saveAsTable(f"{prefix}.{table}")
        )
    in_shape = found[found["sector_code"].notna()]["n"].sum()
    print(f"{len(found)} of {len(counts)} names located, {found['n'].sum() / counts['n'].sum():.0%} of listings; "
          f"{in_shape / counts['n'].sum():.0%} of listings in a barrio shape")


if __name__ == "__main__":
    main()
