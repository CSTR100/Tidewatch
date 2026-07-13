# Tidewatch — Agents 1–3 (Person A's half)

Commander → Detector → Vessel-intel, ending in `vessel_records.json` — the
handoff that Person B's Analyst + Reporter consume.

## Run it

```bash
python run_pipeline.py                          # IUU fishing demo (cached)
python run_pipeline.py --profile conflict_zone  # Hormuz mission
python run_pipeline.py --profile bering_alaska  # Bering Sea / Gulf of Alaska mission
YDC_API_KEY=... python run_pipeline.py          # live You.com enrichment
TAVILY_API_KEY=... python run_pipeline.py       # live Tavily enrichment (alternate)
```

Open-web enrichment tries You.com first, then Tavily, then the cached
fallback — set whichever key you have; never commit keys. `.mcp.json` also
registers the Tavily MCP server for Claude Code sessions in this repo
(reads `TAVILY_API_KEY` from the environment).

## What the demo run shows

1. **Commander** tiles the Peru region into 9 tiles and dispatches 6 parallel
   scouts (the visible swarm moment).
2. **Detector** finds 5 SAR contacts, drops 1 as clutter, registers 4.
3. **Vessel-intel**:
   - one-to-one AIS matching → 1 contact is **dark** (a 38 m fishing vessel)
   - length-based classification → the ship loitering 400 m away is a **reefer**
   - **orphan-gap correlation** → the dark vessel's probable identity is
     MMSI 760123456, which went silent 7 h earlier *inside the MPA*
   - jurisdiction stamping → dark vessel is inside **MPA + EEZ**
   - **You.com enrichment** → citation-backed open-web intel on the fleet
     (sponsor prize: Best Use of You.com; cached fallback when no API key)

Person B's Analyst takes it from there: rendezvous scoring (dark fishing
vessel + broadcasting reefer, co-located, inside MPA) and the narrative.

## Design choices to know

- **`contract.py` is the seam.** Both halves build against `VesselRecord` /
  `SwarmState`. Person B: run the pipeline once, then develop entirely
  against `vessel_records.json`.
- **Profiles, not hard-coding** (`profiles.py`). Main goal is `iuu_fishing`;
  `conflict_zone` (Strait of Hormuz AIS-hiding / dark ship-to-ship transfers)
  proves the same swarm generalizes to other dark activity. Adding a mission
  = adding a profile.
- **Dataset differentiation: xView3-SAR.** The Detector is built to run an
  open-sourced xView3 winning model (the public dark-vessel SAR dataset,
  ~1,000 labeled Sentinel-1 scenes) rather than raw thresholding — building
  on existing work instead of training anything. Its length head also powers
  classification (fishing < 60 m < reefer < 160 m < tanker).
- **Cached vs live modes everywhere.** `--mode cached` is demo insurance;
  every live integration (`xview3`, AISstream) persists its output so today's
  live run is tomorrow's fallback.
- **One-to-one AIS matching** (nearest-first assignment). A naive matcher
  lets a reefer's AIS "claim" the dark hull alongside it during a rendezvous —
  hiding exactly the contact this product exists to find.

## Prep-week TODOs (before the 6-hour sprint)

Person A:
- [ ] `Detector._run_xview3`: pull a Sentinel-1 GRD scene for the demo bbox
      from AWS Open Data (earth-search STAC), run an xView3 checkpoint,
      cache the detections.
- [ ] `VesselIntel._collect_aisstream`: AISstream.io WebSocket subscription
      for the bbox; persist buffers to `data/`.
- [ ] Replace bbox zones with real polygons (Marine Regions EEZ + WDPA MPA
      shapefiles, shapely `contains`).
- [ ] Build the `conflict_zone` cached scenario (mirror the fishing one).
- [ ] (Stretch) SAM mask extraction per detection → `mask_ref`.

Person B:
- [x] Analyst: rendezvous detection over `vessel_records.json` (pair-class
      proximity + loiter), threat scoring, Bedrock narration
      (`TIDEWATCH_BEDROCK_MODEL` for live; template fallback otherwise).
- [x] Reporter: Leaflet map + dossier (feed `identity.citations` from
      You.com into the dossier's sources section).

Run Person B's half after the pipeline:

```bash
python run_analysis.py --profile bering_alaska   # → report.html + analyzed records
```

## Legal/framing note

All data sources here are public and vessel-focused (AIS is a public safety
broadcast; Sentinel is open imagery). Keep the product language on
*monitoring and enforcement intelligence* for vessels in public waters.
