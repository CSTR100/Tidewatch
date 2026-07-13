"""
profiles.py — watch profiles: same swarm, different mission.

Main goal is IUU fishing, but the pipeline is profile-driven so the same
agents can hunt other dark activity (e.g. AIS-hiding in the Strait of
Hormuz to avoid attacks / sanctions tracking). A profile bundles:
  - the region of interest
  - the boundary layers that matter
  - which vessel behaviors count as suspicious
  - what the Analyst should look for downstream

Adding a mission = adding a profile. No agent code changes.
"""

from dataclasses import dataclass, field


@dataclass
class Zone:
    name: str
    kind: str                    # "MPA" | "EEZ" | "corridor" | "sanction_zone"
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    # bbox for hackathon speed; swap for real polygons (Marine Regions / WDPA
    # shapefiles via shapely) without touching agent code.


@dataclass
class WatchProfile:
    profile_id: str
    description: str
    region_bbox: tuple[float, float, float, float]
    zones: list[Zone]
    # behavior weights the Analyst uses; Vessel-intel just records facts
    suspicious_behaviors: list[str] = field(default_factory=list)
    # classes that matter for pairing (fishing+reefer vs tanker+tanker STS)
    pair_classes: tuple[str, str] = ("fishing", "reefer")
    min_gap_hours: float = 2.0   # AIS silence shorter than this is ignored


# ---------------------------------------------------------------------------
# Profile 1 (MAIN GOAL): IUU fishing / illegal transshipment
# Demo region: waters off Peru's EEZ edge — a real dark-fleet hotspot with
# terrestrial AIS coverage near the coast.
# ---------------------------------------------------------------------------
IUU_FISHING = WatchProfile(
    profile_id="iuu_fishing",
    description="Illegal fishing & transshipment watch (fishing vessel x reefer rendezvous near protected waters)",
    region_bbox=(-83.0, -15.0, -78.0, -9.0),
    zones=[
        Zone("Peru EEZ (edge)", "EEZ", (-84.7, -20.2, -70.4, -3.4)),
        Zone("Demo MPA (Nazca ridge)", "MPA", (-81.5, -13.5, -79.5, -11.5)),
    ],
    suspicious_behaviors=[
        "dark_while_detected",      # seen by SAR, silent on AIS
        "ais_gap_in_zone",          # went dark crossing a boundary
        "rendezvous_dark_pair",     # fishing+reefer loiter, one dark
        "loitering_in_mpa",
    ],
    pair_classes=("fishing", "reefer"),
    min_gap_hours=2.0,
)

# ---------------------------------------------------------------------------
# Profile 2 (GENERALIZATION): conflict-zone AIS hiding / sanctions evasion
# Strait of Hormuz: vessels disable or spoof AIS to avoid targeting; dark
# ship-to-ship transfers move sanctioned oil. Same pipeline, different zones,
# tanker-tanker pairing, and a lower gap threshold (short gaps are the norm
# there, so only long silences are notable).
# ---------------------------------------------------------------------------
CONFLICT_ZONE = WatchProfile(
    profile_id="conflict_zone",
    description="Dark shipping watch: AIS-hiding & ship-to-ship transfers in a conflict corridor (Strait of Hormuz)",
    region_bbox=(55.0, 24.0, 58.5, 27.5),
    zones=[
        Zone("Hormuz transit corridor", "corridor", (55.8, 25.5, 57.5, 27.0)),
        Zone("STS watch box (Khor Fakkan approaches)", "sanction_zone", (56.0, 24.8, 56.8, 25.6)),
    ],
    suspicious_behaviors=[
        "dark_while_detected",
        "ais_gap_in_zone",
        "rendezvous_dark_pair",     # here: tanker-tanker ship-to-ship transfer
    ],
    pair_classes=("tanker", "tanker"),
    min_gap_hours=6.0,
)

PROFILES = {p.profile_id: p for p in (IUU_FISHING, CONFLICT_ZONE)}
