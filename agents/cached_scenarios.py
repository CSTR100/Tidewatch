"""
cached_scenarios.py — demo insurance for `--mode cached`.

Every live integration (xView3, AISstream, You.com) persists its output so
today's live run is tomorrow's fallback; until those are wired, these are
the persisted scenarios. One entry per profile_id, so adding a mission =
adding a profile + its scenario, no agent code changes.

Each scenario bundles:
  - SCENE_TIMES:      the SAR pass timestamp the Detector stamps on contacts
  - CACHED_CONTACTS:  raw detector hits (incl. sub-threshold clutter)
  - CACHED_AIS:       AIS track buffers for the watch window (incl. the
                      orphan track that goes silent — the gap-correlation
                      story)
  - CACHED_ENRICHMENT: You.com fallback intel for dark contacts
"""


def _hourly(base_lat, base_lon, dlat, dlon, start_hour, n, day="2026-07-10"):
    """n hourly fixes drifting (dlat, dlon) per hour from start_hour."""
    return [
        (base_lat + dlat * i, base_lon + dlon * i, f"{day}T{start_hour + i:02d}:00:00")
        for i in range(n)
    ]


SCENE_TIMES = {
    "iuu_fishing": "2026-07-10T10:00:00",
    "conflict_zone": "2026-07-10T09:00:00",
    "bering_alaska": "2026-07-12T18:00:00",
}

# --------------------------------------------------------------------------
# Detector contacts (what the xView3 model would emit per Sentinel-1 scene).
# 5 hits for iuu_fishing: 4 real hulls + 1 low-confidence clutter return.
# --------------------------------------------------------------------------
CACHED_CONTACTS = {
    "iuu_fishing": [
        # broadcasting fishing vessel working the EEZ edge
        dict(lat=-10.2, lon=-81.9, confidence=0.91, length_m=41.0),
        # broadcasting tanker transiting south of the MPA
        dict(lat=-14.06, lon=-79.3, confidence=0.95, length_m=190.0),
        # the dark hull — no AIS, loitering inside the MPA
        dict(lat=-12.42, lon=-80.51, confidence=0.94, length_m=38.0),
        # the reefer loitering 400 m away (its AIS must not claim the dark hull)
        dict(lat=-12.44, lon=-80.49, confidence=0.97, length_m=128.0),
        # breaking-wave clutter the Detector should drop
        dict(lat=-11.3, lon=-82.4, confidence=0.31, length_m=9.0),
    ],
    "conflict_zone": [
        # dark tanker holding position in the STS watch box
        dict(lat=25.2, lon=56.3, confidence=0.93, length_m=238.0),
        # broadcasting tanker alongside — the ship-to-ship partner
        dict(lat=25.21, lon=56.31, confidence=0.96, length_m=245.0),
        # broadcasting tanker transiting the corridor normally
        dict(lat=26.4, lon=57.0, confidence=0.90, length_m=205.0),
        # sea-state clutter
        dict(lat=24.3, lon=55.4, confidence=0.22, length_m=7.0),
    ],
    "bering_alaska": [
        # the dark hull — no AIS, loitering inside the Pribilof HCA
        # (~25 km north of St. George Island: positions must sit inside the
        # real NOAA HCA polygon and in actual water — the real EEZ polygon
        # excludes the island's landmass)
        dict(lat=56.70, lon=-169.60, confidence=0.93, length_m=44.0),
        # the reefer 800 m away — the transshipment partner
        dict(lat=56.705, lon=-169.59, confidence=0.97, length_m=140.0),
        # broadcasting crabber on the Bering shelf
        dict(lat=57.8, lon=-166.0, confidence=0.90, length_m=38.0),
        # broadcasting trawler in the SSL no-transit zone off Marmot Island
        # (Kodiak) — the real zones are 3 nm circles around rookeries
        dict(lat=58.14, lon=-151.90, confidence=0.92, length_m=52.0),
        # tanker transiting the Gulf of Alaska
        dict(lat=59.3, lon=-145.5, confidence=0.95, length_m=183.0),
        # sea-state clutter (Aleutian swell)
        dict(lat=52.4, lon=-175.0, confidence=0.28, length_m=8.0),
        # clutter near Shelikof Strait
        dict(lat=58.9, lon=-155.9, confidence=0.41, length_m=11.0),
    ],
}

