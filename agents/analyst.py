"""
analyst.py — Agent 4: threat analyst (Person B's half, task 6).

Consumes the vessel_records.json handoff — plain dicts, the contract seam —
and writes the Analyst fields back onto each record:
  1. rendezvous detection: profile pair-class proximity at scene time with
     at least one hull dark (the transshipment signature), plus a loiter
     check over the AIS track
  2. threat scoring against the profile's suspicious_behaviors
  3. narration — AWS Bedrock when TIDEWATCH_BEDROCK_MODEL is set (and boto3 +
     credentials exist), fact-template fallback otherwise, so the demo never
     stalls (same insurance philosophy as every other live integration here).
"""

from __future__ import annotations

import math
import os
import time

from profiles import WatchProfile

RENDEZVOUS_RADIUS_KM = 5.0   # SAR geolocation slop; alongside ships are <<1 km
LOITER_MAX_DRIFT_KM = 8.0    # track that stays inside this over the window

# behavior weights; only behaviors the profile lists as suspicious count
WEIGHTS = {
    "dark_while_detected": 0.35,
    "rendezvous_dark_pair": 0.30,
    "ais_gap_in_zone": 0.20,
    "loitering_in_mpa": 0.15,
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class Analyst:
    def __init__(self, profile: WatchProfile):
        self.profile = profile
        self._mpa_names = {z.name for z in profile.zones if z.kind == "MPA"}

    # ------------------------------------------------------------------ run
    def run(self, records: list[dict]) -> list[dict]:
        pairs = self._find_rendezvous(records)
        for rec in records:
            behaviors = self._behaviors(rec)
            rec["threat_score"] = self._score(behaviors)
            rec["threat_narrative"] = self._narrate(rec, behaviors)
            _log(
                f"analyst: {rec['vessel_id']} score={rec['threat_score']:.2f} "
                f"behaviors={behaviors or ['none']}"
            )
        _log(f"analyst: {len(pairs)} rendezvous pair(s), "
             f"{sum(1 for r in records if r['threat_score'] >= 0.5)} high-threat vessel(s)")
        return records

    # ------------------------------------------------------------ rendezvous
    def _loitering(self, rec: dict) -> bool:
        pts = rec.get("track") or []
        if len(pts) < 2:
            # single SAR fix: can't prove movement; a dark hull holding
            # position next to a loitering partner reads as loitering
            return True
        first = pts[0]
        drift = max(
            _haversine_km(first["lat"], first["lon"], p["lat"], p["lon"])
            for p in pts[1:]
        )
        return drift <= LOITER_MAX_DRIFT_KM

    def _find_rendezvous(self, records: list[dict]) -> list[tuple[str, str]]:
        want = set(self.profile.pair_classes)
        pairs = []
        for i, a in enumerate(records):
            for b in records[i + 1:]:
                if {a.get("vessel_class"), b.get("vessel_class")} != want and \
                        not (len(want) == 1 and
                             {a.get("vessel_class"), b.get("vessel_class")} == want):
                    continue
                if not (a.get("is_dark") or b.get("is_dark")):
                    continue  # two broadcasters alongside is ordinary seamanship
                d = _haversine_km(a["detection"]["lat"], a["detection"]["lon"],
                                  b["detection"]["lat"], b["detection"]["lon"])
                if d > RENDEZVOUS_RADIUS_KM:
                    continue
                if not (self._loitering(a) and self._loitering(b)):
                    continue
                a["rendezvous_with"] = b["vessel_id"]
                b["rendezvous_with"] = a["vessel_id"]
                pairs.append((a["vessel_id"], b["vessel_id"]))
                _log(
                    f"analyst: RENDEZVOUS {a['vessel_id']} "
                    f"({a['vessel_class']}{', dark' if a.get('is_dark') else ''}) ↔ "
                    f"{b['vessel_id']} ({b['vessel_class']}"
                    f"{', dark' if b.get('is_dark') else ''}) — {d:.1f} km apart, "
                    f"both loitering"
                )
        return pairs

    # --------------------------------------------------------------- scoring
    def _behaviors(self, rec: dict) -> list[str]:
        found = []
        if rec.get("is_dark"):
            found.append("dark_while_detected")
        if rec.get("rendezvous_with"):
            found.append("rendezvous_dark_pair")
        if any(g.get("inside_zone") for g in rec.get("ais_gaps") or []):
            found.append("ais_gap_in_zone")
        in_mpa = bool(self._mpa_names & set(rec.get("zones") or []))
        if in_mpa and self._loitering(rec):
            found.append("loitering_in_mpa")
        return [b for b in found if b in self.profile.suspicious_behaviors]

    def _score(self, behaviors: list[str]) -> float:
        return round(min(1.0, sum(WEIGHTS.get(b, 0.1) for b in behaviors)), 2)

    # -------------------------------------------------------------- narration
    def _facts(self, rec: dict, behaviors: list[str]) -> str:
        det = rec["detection"]
        lines = [
            f"{det.get('length_m')} m {rec.get('vessel_class')} vessel detected by "
            f"{det.get('source', 'sar').upper()} at ({det['lat']:.2f}, {det['lon']:.2f}) "
            f"on {det['timestamp']}",
            f"zones: {', '.join(rec.get('zones') or ['open water'])}",
        ]
        if rec.get("is_dark"):
            lines.append("no AIS broadcast (dark) — "
                         f"probable identity MMSI {rec.get('mmsi') or 'unknown'}")
        for g in rec.get("ais_gaps") or []:
            where = f" inside {g['inside_zone']}" if g.get("inside_zone") else ""
            lines.append(f"AIS silent {g['duration_hours']} h since {g['start']}{where}")
        if rec.get("rendezvous_with"):
            lines.append(f"loitering alongside {rec['rendezvous_with']} "
                         f"(pattern consistent with at-sea transshipment)")
        for s in (rec.get("identity") or {}).get("summary_snippets", [])[:2]:
            lines.append(f"open-web intel: {s}")
        return "; ".join(lines)

    def _narrate(self, rec: dict, behaviors: list[str]) -> str:
        facts = self._facts(rec, behaviors)
        live = self._bedrock_narrate(facts)
        if live:
            return live
        if not behaviors:
            return (f"Routine contact: {facts}. No suspicious behavior for the "
                    f"'{self.profile.profile_id}' watch.")
        return (f"SUSPICIOUS ({', '.join(behaviors)}): {facts}. "
                f"Recommend tasking follow-up collection and notifying enforcement.")

    def _bedrock_narrate(self, facts: str) -> str | None:
        model = os.environ.get("TIDEWATCH_BEDROCK_MODEL")
        if not model:
            return None
        try:
            import boto3
            client = boto3.client("bedrock-runtime")
            resp = client.converse(
                modelId=model,
                messages=[{"role": "user", "content": [{"text":
                    "Write a 2-sentence maritime enforcement threat narrative "
                    f"from these facts, no preamble: {facts}"}]}],
                inferenceConfig={"maxTokens": 200},
            )
            text = resp["output"]["message"]["content"][0]["text"].strip()
            _log("analyst: Bedrock narration (live)")
            return text
        except Exception as exc:  # noqa: BLE001 — demo insurance: never stall
            _log(f"analyst: Bedrock narration failed ({exc}) — template fallback")
            return None
