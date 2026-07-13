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

  /* --- rendezvous zoom inset --- */
  #rdv { display:none; }
  #rdv .eyebrow { font-size:11px; font-weight:700; letter-spacing:.08em;
                  text-transform:uppercase; color:var(--accent); }
  #rdv svg { width:100%; max-width:520px; display:block; margin:10px auto 0;
             background:#f2f6f8; border:1px solid var(--line); border-radius:8px; }
  #rdv .inset-note { font-size:13px; color:var(--muted); margin-top:8px; }
  @keyframes pulse { 0% { r:9; opacity:.65; } 100% { r:26; opacity:0; } }
  .pulse { animation:pulse 2.4s ease-out infinite; }
  @media (prefers-reduced-motion:reduce) { .pulse { animation:none; opacity:.25; } }

  .boat-label { background:rgba(251,252,253,.92); border:1px solid var(--line);
                border-radius:5px; color:var(--ink); font:600 11px system-ui;
                padding:1px 6px; box-shadow:none; }
  .boat-label-dark { color:#b32f2f; border-color:#d03b3b; }
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
<main>
  <div class="card" id="rdv">
    <span class="eyebrow">Rendezvous detail</span>
    <svg id="inset" viewBox="0 0 520 300" role="img"
         aria-label="Zoomed schematic of the rendezvous pair"></svg>
    <p class="inset-note" id="rdv-note"></p>
  </div>
  <div id="dossiers"></div>
</main>
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
  const marker = L.circleMarker([r.detection.lat, r.detection.lon],
    { radius: r.is_dark ? 9 : 6, color:c, fillColor:c, fillOpacity:0.85 })
    .bindPopup((r.name ? "<b>" + r.name + "</b><br>" : "") +
      "<b>" + r.vessel_id + "</b><br>" + r.vessel_class + " · " +
      (r.detection.length_m || "?") + " m" +
      (r.is_dark ? "<br><b style='color:" + D.dark_color + "'>DARK</b>" : "") +
      (r.mmsi ? "<br>MMSI " + r.mmsi + (r.is_dark ? " (probable)" : "") : "") +
      "<br>threat " + (r.threat_score ?? 0).toFixed(2)).addTo(map);
  if (r.name) marker.bindTooltip(r.name, { permanent:true, direction:"top",
    offset:[0,-9], className:"boat-label" + (r.is_dark ? " boat-label-dark" : "") });
});

document.getElementById("legend").innerHTML =
  "<span><span class='dot' style='background:" + D.dark_color +
  "'></span>dark contact</span>" +
  Object.entries(D.class_colors).map(([k, v]) =>
    "<span><span class='dot' style='background:" + v + "'></span>" + k +
    "</span>").join("") +
  "<span style='color:#2e7f93'>▭ protected / watch zones</span>";

