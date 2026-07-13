"""
run_pipeline.py — Person A's half of Tidewatch, end to end.

    python run_pipeline.py                      # IUU fishing demo (cached)
    python run_pipeline.py --profile conflict_zone   # Hormuz mission (needs
                                                     # its cached scenario or
                                                     # live mode wired)

Output: a full swarm log + vessel_records.json — exactly what Person B's
Analyst consumes. Until integration day, Person B runs this once and builds
against the JSON.
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "agents"))

from profiles import PROFILES
from agents.commander import Commander
from agents.detector import Detector
from agents.vessel_intel import VesselIntel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="iuu_fishing", choices=PROFILES.keys())
    ap.add_argument("--mode", default="cached", choices=["cached", "live"],
                    help="cached = demo insurance; live = real Sentinel/AIS")
    ap.add_argument("--out", default="vessel_records.json")
    args = ap.parse_args()

    profile = PROFILES[args.profile]
    print(f"\n=== TIDEWATCH · mission: {profile.description} ===\n")

    # Agent 1 — Commander: tile + dispatch
    commander = Commander(profile, grid=3, max_workers=6)
    tiles = commander.tile_region()

    # Agent 2 — Detector: fan out across tiles (the swarm moment)
    det_mode = "cached" if args.mode == "cached" else "xview3"
    detector = Detector(mode=det_mode, scenario=profile.profile_id)
    commander.dispatch(tiles, detector.scan_tile)

    # Agent 3 — Vessel-intel: AIS, class, tracks/gaps, zones, You.com
    intel_mode = "cached" if args.mode == "cached" else "live"
    intel = VesselIntel(profile, mode=intel_mode, scenario=profile.profile_id)
    intel.run(commander.state)

    # Hand off to Person B
    commander.state.dump(args.out)
    print(f"\n=== handoff: {len(commander.state.all())} vessel_records "
          f"written to {args.out} (Analyst input) ===")

    # quick human-readable summary of the interesting ones
    for rec in commander.state.dark_vessels():
        print(f"\n--- DARK CONTACT {rec.vessel_id} ---")
        print(f"  class={rec.vessel_class}  len={rec.detection.length_m}m  "
              f"zones={rec.zones or ['open water']}")
        if rec.identity:
            for s in rec.identity.get("summary_snippets", [])[:2]:
                print(f"  intel: {s}")


if __name__ == "__main__":
    main()
