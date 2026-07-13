"""
detector.py — Agent 2: SAR contact detector.

Scans one tile at a time (called in parallel by the Commander's scouts)
and returns Detections above a confidence floor; sub-threshold returns
are dropped as clutter. Cached mode replays the persisted scenario for
the active profile; xview3 mode is where the xView3 winning model runs
over a Sentinel-1 GRD scene (prep-week TODO) — until wired, it falls
back to the cached scenario so the swarm never stalls.
"""

from __future__ import annotations

import time

from contract import Detection

from .cached_scenarios import CACHED_CONTACTS, SCENE_TIMES


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


class Detector:
    MIN_CONFIDENCE = 0.5  # xView3 head output below this is clutter

    def __init__(self, mode: str = "cached", scenario: str = "iuu_fishing"):
        if scenario not in CACHED_CONTACTS:
            raise ValueError(f"no cached scenario for profile '{scenario}'")
        self.mode = mode
        self.scenario = scenario
        self.dropped = 0

    def scan_tile(self, tile) -> list[Detection]:
        contacts = (
            self._run_xview3(tile) if self.mode == "xview3" else self._cached(tile)
        )
        kept = []
        for det in contacts:
            if det.confidence < self.MIN_CONFIDENCE:
                self.dropped += 1
                _log(
                    f"detector[{tile.tile_id}]: dropped contact @ "
                    f"({det.lat:.2f},{det.lon:.2f}) as clutter "
                    f"(conf {det.confidence:.2f} < {self.MIN_CONFIDENCE})"
                )
            else:
                kept.append(det)
        return kept

    def _cached(self, tile) -> list[Detection]:
        return [
            Detection(
                tile_id=tile.tile_id,
                lat=c["lat"],
                lon=c["lon"],
                timestamp=SCENE_TIMES[self.scenario],
                source="sar",
                confidence=c["confidence"],
                length_m=c["length_m"],
            )
            for c in CACHED_CONTACTS[self.scenario]
            if tile.contains(c["lat"], c["lon"])
        ]

    def _run_xview3(self, tile) -> list[Detection]:
        # prep-week TODO: earth-search STAC → Sentinel-1 GRD for tile.bbox →
        # xView3 checkpoint inference → persist detections to data/.
        _log(
            f"detector[{tile.tile_id}]: xview3 live mode not wired yet — "
            f"falling back to cached scenario"
        )
        return self._cached(tile)
