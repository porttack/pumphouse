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

const url = "https://REDACTED-HOST:6443/api/epaper.bmp?tenant=no";
const dashboardUrl = "https://REDACTED-HOST:6443?totals=income"; // Your full dashboard

let widget = new ListWidget();

// Set the URL that opens when widget is tapped
widget.url = dashboardUrl;

try {
  let req = new Request(url);
  let image = await req.loadImage();

  widget.backgroundImage = image;

  // Subtle blue tint overlay for better readability on iOS
  let gradient = new LinearGradient();
  gradient.colors = [
    new Color("#2d5a7b", 0.3),
    new Color("#4a7c9e", 0.25),
    new Color("#6b9dc4", 0.2)
  ];
  gradient.locations = [0.0, 0.5, 1.0];
  gradient.startPoint = new Point(0, 0);
  gradient.endPoint = new Point(1, 1);

  widget.backgroundGradient = gradient;

} catch (error) {
  widget.backgroundColor = new Color("#2d5a7b");
  let errorText = widget.addText("Error loading tank status");
  errorText.textColor = Color.white();
  errorText.font = Font.systemFont(14);
}

// Request refresh every 5 minutes
// Note: iOS may not honor this exactly — it throttles widget refreshes
// based on battery, usage patterns, etc. Typical refresh is 5-15 minutes.
widget.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);

Script.setWidget(widget);
Script.complete();

// Show preview when running in Scriptable app (not as widget)
if (!config.runsInWidget) {
  await widget.presentMedium();
}
