"""
contract.py — the shared vessel_record schema and state store.

This is the seam between Person A (Commander/Detector/Vessel-intel) and
Person B (Analyst/Reporter). Both sides build against this contract.
Person B can import SwarmState and work entirely from mocks.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import time
import uuid


@dataclass
class Detection:
    """A single sensor hit, before it becomes a vessel."""
    tile_id: str
    lat: float
    lon: float
    timestamp: str                    # ISO 8601 UTC
    source: str                       # "sar" | "optical" | "viirs"
    confidence: float                 # 0..1 from the detector
    length_m: Optional[float] = None  # from SAM mask / xView3 head
    mask_ref: Optional[str] = None    # path/S3 key to extracted pixel mask


@dataclass
class TrackPoint:
    lat: float
    lon: float
    timestamp: str
    source: str                       # "ais" | "sar" | "viirs"


@dataclass
class AISGap:
    """A deliberate-looking transponder silence."""
    start: str
    end: str
    duration_hours: float
    last_known: tuple[float, float]   # (lat, lon) before going dark
    inside_zone: Optional[str] = None # zone name if gap occurred in a boundary


@dataclass
class VesselRecord:
    """The single object every agent enriches. Keyed by vessel_id."""
    vessel_id: str
    detection: Detection
    # --- Vessel-intel fields ---
    ais_matched: Optional[bool] = None      # None = not yet checked
    is_dark: Optional[bool] = None          # detected but not broadcasting
    mmsi: Optional[str] = None              # AIS identity if matched
    vessel_class: Optional[str] = None      # "fishing" | "reefer" | "tanker" | "other"
    track: list[TrackPoint] = field(default_factory=list)
    ais_gaps: list[AISGap] = field(default_factory=list)
    zones: list[str] = field(default_factory=list)   # EEZ/MPA/corridor names
    # --- You.com enrichment ---
    identity: Optional[dict] = None         # {name, flag, owner, history, citations}
    # --- Analyst fields (Person B writes these) ---
    threat_score: Optional[float] = None
    threat_narrative: Optional[str] = None
    rendezvous_with: Optional[str] = None   # vessel_id of the partner ship

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


class SwarmState:
    """
    The blackboard. In the hackathon build this can stay in-memory or move
    to DynamoDB/HydraDB with the same interface — agents only use put/get/all.
    """

    def __init__(self):
        self._records: dict[str, VesselRecord] = {}
        self._log: list[str] = []

    def new_vessel(self, det: Detection) -> VesselRecord:
        vid = f"V-{uuid.uuid4().hex[:8]}"
        rec = VesselRecord(vessel_id=vid, detection=det)
        self._records[vid] = rec
        self.log(f"state: registered {vid} from {det.source} @ ({det.lat:.3f},{det.lon:.3f})")
        return rec

    def get(self, vessel_id: str) -> VesselRecord:
        return self._records[vessel_id]

    def all(self) -> list[VesselRecord]:
        return list(self._records.values())

    def dark_vessels(self) -> list[VesselRecord]:
        return [r for r in self._records.values() if r.is_dark]

    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self._log.append(line)
        print(line)

    def dump(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump([asdict(r) for r in self._records.values()], f, indent=2, default=str)
