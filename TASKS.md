# Tidewatch — Sprint Task List

_Generated from the Tidewatch Sprint Tracker on 2026-07-13. Region: Alaska & the Bering Sea (US territorial waters)._

Pipeline: **Commander → Detector → Vessel-intel** → `vessel_records.json` → **Analyst → Reporter**.

| # | Task | Owner | Priority | Status |
|---|------|-------|----------|--------|
| 1 | Detector: live Sentinel-1 + xView3 inference | Person A | High | ⬜ To do |
| 2 | Vessel-intel: live AISstream collection | Person A | High | 🟡 In progress |
| 3 | Zones: real EEZ/MPA polygons | Person A | Medium | ⬜ To do |
| 4 | Profiles: conflict_zone scenario | Person A | Medium | ⬜ To do |
| 5 | Detector: SAM masks (stretch) | Person A | Low | ⬜ To do |
| 6 | Analyst: rendezvous + threat scoring | Person B | High | ⬜ To do |
| 7 | Reporter: map + dossier | Person B | High | ⬜ To do |

---

## 1. Detector — ⬜ To do  ·  _Person A, High_

**Task:** Detector._run_xview3 — pull a real Sentinel-1 GRD scene (earth-search STAC, AWS Open Data), run the xView3 checkpoint, cache detections.

**In plain language:** Swap demo data for a real radar satellite image and run a proven ship-detection model to spot vessels.

**Progress:** Draft PR #3 — STAC selection (prefers IW/VV+VH; the Bering Sea returns a mix), tiling/NMS/geo + traced-ensemble inference wired & unit-tested. Remaining: run on a GPU box with `traced_ensemble.jit` (`TIDEWATCH_XVIEW3_CKPT`).

## 2. Vessel-intel — 🟡 In progress  ·  _Person A, High_

**Task:** VesselIntel._collect_aisstream — AISstream.io WebSocket subscription for the bbox; persist buffers to `data/`.

**In plain language:** Subscribe to live ship broadcasts (AIS) so we can compare who's announcing themselves against what radar sees. The gap is how we catch a dark vessel.

**Progress:** Draft PR #4 — live collector built & tested (STAC→AISstream bbox order handled; the real matcher consumes the output). Remaining: set `AISSTREAM_API_KEY` and run a live window.

## 3. Zones — ⬜ To do  ·  _Person A, Medium_

**Task:** Replace bbox zones with real polygons (Marine Regions EEZ + WDPA MPA shapefiles, shapely `contains`).

**In plain language:** Use real legal boundaries so the system can say a ship is inside a protected zone, not just a rough box.

## 4. Profiles — ⬜ To do  ·  _Person A, Medium_

**Task:** Build the `conflict_zone` cached scenario (mirror the fishing one).

**In plain language:** A second parallel scenario so the product handles more than one situation.

## 5. Detector — ⬜ To do  ·  _Person A, Low_

**Task:** (Stretch) SAM mask extraction per detection → `mask_ref`.

**In plain language:** Nice-to-have: outline each detected ship's shape; skippable if time runs short.

## 6. Analyst — ⬜ To do  ·  _Person B, High_

**Task:** Analyst — rendezvous detection over `vessel_records.json` (pair-class proximity + loiter), threat scoring, Bedrock narration.

**In plain language:** Spot suspicious behavior (rendezvous, loitering), score the threat, narrate it.

## 7. Reporter — ⬜ To do  ·  _Person B, High_

**Task:** Reporter — Leaflet map + dossier (feed `identity.citations` from You.com into dossier sources).

**In plain language:** Produce the human-facing map + dossier with cited sources.

---

### In-flight PRs
- **#3** `feat/detector-xview3-bering` — live xView3 detector (draft)
- **#4** `feat/vessel-intel-aisstream` — live AIS collection (draft)

_Source of truth is the Tidewatch Sprint Tracker in the team workspace; this file is a snapshot for GitHub readers._