"""
vessel_intel.py — Agent 3: vessel intelligence.

Enriches every registered contact on the blackboard:
  1. one-to-one AIS matching (nearest-first assignment, so a reefer's AIS
     can never "claim" the dark hull alongside it during a rendezvous)
  2. length-based classification (fishing < 60 m ≤ reefer < 160 m ≤ tanker)
  3. tracks + orphan-gap correlation (an AIS track that went silent near a
     dark contact is that contact's probable identity)
  4. jurisdiction stamping against the profile's zones
  5. You.com open-web enrichment for dark contacts (cached fallback when
     YDC_API_KEY is unset)
"""

from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime

from contract import AISGap, SwarmState, TrackPoint, VesselRecord
from profiles import WatchProfile, Zone

from .cached_scenarios import CACHED_AIS, CACHED_ENRICHMENT

MATCH_RADIUS_KM = 3.0    # AIS fix must be this close at scene time to match
ORPHAN_RADIUS_KM = 15.0  # dark contact ↔ silent track correlation radius
YDC_ENDPOINT = "https://api.ydc-index.io/search"


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _classify(length_m: float | None) -> str:
    if length_m is None:
        return "other"
    if length_m < 60:
        return "fishing"
    if length_m < 160:
        return "reefer"
    return "tanker"


def _zone_contains(zone: Zone, lat: float, lon: float) -> bool:
    min_lon, min_lat, max_lon, max_lat = zone.bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


