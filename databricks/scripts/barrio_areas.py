"""Approximate barrio areas for Envigado and Sabaneta, for the map task (locate.py).

Neither town publishes barrio outlines, and OpenStreetMap has almost none there. Both
publish points that carry a barrio name: Envigado its traffic accidents, Sabaneta its
streetlights (datos.gov.co). Each spot of the town gets the barrio of the nearest point
(Voronoi cells, merged per barrio), cut to the town outline from OpenStreetMap and to
about 450 m around the barrio's own points. So the areas are close to the real barrios
where there are many points; rural land with no points stays blank.

Usage (from databricks/), with the files from the README step:
  uvx --with shapely python scripts/barrio_areas.py aburra_osm.geojson envigado_accidents.json \\
      sabaneta_lights.json envigado_sabaneta_barrios.geojson
"""
import collections
import json
import statistics
import sys

import shapely
from shapely.geometry import MultiPoint, Point, mapping, shape
from shapely.ops import unary_union


def town_outline(osm_file: str, name: str):
    features = json.load(open(osm_file))["features"]
    return next(shape(f["geometry"]) for f in features
                if f["properties"].get("admin_level") == "6" and f["properties"].get("name") == name)


def clean(points: list) -> list:
    """One name per spot (the most common), without points far from the rest of their barrio:
    an address typed in the wrong barrio would put a piece of it somewhere else."""
    by_spot = collections.defaultdict(collections.Counter)
    for name, lat, lon in points:
        by_spot[(round(lat, 5), round(lon, 5))][name] += 1
    spots = [(counts.most_common(1)[0][0], lat, lon) for (lat, lon), counts in by_spot.items()]
    by_name = collections.defaultdict(list)
    for name, lat, lon in spots:
        by_name[name].append((lat, lon))
    kept = []
    for name, coords in by_name.items():
        mid_lat = statistics.median(c[0] for c in coords)
        mid_lon = statistics.median(c[1] for c in coords)
        dist = [((lat - mid_lat) ** 2 + (lon - mid_lon) ** 2) ** 0.5 for lat, lon in coords]
        limit = max(3 * statistics.median(dist), 0.003)  # about 300 m at least
        kept += [(name, lat, lon) for (lat, lon), d in zip(coords, dist, strict=True) if d <= limit]
    return kept


REACH = 0.004  # degrees, about 450 m


def areas(points: list, outline) -> dict:
    """{barrio: polygon}: Voronoi cells of its points, merged, cut to the town outline and to
    REACH around its points. Pieces smaller than 5% of a barrio's area are dropped."""
    # ordered=True: cell i belongs to point i.
    cells = shapely.voronoi_polygons(MultiPoint([(lon, lat) for _, lat, lon in points]),
                                     extend_to=outline.envelope, ordered=True)
    parts, near = collections.defaultdict(list), collections.defaultdict(list)
    for (name, lat, lon), cell in zip(points, cells.geoms, strict=True):
        parts[name].append(cell)
        near[name].append(Point(lon, lat).buffer(REACH))
    out = {}
    for name, polys in parts.items():
        merged = unary_union(polys).intersection(outline).intersection(unary_union(near[name]))
        pieces = list(getattr(merged, "geoms", [merged]))
        biggest = max(p.area for p in pieces)
        merged = unary_union([p for p in pieces if p.area >= 0.05 * biggest])
        out[name] = merged.simplify(0.00005)
    return out


def main():
    osm_file, accidents_file, lights_file, out_file = sys.argv[1:5]
    accidents = json.load(open(accidents_file))
    lights = json.load(open(lights_file))
    towns = {
        "envigado": [(r["barrio"].title(), r["coordenadas"]["coordinates"][1], r["coordenadas"]["coordinates"][0])
                     for r in accidents if r.get("barrio") and r.get("coordenadas") and r["barrio"] != "NO EXISTE"],
        "sabaneta": [(r["barrio"], float(r["latitud_n"]), float(r["longitud_w"]))
                     for r in lights if r.get("barrio") and r.get("latitud_n")],
    }
    features = []
    for city, points in towns.items():
        outline = town_outline(osm_file, city.title())
        points = [(n, lat, lon) for n, lat, lon in clean(points) if outline.contains(Point(lon, lat))]
        for i, (name, polygon) in enumerate(sorted(areas(points, outline).items()), start=1):
            features.append({"type": "Feature", "properties": {"city": city, "code": f"{city}-{i}", "name": name},
                             "geometry": mapping(polygon)})
        print(city, len(points), "points,", sum(f["properties"]["city"] == city for f in features), "barrios")
    with open(out_file, "w") as out:
        json.dump({"type": "FeatureCollection", "features": features}, out)


if __name__ == "__main__":
    main()
