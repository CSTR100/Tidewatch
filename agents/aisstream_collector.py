"""
aisstream_collector.py — live AIS collection backend for Tidewatch's
Vessel-intel agent (Task 2).

Fills the prep-week TODO in agents/vessel_intel.py:
    AISstream.io WebSocket subscription for the profile bbox
    -> buffer PositionReports per MMSI -> persist to data/
    -> return tracks in the pipeline's shape so one-to-one AIS matching,
       orphan-gap correlation and jurisdiction stamping work unchanged.

Reality of the data source (aisstream.io, verified 2026-07-13):
  * wss://stream.aisstream.io/v0/stream ; API key required (free, GitHub login).
  * REAL-TIME ONLY — no historical backfill. You open the socket, collect for
    a bounded window, and cache what arrived. Today's live window becomes
    tomorrow's cached fallback (matches the repo's cached-vs-live philosophy).
  * BoundingBoxes use [lat, lon] corner order — the OPPOSITE of the STAC
    (lon,lat,lon,lat) bbox used by the detector. We convert explicitly.
  * Subscription JSON must be sent within 3s of connecting.

Output track shape (exactly what vessel_intel._match_one_to_one consumes):
    {"mmsi": "<str>", "points": [(lat, lon, "YYYY-MM-DDTHH:MM:SS"), ...]}
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

DATA_DIR = os.environ.get("TIDEWATCH_DATA_DIR", "data")
AIS_URL = "wss://stream.aisstream.io/v0/stream"
# how long to collect live before caching + returning (seconds)
COLLECT_SECONDS = int(os.environ.get("TIDEWATCH_AIS_SECONDS", "120"))


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] aisstream: {msg}")


def stac_bbox_to_ais(bbox: tuple[float, float, float, float]):
    """(min_lon, min_lat, max_lon, max_lat)  ->  AISstream [[[lat,lon],[lat,lon]]].
    AISstream corner order is [lat, lon]; order of the two corners is irrelevant."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return [[[min_lat, min_lon], [max_lat, max_lon]]]


def _cache_path(profile_id: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, f"ais_{profile_id}.json")


def load_cached(profile_id: str) -> Optional[list[dict]]:
    p = _cache_path(profile_id)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        raw = json.load(f)
    # JSON turns tuples into lists; restore point tuples for the matcher
    for tr in raw:
        tr["points"] = [tuple(pt) for pt in tr["points"]]
    _log(f"loaded {len(raw)} cached AIS tracks from {p}")
    return raw


def _save(profile_id: str, tracks: list[dict]) -> None:
    p = _cache_path(profile_id)
    with open(p, "w") as f:
        json.dump(tracks, f, indent=2, default=str)
    _log(f"cached {len(tracks)} AIS tracks -> {p}")


def buffers_to_tracks(buffers: dict[str, list]) -> list[dict]:
    """Per-MMSI point lists -> pipeline track dicts, points sorted by time."""
    tracks = []
    for mmsi, pts in buffers.items():
        pts_sorted = sorted(pts, key=lambda p: p[2])
        tracks.append({"mmsi": str(mmsi), "points": pts_sorted})
    return tracks


def parse_position_message(msg: dict) -> Optional[tuple[str, tuple]]:
    """Extract (mmsi, (lat, lon, iso_ts)) from an aisstream PositionReport.
    Returns None for non-position or invalid messages. GPU/network-free —
    unit-testable against the documented message schema."""
    if msg.get("MessageType") != "PositionReport":
        return None
    meta = msg.get("MetaData", {}) or {}
    body = (msg.get("Message", {}) or {}).get("PositionReport", {}) or {}
    mmsi = meta.get("MMSI") or body.get("UserID")
    lat = meta.get("latitude", body.get("Latitude"))
    lon = meta.get("longitude", body.get("Longitude"))
    if mmsi is None or lat is None or lon is None:
        return None
    # aisstream time_utc: "2022-12-29 18:22:32.318353 +0000 UTC" -> ISO-ish
    raw_ts = meta.get("time_utc", "")
    iso = raw_ts.split(".")[0].replace(" ", "T") if raw_ts else \
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    # keep only date+time portion if a "T00:00:00" style remains
    iso = iso[:19]
    return str(mmsi), (float(lat), float(lon), iso)


async def _collect(api_key: str, bbox, seconds: int) -> dict[str, list]:
    import websockets  # lazy import; only needed for live mode
    buffers: dict[str, list] = {}
    sub = {
        "APIKey": api_key,
        "BoundingBoxes": stac_bbox_to_ais(bbox),
        "FilterMessageTypes": ["PositionReport"],
    }
    deadline = time.time() + seconds
    async with websockets.connect(AIS_URL, max_size=None) as ws:
        await ws.send(json.dumps(sub))  # must be within 3s
        _log(f"subscribed to bbox {bbox} for {seconds}s")
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if "error" in msg:
                raise RuntimeError(f"aisstream error: {msg['error']}")
            parsed = parse_position_message(msg)
            if parsed:
                mmsi, pt = parsed
                buffers.setdefault(mmsi, []).append(pt)
    _log(f"collected {sum(len(v) for v in buffers.values())} fixes across "
         f"{len(buffers)} vessels")
    return buffers


def collect_ais(profile_id: str, bbox, seconds: int = COLLECT_SECONDS) -> list[dict]:
    """
    Drop-in body for VesselIntel._collect_aisstream.
    Opens the live AISstream socket for `seconds`, buffers PositionReports for
    the bbox, caches to data/, and returns tracks in the pipeline's shape.
    Requires AISSTREAM_API_KEY. Raises if unset so live mode never silently
    fabricates tracks.
    """
    api_key = os.environ.get("AISSTREAM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "AISSTREAM_API_KEY not set. Get a free key at aisstream.io "
            "(GitHub login) to enable live AIS collection. "
            "(bbox conversion + buffering + caching are already working.)"
        )
    buffers = asyncio.run(_collect(api_key, bbox, seconds))
    tracks = buffers_to_tracks(buffers)
    _save(profile_id, tracks)
    return tracks
