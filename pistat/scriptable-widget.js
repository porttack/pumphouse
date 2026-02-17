// Pumphouse Tank Status - Scriptable Widget for iPhone
//
// Shows the e-paper tank status image as an iPhone widget.
// Updates every 5 minutes.
//
// Setup:
//   1. Install "Scriptable" from the App Store (free)
//   2. Open Scriptable, tap "+" to create a new script
//   3. Paste this entire file
//   4. Tap the title at top to rename it (e.g., "Tank Status")
//   5. Long-press your home screen → tap "+" → search "Scriptable"
//   6. Add a Medium widget
//   7. Long-press the widget → "Edit Widget" → choose your script
//
// The widget will show the tank BMP image with a subtle blue gradient overlay.
// Tap the widget to open Scriptable and see a preview.

// Replace YOUR-HOST with your DDNS hostname (from ~/.config/pumphouse/secrets.conf PUMPHOUSE_HOST)
const url = "https://YOUR-HOST:6443/api/epaper.bmp?tenant=no&scale=4";
const dashboardUrl = "https://YOUR-HOST:6443?totals=income"; // Your full dashboard

let widget = new ListWidget();
widget.url = dashboardUrl;

// File manager for caching
const fm = FileManager.local();
const cachePath = fm.joinPath(fm.documentsDirectory(), "water_system_cache.jpg");

let image;
let isError = false;

try {
  let req = new Request(url);
  image = await req.loadImage();
  
  // Save successful image to cache
  fm.writeImage(cachePath, image);
  
} catch (error) {
  // Try to load cached image
  if (fm.fileExists(cachePath)) {
    image = fm.readImage(cachePath);
    isError = true;
  } else {
    // No cache available, create a simple placeholder
    image = null;
  }
}

if (image) {
  widget.backgroundImage = image;
  
  let gradient = new LinearGradient();
  
  if (isError) {
    // Red-tinted gradient to indicate error/stale data
    gradient.colors = [
      new Color("#7b2d2d", 0.4),  // Dark red, slightly more opaque
      new Color("#9e4a4a", 0.35),
      new Color("#c46b6b", 0.3)
    ];
  } else {
    // Normal blue gradient
    gradient.colors = [
      new Color("#2d5a7b", 0.3),
      new Color("#4a7c9e", 0.25),
      new Color("#6b9dc4", 0.2)
    ];
  }
  
  gradient.locations = [0.0, 0.5, 1.0];
  gradient.startPoint = new Point(0, 0);
  gradient.endPoint = new Point(1, 1);
  
  widget.backgroundGradient = gradient;
} else {
  // Fallback if no cache exists
  widget.backgroundColor = new Color("#7b2d2d");
  let text = widget.addText("Unable to load water system data");
  text.textColor = Color.white();
  text.font = Font.systemFont(12);
}

widget.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);
Script.setWidget(widget);
Script.complete();
if (!config.runsInWidget) { await widget.presentMedium(); }