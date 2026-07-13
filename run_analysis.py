"""
run_analysis.py — Person B's half of Tidewatch: Analyst → Reporter.

    python run_analysis.py                            # analyze vessel_records.json
    python run_analysis.py --profile bering_alaska    # Bering mission records
    python run_analysis.py --in vessel_records_bering.json --out bering_report.html

Consumes the vessel_records.json handoff from Person A's pipeline, writes the
Analyst fields (threat_score, threat_narrative, rendezvous_with) back into the
records file, and renders the Leaflet map + dossier report.

Bedrock narration: set TIDEWATCH_BEDROCK_MODEL (and AWS credentials) for live
LLM narratives; otherwise a fact-template fallback is used.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "agents"))

from profiles import PROFILES
from agents.analyst import Analyst
from agents.reporter import Reporter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="vessel_records.json",
                    help="vessel_records handoff from run_pipeline.py")
    ap.add_argument("--profile", default="bering_alaska", choices=PROFILES.keys())
    ap.add_argument("--out", default="report.html")
    args = ap.parse_args()

    profile = PROFILES[args.profile]
    print(f"\n=== TIDEWATCH · analysis: {profile.description} ===\n")

    with open(args.inp) as f:
        records = json.load(f)

    # Agent 4 — Analyst: rendezvous, scoring, narration
    records = Analyst(profile).run(records)
    with open(args.inp, "w") as f:
        json.dump(records, f, indent=2)

    # Agent 5 — Reporter: Leaflet map + dossier
    out = Reporter(profile).run(records, args.out)

    print(f"\n=== report: {out} (open in a browser) · "
          f"analyzed records written back to {args.inp} ===")


if __name__ == "__main__":
    main()
