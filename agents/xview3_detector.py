"""
xview3_detector.py — live SAR detection backend for Tidewatch's Detector.

Fills the prep-week TODO in agents/detector.py:
    earth-search STAC -> Sentinel-1 GRD for tile.bbox
    -> xView3 checkpoint inference -> persist detections to data/.

Design notes for the sprint
---------------------------
* Data source: earth-search STAC (AWS Open Data), collection "sentinel-1-grd".
  Verified live over the Bering Sea on 2026-07-13.
* Region reality (Alaska / Bering Sea, US waters):
    - xView3 was trained on Sentinel-1 IW mode, VV+VH polarization.
    - The Bering Sea also returns a lot of EW mode, HH+HV polarization
      (standard over open ocean / high latitudes). xView3 checkpoints do
      NOT expect HH/HV, so we PREFER IW/VV+VH scenes and skip EW by default
      (configurable) rather than silently feed the model the wrong bands.
* The heavy model call (`_infer_xview3`) is isolated behind one function so
  the DIUx-xView/xView3_first_place checkpoint can be dropped in without
  touching the STAC/caching plumbing. Until the checkpoint + GPU are wired,
  set TIDEWATCH_XVIEW3_CKPT to enable real inference; otherwise this raises
  a clear error so we never fake detections in live mode.
* Everything a live run produces is cached to data/ so today's live pull is
  tomorrow's fallback (matches the repo's cached-vs-live philosophy).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Any, Optional

import pystac_client

# The Detection dataclass lives in the repo's contract.py.
try:
    from contract import Detection  # when dropped into the repo
except Exception:  # pragma: no cover - standalone/testing
    from dataclasses import dataclass

    @dataclass
    class Detection:
        tile_id: str
        lat: float
        lon: float
        timestamp: str
        source: str
        confidence: float
        length_m: Optional[float] = None
        mask_ref: Optional[str] = None


STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-1-grd"
DATA_DIR = os.environ.get("TIDEWATCH_DATA_DIR", "data")
# xView3 expects IW / VV+VH. Set to "1" to also attempt EW/HH scenes.
ALLOW_EW = os.environ.get("TIDEWATCH_ALLOW_EW", "0") == "1"
# Days back from `before` to look for a scene covering the tile.
LOOKBACK_DAYS = int(os.environ.get("TIDEWATCH_LOOKBACK_DAYS", "14"))


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] xview3: {msg}")


def _cache_path(tile_id: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, f"detections_{tile_id}.json")


def _load_cache(tile_id: str) -> Optional[list[Detection]]:
    path = _cache_path(tile_id)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        raw = json.load(f)
    _log(f"tile {tile_id}: loaded {len(raw)} cached detections from {path}")
    return [Detection(**d) for d in raw]


def _save_cache(tile_id: str, dets: list[Detection]) -> None:
    path = _cache_path(tile_id)
    with open(path, "w") as f:
        json.dump([asdict(d) for d in dets], f, indent=2, default=str)
    _log(f"tile {tile_id}: cached {len(dets)} detections -> {path}")


def _pick_scene(bbox: tuple[float, float, float, float]) -> Optional[Any]:
    """Newest Sentinel-1 GRD scene covering bbox, preferring IW/VV+VH."""
    before = time.strftime("%Y-%m-%d", time.gmtime())
    after = time.strftime(
        "%Y-%m-%d", time.gmtime(time.time() - LOOKBACK_DAYS * 86400)
    )
    client = pystac_client.Client.open(STAC_URL)
    search = client.search(
        collections=[COLLECTION],
        bbox=list(bbox),
        datetime=f"{after}/{before}",
        max_items=50,
    )
    items = list(search.items())
    if not items:
        _log(f"no Sentinel-1 GRD scenes for bbox {bbox} in {after}..{before}")
        return None

    def mode(it):
        return (it.properties.get("sar:instrument_mode") or "").upper()

    def pols(it):
        return [p.upper() for p in (it.properties.get("sar:polarizations") or [])]

    # rank: IW+VV/VH first (what xView3 expects), newest first
    def score(it):
        iw = mode(it) == "IW"
        vv = "VV" in pols(it)
        return (iw and vv, it.properties.get("datetime", ""))

    items.sort(key=score, reverse=True)
    best = items[0]
    if mode(best) != "IW" or "VV" not in pols(best):
        if not ALLOW_EW:
            _log(
                f"tile scenes are {mode(best)}/{pols(best)} (not IW/VV+VH); "
                f"xView3 expects IW/VV+VH. Skipping (set TIDEWATCH_ALLOW_EW=1 "
                f"to override)."
            )
            return None
        _log(f"WARNING: using {mode(best)}/{pols(best)} scene; xView3 trained on IW/VV+VH")
    _log(
        f"selected scene {best.id} "
        f"({mode(best)}/{pols(best)} @ {best.properties.get('datetime')})"
    )
    return best


def _infer_xview3(scene: Any, bbox: tuple[float, float, float, float]) -> list[dict]:
    """
    Run the xView3 first-place traced ensemble over the scene's VV/VH assets
    and return raw contact dicts: {lat, lon, confidence, length_m}.

    Delegates to xview3_infer.infer_scene, which implements the winning
    solution's documented pipeline (2-ch SAR sigmoid norm -> 2048px tiles
    step 1536 -> traced ensemble + flip-LR TTA -> CenterNet NMS -> pixel->geo).
    Requires torch + rasterio + the traced_ensemble.jit checkpoint
    (TIDEWATCH_XVIEW3_CKPT). No GPU present -> runs on CPU (slow) but works.
    """
    vv = scene.assets["vv"].href
    vh = scene.assets["vh"].href
    from .xview3_infer import infer_scene  # heavy deps imported lazily inside
    raw = infer_scene(vv, vh, bbox=bbox)
    # keep only vessels for the Detector; classification carries downstream
    return [
        {
            "lat": d["lat"],
            "lon": d["lon"],
            "confidence": d["confidence"],
            "length_m": d["length_m"],
        }
        for d in raw
        if d.get("is_vessel", True)
    ]


def run_xview3(tile) -> list["Detection"]:
    """
    Drop-in body for Detector._run_xview3(tile).
    Returns Detection objects (already confidence-filtered downstream by the
    Detector's MIN_CONFIDENCE floor).
    """
    tile_id = getattr(tile, "tile_id", "T??")
    bbox = tuple(getattr(tile, "bbox"))

    cached = _load_cache(tile_id)
    if cached is not None:
        return cached

    scene = _pick_scene(bbox)
    if scene is None:
        _save_cache(tile_id, [])
        return []

    ts = scene.properties.get("datetime", "")
    raw = _infer_xview3(scene, bbox)
    dets = [
        Detection(
            tile_id=tile_id,
            lat=c["lat"],
            lon=c["lon"],
            timestamp=ts,
            source="sar",
            confidence=c["confidence"],
            length_m=c.get("length_m"),
            mask_ref=c.get("mask_ref"),
        )
        for c in raw
    ]
    _save_cache(tile_id, dets)
    return dets
