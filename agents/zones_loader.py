"""
zones_loader.py — real EEZ/MPA polygons for Tidewatch zone stamping (Task 3).

Replaces the rough rectangular zones with true legal boundaries so the
pipeline can honestly say a vessel is *inside* a protected zone, not just
inside a bounding box.

Data sources (both vessel-focused, public):
  * EEZ  — Marine Regions gazetteer (marineregions.org), no API key.
           US EEZ (Alaska) = MRGID 8463. Fetched live as WKT MULTIPOLYGON.
  * MPA  — NOAA Alaska Region's public ArcGIS habitat-restrictions service,
           no API key. The Pribilof Islands HCA and the Steller sea lion 3 nm
           no-transit zones are fetched as GeoJSON and cached to
           data/mpa_alaska.geojson (fetch_noaa_mpas). A hand-supplied GeoJSON
           at that path also works. (Protected Planet's API is deprecated in
           favor of bulk shapefile downloads, so it is no longer the MPA path.)

Design: keeps profiles.py's Zone contract intact but lets a Zone carry an
optional shapely geometry. Point-in-polygon is used when a real polygon is
present; otherwise the code falls back to the existing bbox test — so nothing
breaks if a polygon is missing.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Optional

from shapely.geometry import Point, shape
from shapely import wkt as shapely_wkt

DATA_DIR = os.environ.get("TIDEWATCH_DATA_DIR", "data")
MR_REST = "https://www.marineregions.org/rest"
# Prebuilt MRGIDs for the AOI (discovered via getGazetteerRecordsByName).
EEZ_MRGID = {
    "us_alaska": 8463,
    "us_mainland": 8456,
    "us_hawaii": 8453,
}


def _get(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tidewatch/1.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _cache_geojson(name: str, geom) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"zone_{name}.geojson")
    with open(path, "w") as f:
        json.dump({"type": "Feature", "geometry": geom.__geo_interface__,
                   "properties": {"name": name}}, f)
    return path


def load_eez_polygon(region_key: str = "us_alaska", use_cache: bool = True):
    """Return a shapely geometry for the region's EEZ.
    Fetches from Marine Regions (WKT) and caches to data/. GPU-free but needs
    network on first call; cached thereafter."""
    cache = os.path.join(DATA_DIR, f"zone_eez_{region_key}.geojson")
    if use_cache and os.path.exists(cache):
        with open(cache) as f:
            return shape(json.load(f)["geometry"])
    mrgid = EEZ_MRGID[region_key]
    raw = _get(f"{MR_REST}/getGazetteerGeometries.jsonld/{mrgid}/").decode("utf-8", "ignore")
    m = re.search(r'(MULTIPOLYGON\s*\(\(\(.*?\)\)\)|POLYGON\s*\(\(.*?\)\))', raw, re.S)
    if not m:
        raise RuntimeError(f"no WKT geometry in Marine Regions response for MRGID {mrgid}")
    geom = shapely_wkt.loads(m.group(1))
    _cache_geojson(f"eez_{region_key}", geom)
    return geom


def load_mpa_polygons(geojson_path: Optional[str] = None) -> dict:
    """Load MPA polygons from a local GeoJSON (FeatureCollection). Returns
    {name: shapely geometry}. If the file is missing, fetches it from NOAA's
    public service first (see fetch_noaa_mpas) — mirroring load_eez_polygon's
    fetch-once-then-cache behavior."""
    path = geojson_path or os.path.join(DATA_DIR, "mpa_alaska.geojson")
    if not os.path.exists(path):
        try:
            path = fetch_noaa_mpas(out_path=geojson_path)
        except Exception:
            return {}
    with open(path) as f:
        fc = json.load(f)
    out = {}
    for feat in fc.get("features", []):
        props = feat.get("properties", {})
        name = props.get("NAME") or props.get("name") or f"MPA-{len(out)+1}"
        out[name] = shape(feat["geometry"])
    return out


NOAA_AKRO_HABITAT = ("https://alaskafisheries.noaa.gov/arcgis/rest/services/"
                     "Steller_Sea_Lion_wOther_Management/MapServer/4/query")
# The Kodiak-area window the bering_alaska profile's SSL zone covers.
KODIAK_BBOX = (-156.0, 56.0, -150.5, 59.0)


def fetch_noaa_mpas(out_path: Optional[str] = None) -> str:
    """Fetch the bering_alaska MPA polygons from NOAA Alaska Region's public
    ArcGIS habitat-restrictions service (no API key) and write a GeoJSON
    FeatureCollection whose feature names match the profile's zone names.
    (Protected Planet's WDPA API is deprecated in favor of bulk shapefile
    downloads; NOAA is the authoritative keyless source for these zones.)"""
    from shapely.geometry import box
    from shapely.ops import unary_union

    def _layer_union(htm: str):
        qs = urllib.parse.urlencode({
            "where": f"HTM='{htm}'", "outFields": "OBJECTID",
            "returnGeometry": "true", "outSR": "4326", "f": "geojson",
        })
        fc = json.loads(_get(f"{NOAA_AKRO_HABITAT}?{qs}").decode())
        return unary_union([shape(f["geometry"]) for f in fc["features"]])

    prib = _layer_union("Prib_Hab_Cons_Area.htm")
    # SSL protection = 3 nm no-transit zones around rookeries, statewide;
    # clip to the Kodiak window the profile's zone describes.
    ssl_kodiak = _layer_union("3nm_No_Transit.htm").intersection(box(*KODIAK_BBOX))
    feats = [
        {"type": "Feature", "geometry": prib.__geo_interface__,
         "properties": {"name": "Pribilof Islands Habitat Conservation Area"}},
        {"type": "Feature", "geometry": ssl_kodiak.__geo_interface__,
         "properties": {"name": "Steller sea lion protection area (Kodiak)"}},
    ]
    out_path = out_path or os.path.join(DATA_DIR, "mpa_alaska.geojson")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    return out_path


def point_in(geom, lat: float, lon: float) -> bool:
    """True if (lat,lon) falls inside the shapely geometry (lon,lat order)."""
    return geom.contains(Point(lon, lat))
