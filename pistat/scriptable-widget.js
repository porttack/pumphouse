// Pumphouse Tank Status - Scriptable Widget for iPhone
//
// Shows the e-paper tank status image as a Medium iPhone widget, with tappable
// zones mapped to the regions of the e-paper JPG:
//
//   ┌─────────────────────────────────────────────┐
//   │  1,234 gal        available        87%       │  header
//   │  └─► /water2                    └─► owner dash│
//   │                                               │
//   │              [ camera + graph ]               │  graph
//   │                 └─► /weather                  │
//   │                                               │
//   │  3d ago                          7/15 3:40 PM │  x-axis
//   │  └─► toggle 3d ⇄ 6h                            │
//   └─────────────────────────────────────────────┘
//
// TAP ZONES:
//   - gallons (top-left)   → /water2 page
//   - percent (top-right)  → owner dashboard  (also the whole-widget fallback)
//   - graph   (center)     → /weather page
//   - "3d ago" (bot-left)  → cycle the widget's graph window 3d ⇄ 6h
//
// NOTE on the timeframe toggle: Scriptable widgets can't run code in place on
// tap (no App Intents), so tapping "3d ago" briefly opens the Scriptable app,
// flips a stored hours value, then you return to the home screen. The widget
// picks up the new window on its next refresh (up to a few minutes). An instant
// in-place toggle would require a native WidgetKit widget with an App Intent.
//
// Setup:
//   1. Install "Scriptable" from the App Store (free)
//   2. Open Scriptable, tap "+", paste this file
//   3. Rename the script (the toggle deep-link uses its name automatically)
//   4. Long-press home screen → "+" → Scriptable → add a Medium widget
//   5. Long-press the widget → "Edit Widget" → choose this script

// Replace YOUR-HOST with your DDNS hostname (secrets.conf PUMPHOUSE_HOST)
const BASE = "https://YOUR-HOST:6443";

// Page routes for the tap zones
const URL_WATER2  = `${BASE}/water2`;
const URL_WEATHER = `${BASE}/weather`;
const URL_OWNER   = `${BASE}/?owner`;

// ── Timeframe state (cycled by tapping the "3d ago" label) ──────────────────
const HOURS_3D = 72;   // "3d ago"
const HOURS_6H = 6;    // "6h ago"
const fm = FileManager.local();
const hoursPath = fm.joinPath(fm.documentsDirectory(), "glimpse_hours.txt");

function readHours() {
  try {
    const v = parseInt(fm.readString(hoursPath), 10);
    return v === HOURS_6H ? HOURS_6H : HOURS_3D;
  } catch (e) {
    return HOURS_3D;
  }
}
function writeHours(h) {
  try { fm.writeString(hoursPath, String(h)); } catch (e) {}
}

// If launched from the toggle deep-link, flip the stored window and exit early
// (nothing else to do — the home-screen widget refreshes on its own timeline).
if (args.queryParameters && args.queryParameters.toggle) {
  const cur = readHours();
  writeHours(cur === HOURS_3D ? HOURS_6H : HOURS_3D);
  Script.complete();
} else {
  await buildWidget();
}

async function buildWidget() {
  const hours = readHours();

  const imgUrl = `${BASE}/api/epaper.jpg?tenant=no&scale=4&hours=${hours}`;
  const toggleUrl =
    `scriptable:///run/${encodeURIComponent(Script.name())}?toggle=1`;

  let widget = new ListWidget();
  widget.setPadding(0, 0, 0, 0);
  widget.url = URL_OWNER;   // fallback for any gap / dead area

  // ── Fetch image, fall back to cache ───────────────────────────────────────
  const cachePath = fm.joinPath(fm.documentsDirectory(), "water_system_cache.jpg");
  let image = null;
  let isError = false;
  try {
    let req = new Request(imgUrl);
    req.timeoutInterval = 15;
    image = await req.loadImage();
    fm.writeImage(cachePath, image);
  } catch (error) {
    if (fm.fileExists(cachePath)) {
      image = fm.readImage(cachePath);
      isError = true;
    }
  }

  if (!image) {
    widget.backgroundColor = new Color("#7b2d2d");
    let t = widget.addText("Unable to load water system data");
    t.textColor = Color.white();
    t.font = Font.systemFont(12);
    finish(widget);
    return;
  }

  widget.backgroundImage = image;

  let gradient = new LinearGradient();
  gradient.colors = isError
    ? [new Color("#7b2d2d", 0.4), new Color("#9e4a4a", 0.35), new Color("#c46b6b", 0.3)]
    : [new Color("#2d5a7b", 0.3), new Color("#4a7c9e", 0.25), new Color("#6b9dc4", 0.2)];
  gradient.locations = [0.0, 0.5, 1.0];
  gradient.startPoint = new Point(0, 0);
  gradient.endPoint = new Point(1, 1);
  widget.backgroundGradient = gradient;

  // ── Transparent tap-zone overlay ──────────────────────────────────────────
  // Sizes are a reference design for a ~329×155pt Medium widget; the e-paper
  // JPG has a near-identical 250:122 (≈2.05:1) aspect, so zones line up with the
  // image bands well enough across devices. Corners are the robust targets.
  const W = 329;
  const H_HEADER = 36;   // ~23% — gallons / percent row
  const H_GRAPH  = 92;   // ~59% — camera + chart
  const H_XAXIS  = 27;   // ~18% — "3d ago" / timestamp row

  const addZone = (row, w, h, url) => {
    const cell = row.addStack();
    cell.size = new Size(w, h);
    if (url) cell.url = url;
    return cell;
  };

  const col = widget.addStack();
  col.layoutVertically();
  col.spacing = 0;

  // Header row: gallons (left) → water2 ; percent (right) → owner
  const header = col.addStack();
  header.layoutHorizontally();
  addZone(header, 125, H_HEADER, URL_WATER2);   // "1,234 gal"
  header.addSpacer();                            // "available water" — dead → owner
  addZone(header, 115, H_HEADER, URL_OWNER);     // "87%"

  // Graph row: whole camera/chart band → weather
  const graph = col.addStack();
  graph.layoutHorizontally();
  addZone(graph, W, H_GRAPH, URL_WEATHER);

  // X-axis row: "3d ago" (left) → toggle window ; timestamp (right) — dead
  const xaxis = col.addStack();
  xaxis.layoutHorizontally();
  addZone(xaxis, 140, H_XAXIS, toggleUrl);       // "3d ago" / "6h ago"
  xaxis.addSpacer();                             // timestamp — dead → owner

  finish(widget);
}

function finish(widget) {
  widget.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);
  Script.setWidget(widget);
  Script.complete();
  if (!config.runsInWidget) { widget.presentMedium(); }
}