# --------------------------------------------------------------------------
# AIS buffers (what _collect_aisstream would persist to data/).
# The last track in each scenario is the orphan: it goes silent mid-window,
# inside a boundary zone, and its last known position sits near the dark
# SAR contact — that correlation is the vessel's probable identity.
# --------------------------------------------------------------------------
CACHED_AIS = {
    "iuu_fishing": [
        {"mmsi": "760555111", "points": _hourly(-10.2, -81.9, 0.0, 0.002, 6, 9)},
        {"mmsi": "636444222", "points": _hourly(-14.1, -79.3, 0.01, 0.0, 6, 9)},
        {"mmsi": "351987000", "points": _hourly(-12.44, -80.49, 0.001, 0.0, 6, 9)},
        # goes dark at 07:00 inside the MPA, last known 3 km from the dark hull
        {"mmsi": "760123456", "points": _hourly(-12.4, -80.56, 0.0, 0.01, 4, 4)},
    ],
    "conflict_zone": [
        {"mmsi": "422555888", "points": _hourly(25.21, 56.31, 0.0, 0.0005, 5, 8)},
        {"mmsi": "636999777", "points": _hourly(26.4, 56.6, 0.0, 0.1, 5, 8)},
        # goes dark at 03:00 inside the STS watch box
        {"mmsi": "572333111", "points": _hourly(25.25, 56.28, 0.0, -0.01, 0, 4)},
    ],
    "bering_alaska": [
        # reefer loitering by the Pribilofs (flag of convenience)
        {"mmsi": "511200345", "points": _hourly(56.705, -169.59, 0.0, 0.0005, 12, 11, day="2026-07-12")},
        # crabber working the shelf
        {"mmsi": "368112233", "points": _hourly(57.8, -166.03, 0.0, 0.005, 12, 11, day="2026-07-12")},
        # trawler off Marmot Island (Kodiak)
        {"mmsi": "367445566", "points": _hourly(58.17, -151.90, -0.005, 0.0, 12, 11, day="2026-07-12")},
        # tanker northeast-bound across the Gulf
        {"mmsi": "636987654", "points": _hourly(59.0, -146.1, 0.05, 0.1, 12, 11, day="2026-07-12")},
        # goes dark at 15:00 inside the Pribilof HCA, 1.3 km from the dark hull
        {"mmsi": "273812345", "points": _hourly(56.72, -169.72, -0.005, 0.02, 10, 6, day="2026-07-12")},
    ],
}

# --------------------------------------------------------------------------
# You.com fallback intel, keyed by scenario (used when YDC_API_KEY is unset
# or the live call fails).
# --------------------------------------------------------------------------
CACHED_ENRICHMENT = {
    "iuu_fishing": {
        "summary_snippets": [
            "Regional reporting documents a distant-water fishing fleet operating near the Peruvian EEZ with repeated transponder shutoffs.",
            "NGO trackers have linked reefers loitering along the EEZ edge to at-sea transshipment of unreported catch.",
            "Port-state inspections previously cited vessels from this fleet for unreported catch transfers.",
        ],
        "citations": [
            {
                "title": "Dark fleet activity off South America (tracker report)",
                "url": "https://example.org/dark-fleet-report",
            },
            {
                "title": "Transshipment patterns near EEZ boundaries",
                "url": "https://example.org/transshipment-study",
            },
        ],
        "note": "cached fallback -- replace with live You.com API results when YDC_API_KEY is set",
    },
    "conflict_zone": {
        "summary_snippets": [
            "Sanctions trackers report tankers disabling AIS in the Strait of Hormuz before conducting ship-to-ship transfers of sanctioned crude.",
            "Maritime security advisories note AIS-hiding in the corridor both to evade targeting and to obscure cargo provenance.",
            "Vessels linked to this shadow fleet have previously spoofed positions during loitering off Khor Fakkan.",
        ],
        "citations": [
            {
                "title": "Shadow fleet STS transfers in the Gulf of Oman (advisory)",
                "url": "https://example.org/shadow-fleet-advisory",
            },
            {
                "title": "AIS manipulation in conflict corridors",
                "url": "https://example.org/ais-manipulation-study",
            },
        ],
        "note": "cached fallback -- replace with live You.com API results when YDC_API_KEY is set",
    },
    "bering_alaska": {
        "summary_snippets": [
            "Enforcement reporting documents distant-water trawlers shutting off AIS near the US EEZ boundary in the Bering Sea before transferring catch at sea.",
            "Observers have flagged reefers loitering near the Pribilof Islands conservation area during the summer pollock season.",
            "Vessels associated with this fleet have prior port-state citations for unreported transshipment in the North Pacific.",
        ],
        "citations": [
            {
                "title": "Dark fleet pressure along the Bering Sea Convention Line (watch brief)",
                "url": "https://example.org/bering-convention-line-brief",
            },
            {
                "title": "At-sea transshipment in the North Pacific (fisheries intelligence review)",
                "url": "https://example.org/north-pacific-transshipment",
            },
        ],
        "note": "cached fallback -- replace with live You.com API results when YDC_API_KEY is set",
    },
}
