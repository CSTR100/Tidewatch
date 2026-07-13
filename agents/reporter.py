"""
reporter.py — Agent 5: reporter (Person B's half, task 7).

Turns analyzed vessel records into the human-facing product: a single-file
HTML report with a Leaflet map (tracks, rendezvous links, zone boxes, real
MPA polygons when data/mpa_alaska.geojson exists) and a dossier per vessel,
highest threat first, with `identity.citations` from the open-web enrichment
feeding the sources section.

Leaflet + OSM tiles load from the network when the report is opened; all
data is embedded, so the file itself is portable.
"""

from __future__ import annotations

import json
import os
import time

from profiles import WatchProfile

CLASS_COLORS = {"fishing": "#2a78d6", "reefer": "#eda100",
                "tanker": "#4a3aa7", "other": "#7c8b98"}
DARK_COLOR = "#d03b3b"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


class Reporter:
    def __init__(self, profile: WatchProfile, mpa_geojson: str = "data/mpa_alaska.geojson"):
        self.profile = profile
        self.mpa_geojson = mpa_geojson

    def run(self, records: list[dict], out_path: str = "report.html") -> str:
        mpa_fc = None
        if os.path.exists(self.mpa_geojson):
            with open(self.mpa_geojson) as f:
                mpa_fc = json.load(f)
            _log(f"reporter: embedding real MPA polygons from {self.mpa_geojson}")
        payload = {
            "mission": self.profile.description,
            "profile_id": self.profile.profile_id,
            "zones": [{"name": z.name, "kind": z.kind, "bbox": list(z.bbox)}
                      for z in self.profile.zones],
            "records": sorted(records, key=lambda r: -(r.get("threat_score") or 0)),
            "mpa_geojson": mpa_fc,
            "class_colors": CLASS_COLORS,
            "dark_color": DARK_COLOR,
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        html = _TEMPLATE.replace("__TIDEWATCH_DATA__", json.dumps(payload))
        with open(out_path, "w") as f:
            f.write(html)
        _log(f"reporter: wrote {out_path} "
             f"({len(records)} vessels, {sum(1 for r in records if r.get('is_dark'))} dark)")
        return out_path


_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tidewatch — Mission Report</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root { --ink:#16232e; --muted:#64778a; --line:#dae2e8; --card:#fbfcfd;
          --page:#edf1f4; --critical:#d03b3b; --accent:#2e7f93; }
  * { box-sizing:border-box; margin:0; }
  body { font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
         color:var(--ink); background:var(--page); }
  header { padding:18px 24px; background:var(--ink); color:#e6edf3; }
  header h1 { font-size:19px; } header p { color:#9dafbd; font-size:13px; }
  #map { height:52vh; min-height:360px; }
  .legend { display:flex; gap:14px; flex-wrap:wrap; padding:10px 24px;
            background:var(--card); border-bottom:1px solid var(--line);
            font-size:12.5px; color:var(--muted); }
  .dot { display:inline-block; width:10px; height:10px; border-radius:50%;
         margin-right:5px; vertical-align:baseline; }
  main { padding:20px 24px; max-width:1100px; margin:0 auto; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:16px 18px; margin-bottom:14px; }
  .card.dark-vessel { border-left:4px solid var(--critical); }
  .row { display:flex; justify-content:space-between; align-items:baseline;
         gap:12px; flex-wrap:wrap; }
  .vid { font-family:ui-monospace,Menlo,monospace; font-weight:600; }
  .badge { font-size:11px; font-weight:700; letter-spacing:.05em; padding:2px 8px;
           border-radius:999px; background:#f6e0e0; color:var(--critical); }
  .meta { color:var(--muted); font-size:13px; margin:4px 0 8px; }
  .bar { height:8px; background:#e4eaee; border-radius:4px; overflow:hidden;
         flex:1; min-width:140px; max-width:260px; }
  .bar i { display:block; height:100%; background:var(--critical); }
  .score { display:flex; align-items:center; gap:10px; font-size:13px;
           color:var(--muted); }
  .narr { margin:8px 0; }
  .srcs { font-size:13px; color:var(--muted); margin-top:8px;
          border-top:1px dashed var(--line); padding-top:8px; }
  .srcs a { color:var(--accent); }
  footer { padding:14px 24px 28px; color:var(--muted); font-size:12.5px;
           text-align:center; }
  .pdf-btn { float:right; margin-top:-2px; font:600 13px system-ui;
             color:#e6edf3; background:var(--accent); border:none;
             border-radius:7px; padding:7px 14px; cursor:pointer; }
  .pdf-btn:hover { filter:brightness(1.1); }

  /* --- print / PDF: dossier only, clean pages, full source URLs --- */
  @media print {
    #map, .legend, .pdf-btn, footer { display:none; }
    body { background:#fff; }
    header { background:#fff; color:#16232e; padding:0 0 12px;
             border-bottom:2px solid #16232e; }
    header p { color:#4c5f6e; }
    main { padding:16px 0; max-width:none; }
    .card { break-inside:avoid; border:1px solid #bbb; box-shadow:none; }
    .card.dark-vessel { border-left:4px solid #d03b3b; }
    .srcs a::after { content:" (" attr(href) ")"; font-size:11px;
                     word-break:break-all; }
  }
</style>
</head>
<body>
<header>
  <button class="pdf-btn" onclick="window.print()"
          title="Uses your browser's Save as PDF">Download dossier (PDF)</button>
  <h1>Tidewatch — Mission Report</h1><p id="mission"></p>
</header>
<div id="map"></div>
<div class="legend" id="legend"></div>
<main id="dossiers"></main>
<footer>Generated by Tidewatch Reporter (Agent 5). AIS is a public safety
broadcast; Sentinel-1 is open imagery. Monitoring &amp; enforcement
intelligence for vessels in public waters.</footer>
<script>
const D = __TIDEWATCH_DATA__;
document.getElementById("mission").textContent =
  D.mission + " · generated " + D.generated;

const recs = D.records;
const lats = recs.map(r => r.detection.lat), lons = recs.map(r => r.detection.lon);
const map = L.map("map");
map.fitBounds([[Math.min(...lats)-1, Math.min(...lons)-1],
               [Math.max(...lats)+1, Math.max(...lons)+1]]);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  { attribution: "&copy; OpenStreetMap" }).addTo(map);

// zones: real MPA polygons when present, bbox rectangles otherwise
const realNames = new Set();
if (D.mpa_geojson) {
  L.geoJSON(D.mpa_geojson, { style: { color:"#2e7f93", weight:1.5,
    fillOpacity:0.08 } }).bindTooltip(l => l.feature.properties.name).addTo(map);
  D.mpa_geojson.features.forEach(f => realNames.add(f.properties.name));
}
D.zones.forEach(z => {
  if (realNames.has(z.name)) return;
  const [w, s, e, n] = z.bbox;
  L.rectangle([[s, w], [n, e]], { color:"#2e7f93", weight:1, dashArray:"4 4",
    fillOpacity:0.04 }).bindTooltip(z.name + " (" + z.kind + ")").addTo(map);
});

// rendezvous links first (under the markers)
const byId = Object.fromEntries(recs.map(r => [r.vessel_id, r]));
const drawn = new Set();
recs.forEach(r => {
  const p = r.rendezvous_with && byId[r.rendezvous_with];
  if (!p || drawn.has(p.vessel_id)) return;
  drawn.add(r.vessel_id);
  L.polyline([[r.detection.lat, r.detection.lon],
              [p.detection.lat, p.detection.lon]],
    { color: D.dark_color, weight:2.5, dashArray:"6 6" })
    .bindTooltip("rendezvous pair").addTo(map);
});

recs.forEach(r => {
  const c = r.is_dark ? D.dark_color : (D.class_colors[r.vessel_class] || "#7c8b98");
  if (r.track && r.track.length > 1)
    L.polyline(r.track.map(p => [p.lat, p.lon]), { color:c, weight:2,
      opacity:0.7 }).addTo(map);
  L.circleMarker([r.detection.lat, r.detection.lon],
    { radius: r.is_dark ? 9 : 6, color:c, fillColor:c, fillOpacity:0.85 })
    .bindPopup("<b>" + r.vessel_id + "</b><br>" + r.vessel_class + " · " +
      (r.detection.length_m || "?") + " m" +
      (r.is_dark ? "<br><b style='color:" + D.dark_color + "'>DARK</b>" : "") +
      (r.mmsi ? "<br>MMSI " + r.mmsi : "") +
      "<br>threat " + (r.threat_score ?? 0).toFixed(2)).addTo(map);
});

document.getElementById("legend").innerHTML =
  "<span><span class='dot' style='background:" + D.dark_color +
  "'></span>dark contact</span>" +
  Object.entries(D.class_colors).map(([k, v]) =>
    "<span><span class='dot' style='background:" + v + "'></span>" + k +
    "</span>").join("") +
  "<span style='color:#2e7f93'>▭ protected / watch zones</span>";

document.getElementById("dossiers").innerHTML = recs.map(r => {
  const score = r.threat_score ?? 0;
  const cites = ((r.identity || {}).citations || []);
  const gaps = (r.ais_gaps || []).map(g =>
    "AIS silent " + g.duration_hours + " h from " + g.start +
    (g.inside_zone ? " inside " + g.inside_zone : "")).join(" · ");
  return "<div class='card" + (r.is_dark ? " dark-vessel" : "") + "'>" +
    "<div class='row'><span><span class='vid'>" + r.vessel_id + "</span> — " +
    r.vessel_class + " · " + (r.detection.length_m || "?") + " m" +
    (r.mmsi ? " · MMSI " + r.mmsi + (r.is_dark ? " (probable)" : "") : "") +
    "</span>" + (r.is_dark ? "<span class='badge'>DARK</span>" : "") + "</div>" +
    "<div class='meta'>" + (r.zones && r.zones.length ? r.zones.join(" · ")
      : "open water") + (gaps ? "<br>" + gaps : "") + "</div>" +
    "<div class='score'><span>threat " + score.toFixed(2) + "</span>" +
    "<span class='bar'><i style='width:" + (score * 100) + "%'></i></span>" +
    (r.rendezvous_with ? "<span>rendezvous with <span class='vid'>" +
      r.rendezvous_with + "</span></span>" : "") + "</div>" +
    "<p class='narr'>" + (r.threat_narrative || "") + "</p>" +
    (cites.length ? "<div class='srcs'>Sources: " + cites.map(s =>
      "<a href='" + s.url + "' target='_blank'>" + s.title + "</a>")
      .join(" · ") + "</div>" : "") +
    "</div>";
}).join("");
</script>
</body>
</html>
"""