// --- rendezvous zoom inset: drawn from the records, not hand-baked ---
(function () {
  const dark = recs.find(r => r.is_dark && r.rendezvous_with);
  const partner = dark && byId[dark.rendezvous_with];
  if (!dark || !partner) return;
  const kmPer = (lat) => [111.32 * Math.cos(lat * Math.PI / 180), 110.57];
  const gap = (dark.ais_gaps || [])[0];
  const pts = [
    { x: dark.detection.lon,    y: dark.detection.lat,    kind: "dark" },
    { x: partner.detection.lon, y: partner.detection.lat, kind: "partner" },
  ];
  if (gap) pts.push({ x: gap.last_known[1], y: gap.last_known[0], kind: "orphan" });
  const lat0 = pts.reduce((s, p) => s + p.y, 0) / pts.length;
  const lon0 = pts.reduce((s, p) => s + p.x, 0) / pts.length;
  const [kx, ky] = kmPer(lat0);
  pts.forEach(p => { p.ex = (p.x - lon0) * kx; p.ey = (p.y - lat0) * ky; });
  const ext = Math.max(1.1, ...pts.map(p => Math.max(Math.abs(p.ex), Math.abs(p.ey)))) * 1.45;
  const W = 520, H = 300, S = Math.min(W, H) / (2 * ext);
  const X = e => W / 2 + e * S, Y = e => H / 2 - e * S;
  const kmb = (a, b) => Math.hypot(a.ex - b.ex, a.ey - b.ey);
  const [pd, pp, po] = [pts[0], pts[1], pts[2]];
  const dDark = kmb(pd, pp), dOrph = po ? kmb(pd, po) : null;
  let s = "";
  // grid + range rings around the dark contact
  for (let g = -3; g <= 3; g++) {
    s += `<line x1="${W/2+g*S}" y1="0" x2="${W/2+g*S}" y2="${H}" stroke="#dde5ea" stroke-width="1"/>`;
    s += `<line x1="0" y1="${H/2+g*S}" x2="${W}" y2="${H/2+g*S}" stroke="#dde5ea" stroke-width="1"/>`;
  }
  [0.5, 1].forEach(rk => {
    s += `<circle cx="${X(pd.ex)}" cy="${Y(pd.ey)}" r="${rk*S}" fill="none"
          stroke="#b6c6d0" stroke-dasharray="3 4"/>
          <text x="${X(pd.ex)+rk*S+3}" y="${Y(pd.ey)-3}" font-size="10"
          fill="#7c8b98">${rk} km</text>`;
  });
  // partner AIS track tail
  const tail = (partner.track || []).slice(-6).map(t =>
    X((t.lon - lon0) * kx) + "," + Y((t.lat - lat0) * ky)).join(" ");
  if (tail) s += `<polyline points="${tail}" fill="none" stroke="${D.class_colors[partner.vessel_class] || '#eda100'}" stroke-width="2" opacity="0.6"/>`;
  // orphan last fix + dashed correlation line
  if (po) {
    s += `<line x1="${X(po.ex)}" y1="${Y(po.ey)}" x2="${X(pd.ex)}" y2="${Y(pd.ey)}"
          stroke="${D.dark_color}" stroke-width="1.5" stroke-dasharray="5 5" opacity="0.7"/>
          <text x="${X(po.ex)}" y="${Y(po.ey)-10}" font-size="11" text-anchor="middle"
          fill="${D.dark_color}">✕ AIS went silent</text>
          <text x="${X(po.ex)}" y="${Y(po.ey)+4}" font-size="14" text-anchor="middle"
          fill="${D.dark_color}">✕</text>`;
  }
  // partner
  s += `<circle cx="${X(pp.ex)}" cy="${Y(pp.ey)}" r="7"
        fill="${D.class_colors[partner.vessel_class] || '#eda100'}"/>
        <text x="${X(pp.ex)}" y="${Y(pp.ey)-12}" font-size="11" text-anchor="middle"
        fill="#16232e">${partner.name || partner.vessel_class} · ${partner.detection.length_m} m · MMSI ${partner.mmsi || "?"}</text>`;
  // dark contact: pulsing diamond
  s += `<circle class="pulse" cx="${X(pd.ex)}" cy="${Y(pd.ey)}" r="9"
        fill="none" stroke="${D.dark_color}" stroke-width="2"/>
        <rect x="${X(pd.ex)-6}" y="${Y(pd.ey)-6}" width="12" height="12"
        transform="rotate(45 ${X(pd.ex)} ${Y(pd.ey)})" fill="${D.dark_color}"/>
        <text x="${X(pd.ex)}" y="${Y(pd.ey)+24}" font-size="11" font-weight="700"
        text-anchor="middle" fill="${D.dark_color}">DARK · ${dark.detection.length_m} m ${dark.vessel_class}${dark.name ? " · prob. " + dark.name : ""}</text>`;
  document.getElementById("inset").innerHTML = s;
  document.getElementById("rdv-note").innerHTML =
    `The <b>${dark.detection.length_m} m dark hull</b> loiters <b>${dDark.toFixed(1)} km</b> ` +
    `off the ${partner.vessel_class}'s beam — the ${D.profile_id.includes("fishing") || D.profile_id.includes("bering") ? "fishing × reefer" : "watch"} pairing this profile hunts.` +
    (gap ? ` The orphan AIS track (MMSI ${dark.mmsi || "?"}) went silent <b>${dOrph.toFixed(1)} km</b> away, ` +
      `a ${gap.duration_hours} h gap${gap.inside_zone ? " inside " + gap.inside_zone : ""}.` : "");
  document.getElementById("rdv").style.display = "block";
})();

document.getElementById("dossiers").innerHTML = recs.map(r => {
  const score = r.threat_score ?? 0;
  const cites = ((r.identity || {}).citations || []);
  const gaps = (r.ais_gaps || []).map(g =>
    "AIS silent " + g.duration_hours + " h from " + g.start +
    (g.inside_zone ? " inside " + g.inside_zone : "")).join(" · ");
  return "<div class='card" + (r.is_dark ? " dark-vessel" : "") + "'>" +
    "<div class='row'><span>" +
    (r.name ? "<b>" + r.name + "</b> — " : "") +
    "<span class='vid'>" + r.vessel_id + "</span> — " +
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