class VesselIntel:
    def __init__(self, profile: WatchProfile, mode: str = "cached",
                 scenario: str = "iuu_fishing"):
        self.profile = profile
        self.mode = mode
        self.scenario = scenario
        # most specific zone first, so stamps and gap attribution read
        # "MPA" before "EEZ"
        self._zones = sorted(
            profile.zones,
            key=lambda z: (z.bbox[2] - z.bbox[0]) * (z.bbox[3] - z.bbox[1]),
        )

    # ------------------------------------------------------------------ run
    def run(self, state: SwarmState) -> None:
        tracks = self._collect_ais(state)
        if not tracks:
            state.log("vessel-intel: no AIS tracks — every contact stays dark")
        watch_end = max(_parse(tr["points"][-1][2]) for tr in tracks)

        assigned = self._match_one_to_one(state, tracks)
        self._classify_all(state)
        self._tracks_and_gaps(state, tracks, assigned, watch_end)
        self._stamp_zones(state)
        self._enrich(state)

    # ----------------------------------------------------------- collection
    def _collect_ais(self, state: SwarmState) -> list[dict]:
        if self.mode == "live":
            tracks = self._collect_aisstream(state)
        else:
            tracks = CACHED_AIS[self.scenario]
        state.log(f"vessel-intel: collected {len(tracks)} AIS tracks ({self.mode})")
        return tracks

    def _collect_aisstream(self, state: SwarmState) -> list[dict]:
        # prep-week TODO: AISstream.io WebSocket subscription for the profile
        # bbox; persist buffers to data/ so live runs become cached fallbacks.
        state.log(
            "vessel-intel: AISstream live mode not wired yet — "
            "falling back to cached buffers"
        )
        return CACHED_AIS[self.scenario]

    # -------------------------------------------------------------- matching
    def _match_one_to_one(self, state: SwarmState, tracks: list[dict]) -> dict:
        """Nearest-first one-to-one assignment of AIS tracks to detections."""
        candidates = []  # (dist_km, record, track)
        for rec in state.all():
            det_t = _parse(rec.detection.timestamp)
            for tr in tracks:
                lat, lon, _ = min(
                    tr["points"],
                    key=lambda p: abs((_parse(p[2]) - det_t).total_seconds()),
                )
                d = _haversine_km(rec.detection.lat, rec.detection.lon, lat, lon)
                if d <= MATCH_RADIUS_KM:
                    candidates.append((d, rec, tr))
        candidates.sort(key=lambda c: c[0])

        assigned: dict[str, dict] = {}  # vessel_id -> track
        taken_mmsi: set[str] = set()
        for d, rec, tr in candidates:
            if rec.vessel_id in assigned or tr["mmsi"] in taken_mmsi:
                continue
            rec.ais_matched = True
            rec.is_dark = False
            rec.mmsi = tr["mmsi"]
            assigned[rec.vessel_id] = tr
            taken_mmsi.add(tr["mmsi"])
            state.log(f"vessel-intel: {rec.vessel_id} ↔ MMSI {tr['mmsi']} ({d:.2f} km)")

        for rec in state.all():
            if rec.vessel_id in assigned:
                continue
            rec.ais_matched = False
            rec.is_dark = True
            near = [(d, tr) for d, r, tr in candidates if r is rec]
            if near:
                d, tr = min(near, key=lambda c: c[0])
                state.log(
                    f"vessel-intel: nearest AIS to {rec.vessel_id} is MMSI "
                    f"{tr['mmsi']} ({d:.2f} km) but it belongs to another hull — "
                    f"one-to-one matching keeps this contact DARK"
                )
            else:
                state.log(
                    f"vessel-intel: {rec.vessel_id} is DARK "
                    f"(no AIS within {MATCH_RADIUS_KM:.1f} km)"
                )
        return assigned

    # -------------------------------------------------------- classification
    def _classify_all(self, state: SwarmState) -> None:
        for rec in state.all():
            rec.vessel_class = _classify(rec.detection.length_m)
            state.log(
                f"vessel-intel: {rec.vessel_id} class={rec.vessel_class} "
                f"({rec.detection.length_m} m)"
            )

    # -------------------------------------------------------- tracks & gaps
    def _tracks_and_gaps(self, state: SwarmState, tracks: list[dict],
                         assigned: dict, watch_end: datetime) -> None:
        for rec in state.all():
            tr = assigned.get(rec.vessel_id)
            if tr:
                rec.track = [
                    TrackPoint(lat=lat, lon=lon, timestamp=ts, source="ais")
                    for lat, lon, ts in tr["points"]
                ]
                rec.ais_gaps.extend(self._internal_gaps(tr))
            else:
                det = rec.detection
                rec.track = [
                    TrackPoint(det.lat, det.lon, det.timestamp, det.source)
                ]

        # orphan tracks: broadcast, then silence long enough to matter
        taken = {tr["mmsi"] for tr in assigned.values()}
        orphans = []
        for tr in tracks:
            if tr["mmsi"] in taken:
                continue
            last_lat, last_lon, last_ts = tr["points"][-1]
            silent_h = (watch_end - _parse(last_ts)).total_seconds() / 3600
            if silent_h >= self.profile.min_gap_hours:
                orphans.append((tr, last_lat, last_lon, last_ts, silent_h))
                state.log(
                    f"vessel-intel: orphan track MMSI {tr['mmsi']} went silent "
                    f"{last_ts} at ({last_lat:.2f},{last_lon:.2f}) "
                    f"({silent_h:.1f} h of silence)"
                )

        for rec in state.dark_vessels():
            best = None
            for tr, lat, lon, ts, silent_h in orphans:
                d = _haversine_km(rec.detection.lat, rec.detection.lon, lat, lon)
                if d <= ORPHAN_RADIUS_KM and (best is None or d < best[0]):
                    best = (d, tr, lat, lon, ts, silent_h)
            if not best:
                continue
            d, tr, lat, lon, ts, silent_h = best
            rec.mmsi = tr["mmsi"]
            rec.ais_gaps.append(AISGap(
                start=ts,
                end=watch_end.isoformat(),
                duration_hours=round(silent_h, 1),
                last_known=(lat, lon),
                inside_zone=self._zone_at(lat, lon),
            ))
            state.log(
                f"vessel-intel: correlated dark contact {rec.vessel_id} ↔ MMSI "
                f"{tr['mmsi']} — went dark {d:.1f} km away"
                + (f" inside {rec.ais_gaps[-1].inside_zone}"
                   if rec.ais_gaps[-1].inside_zone else "")
            )

    def _internal_gaps(self, tr: dict) -> list[AISGap]:
        gaps = []
        pts = tr["points"]
        for (lat, lon, t0), (_, _, t1) in zip(pts, pts[1:]):
            hours = (_parse(t1) - _parse(t0)).total_seconds() / 3600
            if hours >= self.profile.min_gap_hours:
                gaps.append(AISGap(
                    start=t0,
                    end=t1,
                    duration_hours=round(hours, 1),
                    last_known=(lat, lon),
                    inside_zone=self._zone_at(lat, lon),
                ))
        return gaps

    # ----------------------------------------------------------------- zones
    def _zone_at(self, lat: float, lon: float) -> str | None:
        for zone in self._zones:
            if _zone_contains(zone, lat, lon):
                return zone.name
        return None

    def _stamp_zones(self, state: SwarmState) -> None:
        for rec in state.all():
            rec.zones = [
                z.name for z in self._zones
                if _zone_contains(z, rec.detection.lat, rec.detection.lon)
            ]
            state.log(
                f"vessel-intel: {rec.vessel_id} zones="
                f"{rec.zones or ['open water']}"
            )

    # ------------------------------------------------------------ enrichment
    def _enrich(self, state: SwarmState) -> None:
        for rec in state.dark_vessels():
            rec.identity = self._youcom_lookup(rec, state)

    def _youcom_lookup(self, rec: VesselRecord, state: SwarmState) -> dict:
        api_key = os.environ.get("YDC_API_KEY")
        if not api_key:
            state.log(
                f"vessel-intel: You.com enrichment for {rec.vessel_id} "
                f"(cached fallback — set YDC_API_KEY for live)"
            )
            return CACHED_ENRICHMENT[self.scenario]

        query = (
            f"MMSI {rec.mmsi} dark vessel AIS gap {self.profile.description}"
            if rec.mmsi else
            f"dark {rec.vessel_class} vessel {self.profile.description}"
        )
        try:
            req = urllib.request.Request(
                f"{YDC_ENDPOINT}?{urllib.parse.urlencode({'query': query})}",
                headers={"X-API-Key": api_key},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                hits = json.load(resp).get("hits", [])[:4]
            identity = {
                "summary_snippets": [
                    s for h in hits for s in h.get("snippets", [])[:1]
                ][:3],
                "citations": [
                    {"title": h.get("title"), "url": h.get("url")} for h in hits
                ],
                "note": "live You.com search results",
            }
            state.log(
                f"vessel-intel: You.com enrichment for {rec.vessel_id} "
                f"(live, {len(hits)} citations)"
            )
            return identity
        except Exception as exc:  # noqa: BLE001 — demo insurance: never stall the swarm
            state.log(
                f"vessel-intel: You.com live call failed ({exc}) — cached fallback"
            )
            return CACHED_ENRICHMENT[self.scenario]
