"""
analyst.py — Agent 4 (Person B): threat analyst.

Consumes the Person A handoff (vessel_records.json) and enriches each record
with the contract's Analyst fields:
    - rendezvous_with : partner vessel_id when two compatible-class hulls sit
                        within RENDEZVOUS_KM of each other (the transshipment
                        signature: a fishing vessel meeting a reefer)
    - threat_score    : 0..1 from real, explainable signals
    - threat_narrative: short plain-language "what & why"

Scoring is deterministic and auditable (no black box): each signal adds a
weighted amount, capped at 1.0, and every added signal is named in the
narrative so an analyst can see exactly why a vessel scored high.

Narration: a local template by default. A Bedrock hook (narrate_bedrock) is
provided for AWS deployments (set ANALYST_USE_BEDROCK=1 + AWS creds); it falls
back to the template on any error so the pipeline never stalls.
"""

from __future__ import annotations

import json
import math
import os
from typing import Optional

RENDEZVOUS_KM = float(os.environ.get("ANALYST_RENDEZVOUS_KM", "5.0"))
LOITER_RADIUS_KM = float(os.environ.get("ANALYST_LOITER_RADIUS_KM", "1.0"))
LOITER_MIN_HOURS = float(os.environ.get("ANALYST_LOITER_MIN_HOURS", "3.0"))

# class pairs whose close approach is a transshipment signal
TRANSSHIP_PAIRS = {("fishing", "reefer"), ("reefer", "fishing")}

# scoring weights (sum can exceed 1; final score is capped)
W = {
    "dark": 0.45,          # detected but not broadcasting AIS
    "in_mpa": 0.25,        # inside a Marine Protected Area
    "in_eez": 0.10,        # inside an EEZ (context, weaker alone)
    "rendezvous": 0.30,    # met a compatible-class vessel
    "loiter": 0.15,        # lingered in a small area
    "ais_gap_in_zone": 0.20,  # went dark inside a boundary
}


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _is_mpa(zones) -> bool:
    return any("MPA" in z or "Protected" in z for z in (zones or []))


def _is_eez(zones) -> bool:
    return any("EEZ" in z for z in (zones or []))


def detect_rendezvous(records: list[dict]) -> dict[str, str]:
    """Return {vessel_id: partner_vessel_id} for compatible-class hulls within
    RENDEZVOUS_KM. Nearest partner wins; symmetric."""
    pairs: dict[str, str] = {}
    best: dict[str, tuple[float, str]] = {}
    for i, a in enumerate(records):
        for b in records[i + 1:]:
            ca, cb = a.get("vessel_class"), b.get("vessel_class")
            if (ca, cb) not in TRANSSHIP_PAIRS:
                continue
            da, db = a["detection"], b["detection"]
            d = _haversine_km(da["lat"], da["lon"], db["lat"], db["lon"])
            if d <= RENDEZVOUS_KM:
                for x, y in ((a, b), (b, a)):
                    xid = x["vessel_id"]
                    if xid not in best or d < best[xid][0]:
                        best[xid] = (d, y["vessel_id"])
    return {vid: py for vid, (_, py) in best.items()}


def detect_loiter(rec: dict) -> bool:
    """True if the vessel's track stays within LOITER_RADIUS_KM for at least
    LOITER_MIN_HOURS."""
    track = rec.get("track") or []
    if len(track) < 2:
        return False
    lat0, lon0 = track[0]["lat"], track[0]["lon"]
    from datetime import datetime
    def _t(s): return datetime.fromisoformat(s.replace("Z", ""))
    within = [p for p in track
              if _haversine_km(lat0, lon0, p["lat"], p["lon"]) <= LOITER_RADIUS_KM]
    if len(within) < 2:
        return False
    span_h = abs((_t(within[-1]["timestamp"]) - _t(within[0]["timestamp"])).total_seconds()) / 3600
    return span_h >= LOITER_MIN_HOURS


def score_vessel(rec: dict, has_rendezvous: bool, loiter: bool) -> tuple[float, list[str]]:
    """Return (threat_score in [0,1], reasons[])."""
    score, reasons = 0.0, []
    if rec.get("is_dark"):
        score += W["dark"]; reasons.append("not broadcasting AIS (dark)")
    if _is_mpa(rec.get("zones")):
        score += W["in_mpa"]; reasons.append("inside a Marine Protected Area")
    elif _is_eez(rec.get("zones")):
        score += W["in_eez"]; reasons.append("inside an EEZ")
    if has_rendezvous:
        score += W["rendezvous"]; reasons.append("close rendezvous with a compatible-class vessel")
    if loiter:
        score += W["loiter"]; reasons.append("loitering in a small area")
    for g in rec.get("ais_gaps") or []:
        if g.get("inside_zone"):
            score += W["ais_gap_in_zone"]
            reasons.append(f"went dark inside {g['inside_zone']}")
            break
    return min(score, 1.0), reasons


def narrate_template(rec: dict, score: float, reasons: list[str], partner: Optional[str]) -> str:
    cls = rec.get("vessel_class") or "vessel"
    length = rec.get("detection", {}).get("length_m")
    who = f"A {int(length)} m {cls}" if length else f"A {cls}"
    band = "HIGH" if score >= 0.7 else "ELEVATED" if score >= 0.4 else "LOW"
    lead = f"{who} scored {score:.2f} ({band} concern)."
    if reasons:
        why = "Signals: " + "; ".join(reasons) + "."
    else:
        why = "No suspicious signals beyond detection."
    tail = f" Probable partner: {partner}." if partner else ""
    return lead + " " + why + tail


def narrate_bedrock(rec, score, reasons, partner):
    """Optional AWS Bedrock narration. Falls back to template on any error."""
    try:
        import boto3  # noqa: F401
        # Deployment hook: build a prompt from (rec, score, reasons, partner),
        # call bedrock-runtime invoke_model, return the text. Kept minimal here
        # so it can be filled in on the AWS box without changing callers.
        raise NotImplementedError("wire bedrock-runtime invoke_model on the AWS box")
    except Exception:
        return narrate_template(rec, score, reasons, partner)


def analyze(records: list[dict], use_bedrock: Optional[bool] = None) -> list[dict]:
    """Enrich records in place with rendezvous_with, threat_score,
    threat_narrative. Returns the same list."""
    if use_bedrock is None:
        use_bedrock = os.environ.get("ANALYST_USE_BEDROCK") == "1"
    rendezvous = detect_rendezvous(records)
    narrate = narrate_bedrock if use_bedrock else narrate_template
    for rec in records:
        vid = rec["vessel_id"]
        partner = rendezvous.get(vid)
        loiter = detect_loiter(rec)
        score, reasons = score_vessel(rec, partner is not None, loiter)
        rec["rendezvous_with"] = partner
        rec["threat_score"] = round(score, 3)
        rec["threat_narrative"] = narrate(rec, score, reasons, partner)
    return records


def run(in_path: str = "vessel_records.json", out_path: Optional[str] = None) -> list[dict]:
    """Read the Person A handoff, analyze, write enriched records back."""
    with open(in_path) as f:
        records = json.load(f)
    analyze(records)
    out_path = out_path or in_path
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2, default=str)
    return records


if __name__ == "__main__":
    import sys
    recs = run(sys.argv[1] if len(sys.argv) > 1 else "vessel_records.json")
    ranked = sorted(recs, key=lambda r: r.get("threat_score") or 0, reverse=True)
    for r in ranked:
        print(f"{r.get('threat_score'):.2f}  {r['vessel_id']}  {r.get('threat_narrative')}")
