"""Download SAGIS stormwater infrastructure intersecting Woodville, Savannah.

Install: py -m pip install requests shapely
Run: py get_woodville_stormwater_infrastructure.py

Downloads spatial features and their published attributes to GeoJSON.
Unlike a simple clip, full geometries are retained for pipes, ditches, and
basins that cross Woodville's boundary, to avoid cutting off network segments.

This is GIS data, not a calibrated EPA SWMM simulation or an XPSWMM project.
"""
import json
import time
from pathlib import Path

import requests
from shapely.geometry import shape
from shapely.ops import unary_union

OUT = Path(__file__).resolve().parent
ROOT = "https://pub.sagis.org/arcgis/rest/services/"
NEIGHBORHOOD_ENDPOINTS = [
    ROOT + "OpenData/Community/MapServer/10/query",
    ROOT + "Savannah/City_Assets/MapServer/5/query",
    ROOT + "Savannah/Traditional_Zones/MapServer/3/query",
]
HYDROLOGY_ROOT = ROOT + "OpenData/Hydrology/MapServer/"
# The official public GIS layer identifiers, checked against SAGIS metadata.
LAYERS = {
    29: ("stormwater_pipes", "Conduit pipes"),
    18: ("stormwater_inlets", "Inlets"),
    16: ("stormwater_manholes", "Manholes"),
    17: ("stormwater_ditches", "Maintained ditches and canals"),
    19: ("stormwater_headwalls", "Headwalls"),
    14: ("stormwater_tide_gates", "Tide gates"),
    15: ("stormwater_pump_stations", "Pump stations"),
    30: ("stormwater_drainage_basins", "Drainage basins"),
}

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "JHU-SpongeBlock-Woodville-GIS/3.0"


def query(url, params, attempts=3):
    err = None
    for i in range(attempts):
        try:
            res = SESSION.get(url, params=params, timeout=(12, 50))
            res.raise_for_status()
            data = res.json()
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            return data
        except (requests.RequestException, ValueError, RuntimeError) as e:
            err = e
            print(f"    Request attempt {i+1}/{attempts} failed: {e}", flush=True)
            if i + 1 < attempts:
                time.sleep(3 + i * 3)
    raise RuntimeError(f"SAGIS request failed at {url}: {err}")


def save(path, features):
    with (OUT / path).open("w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)
    print(f"  Saved {path}: {len(features)} features", flush=True)


def boundary():
    for url in NEIGHBORHOOD_ENDPOINTS:
        try:
            data = query(url, {
                "where": "NAME = 'Woodville'", "outFields": "*",
                "returnGeometry": "true", "outSR": 4326, "f": "geojson",
            }, attempts=2)
            features = data.get("features", [])
            if features:
                geom = unary_union([shape(f["geometry"]) for f in features])
                if not geom.is_valid:
                    geom = geom.buffer(0)
                if geom.is_empty:
                    raise RuntimeError("Empty neighborhood polygon")
                save("Woodville_boundary.geojson", features)
                return geom
            print("  No Woodville features from this neighborhood service; trying next")
        except RuntimeError as e:
            print(f"  Neighborhood endpoint unavailable: {e}")
    raise RuntimeError("Could not download Woodville boundary from any listed service")


def main():
    area = boundary()
    xmin, ymin, xmax, ymax = area.bounds  # Longitude and latitude (EPSG:4326)
    bbox = ",".join(map(str, [xmin, ymin, xmax, ymax]))
    print("Getting official Savannah stormwater asset layers intersecting Woodville...", flush=True)
    for layer_id, (filename, label) in LAYERS.items():
        url = HYDROLOGY_ROOT + str(layer_id) + "/query"
        print(f"\nLayer {layer_id}: {label}", flush=True)
        try:
            ids_data = query(url, {
                "where": "1=1", "geometry": bbox,
                "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "returnIdsOnly": "true", "f": "json",
            })
            ids = sorted(set(ids_data.get("objectIds") or []))
            print(f"  {len(ids)} candidates intersect Woodville's bounding box", flush=True)
            found = []
            # Small batches avoid slow/oversized responses from the public service.
            for start in range(0, len(ids), 50):
                these_ids = ids[start:start+50]
                data = query(url, {
                    "objectIds": ",".join(map(str, these_ids)),
                    "outFields": "*", "returnGeometry": "true",
                    "outSR": 4326, "f": "geojson",
                })
                for feature in data.get("features", []):
                    geometry = feature.get("geometry")
                    if geometry is None:
                        continue
                    geom = shape(geometry)
                    if not geom.is_valid:
                        geom = geom.buffer(0)
                    if not geom.is_empty and geom.intersects(area):
                        # Keep original full geometries and attributes, not clipped.
                        found.append(feature)
                print(f"  Downloaded {min(start+len(these_ids), len(ids))}/{len(ids)} candidates", flush=True)
            save("Woodville_" + filename + ".geojson", found)
        except RuntimeError as e:
            print(f"  WARNING: Could not complete {label}: {e}", flush=True)
            print("  Continuing with next layer. Rerun to retry any missing files.", flush=True)
    print("\nFinished. Drag all Woodville_*.geojson files into QGIS.", flush=True)
    print("Important: These GIS features are NOT an EPA SWMM model. Verify topology, elevations and coverage.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"\nERROR: {e}", flush=True)
        raise SystemExit(1)
