"""
zones_loader.py — real EEZ/MPA polygons for Tidewatch zone stamping (Task 3).

Replaces the rough rectangular zones with true legal boundaries so the
pipeline can honestly say a vessel is *inside* a protected zone, not just
inside a bounding box.

Data sources (both vessel-focused, public):
  * EEZ  — Marine Regions gazetteer (marineregions.org), no API key.
           US EEZ (Alaska) = MRGID 8463. Fetched live as WKT MULTIPOLYGON.
  * MPA  — WDPA / Protected Planet. The polygon API requires a free token
           (PROTECTED_PLANET_TOKEN). Because that needs a key, MPAs are
           loaded from a local GeoJSON the team drops in (data/mpa_*.geojson),
           and a documented fetch helper is provided for when the token exists.

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
    {name: shapely geometry}. WDPA/Protected Planet needs a token, so the team
    supplies the file; see fetch_wdpa_mpas for the keyed fetch path."""
    path = geojson_path or os.path.join(DATA_DIR, "mpa_alaska.geojson")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        fc = json.load(f)
    out = {}
    for feat in fc.get("features", []):
        props = feat.get("properties", {})
        name = props.get("NAME") or props.get("name") or f"MPA-{len(out)+1}"
        out[name] = shape(feat["geometry"])
    return out


def fetch_wdpa_mpas(iso3: str = "USA", out_path: Optional[str] = None) -> str:
    """Fetch marine protected areas from Protected Planet (needs
    PROTECTED_PLANET_TOKEN) and write a GeoJSON FeatureCollection. Documented
    path for the GPU/keyed box; not called automatically."""
    token = os.environ.get("PROTECTED_PLANET_TOKEN")
    if not token:
        raise RuntimeError(
            "PROTECTED_PLANET_TOKEN not set. Request a free token at "
            "api.protectedplanet.net to fetch WDPA MPA polygons, or drop a "
            "GeoJSON at data/mpa_alaska.geojson instead."
        )
    out_path = out_path or os.path.join(DATA_DIR, "mpa_alaska.geojson")
    feats, page = [], 1
    while True:
        url = (f"https://api.protectedplanet.net/v3/protected_areas/search?"
               f"country={iso3}&marine=true&with_geometry=true&per_page=50&page={page}&token={token}")
        data = json.loads(_get(url).decode())
        pas = data.get("protected_areas", [])
        if not pas:
            break
        for pa in pas:
            if pa.get("geojson"):
                feats.append({"type": "Feature",
                              "geometry": pa["geojson"]["geometry"],
                              "properties": {"name": pa.get("name")}})
        page += 1
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    return out_path


def point_in(geom, lat: float, lon: float) -> bool:
    """True if (lat,lon) falls inside the shapely geometry (lon,lat order)."""
    return geom.contains(Point(lon, lat))
