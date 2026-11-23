"""Pull Aburra valley places out of an OpenStreetMap extract, for the map task (locate.py).

Usage (from databricks/): uvx --from osmium python scripts/aburra_osm.py colombia.osm.pbf aburra_osm.geojson
Keeps boundary=administrative (admin_level 6 = municipality, 8, 9, 10) and place=neighbourhood/suburb/quarter
areas and points inside the valley's bounding box.
"""
import json
import sys

import osmium
from osmium.geom import GeoJSONFactory

S, N, W, E = 6.08, 6.37, -75.70, -75.47
PLACES = {"neighbourhood", "suburb", "quarter"}
KEEP_TAGS = ["name", "alt_name", "official_name", "old_name", "short_name", "admin_level", "boundary", "place"]


def wanted(tags) -> bool:
    return (tags.get("boundary") == "administrative" and tags.get("admin_level") in {"6", "8", "9", "10"}) or (
        tags.get("place") in PLACES
    )


def inside(lon, lat) -> bool:
    return S < lat < N and W < lon < E


factory = GeoJSONFactory()
features = []
processor = (
    osmium.FileProcessor(sys.argv[1])
    .with_locations()
    .with_areas(osmium.filter.KeyFilter("place", "boundary"))
    .with_filter(osmium.filter.KeyFilter("place", "boundary"))
)
for obj in processor:
    if not wanted(obj.tags) or "name" not in obj.tags:
        continue
    props = {k: obj.tags.get(k) for k in KEEP_TAGS if obj.tags.get(k)}
    if obj.is_area():
        try:
            geometry = json.loads(factory.create_multipolygon(obj))
        except Exception:
            continue
        ring = geometry["coordinates"][0][0]
        lons, lats = [p[0] for p in ring], [p[1] for p in ring]
        if not inside(sum(lons) / len(lons), sum(lats) / len(lats)):
            continue
        props["osm"] = f"{'way' if obj.from_way() else 'relation'}/{obj.orig_id()}"
        features.append({"type": "Feature", "properties": props, "geometry": geometry})
    elif obj.is_node() and obj.tags.get("place") in PLACES and inside(obj.location.lon, obj.location.lat):
        props["osm"] = f"node/{obj.id}"
        features.append({"type": "Feature", "properties": props,
                         "geometry": {"type": "Point", "coordinates": [obj.location.lon, obj.location.lat]}})

with open(sys.argv[2], "w") as out:
    json.dump({"type": "FeatureCollection", "features": features}, out)
print(len(features), "features")
