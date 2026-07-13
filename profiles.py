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
    # Real boundary (Task 3): optional shapely geometry. When present, zone
    # containment uses precise point-in-polygon; bbox stays as a coarse
    # fallback (and prefilter) so nothing breaks if a polygon is unavailable.
    polygon: object = None       # shapely geometry or None


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

# ---------------------------------------------------------------------------
# Profile 3: IUU fishing in the North Pacific — Bering Sea & Gulf of Alaska.
# Dark trawlers working conservation areas near the Pribilofs and Kodiak, with
# at-sea transshipment to reefers; distant-water fleet pressure along the US
# EEZ / Convention Line. Same fishing+reefer pairing as the Peru mission.
# ---------------------------------------------------------------------------
BERING_ALASKA = WatchProfile(
    profile_id="bering_alaska",
    description="IUU fishing & transshipment watch: Bering Sea & Gulf of Alaska (dark trawlers near conservation areas)",
    region_bbox=(-180.0, 50.0, -141.0, 62.0),
    zones=[
        Zone("US EEZ (Alaska)", "EEZ", (-180.0, 48.0, -130.0, 65.0)),
        Zone("Pribilof Islands Habitat Conservation Area", "MPA", (-172.0, 55.5, -168.0, 58.0)),
        Zone("Steller sea lion protection area (Kodiak)", "MPA", (-156.0, 56.0, -150.5, 59.0)),
    ],
    suspicious_behaviors=[
        "dark_while_detected",
        "ais_gap_in_zone",
        "rendezvous_dark_pair",
        "loitering_in_mpa",
    ],
    pair_classes=("fishing", "reefer"),
    min_gap_hours=3.0,
)

PROFILES = {p.profile_id: p for p in (IUU_FISHING, CONFLICT_ZONE, BERING_ALASKA)}


def hydrate_real_zones(profile: "WatchProfile", eez_region: str = "us_alaska",
                       mpa_geojson: str | None = None) -> "WatchProfile":
    """Attach real EEZ/MPA polygons to a profile's zones in place (Task 3).

    - EEZ zones get the live Marine Regions polygon for `eez_region`.
    - MPA zones get matching polygons from a local GeoJSON (data/mpa_*.geojson)
      when available; otherwise they keep their bbox fallback.
    Safe to call unconditionally: any load failure leaves the bbox in place, so
    zone stamping still works offline.
    """
    try:
        from agents.zones_loader import load_eez_polygon, load_mpa_polygons
    except Exception:
        from zones_loader import load_eez_polygon, load_mpa_polygons  # flat import
    try:
        eez_poly = load_eez_polygon(eez_region)
    except Exception:
        eez_poly = None
    mpas = {}
    try:
        mpas = load_mpa_polygons(mpa_geojson)
    except Exception:
        mpas = {}
    for z in profile.zones:
        if z.kind == "EEZ" and eez_poly is not None:
            z.polygon = eez_poly
        elif z.kind == "MPA" and z.name in mpas:
            z.polygon = mpas[z.name]
    return profile
