# Tank Calibration & Overflow Limits

_Last calibrated: 2026-07-15 (PTdevices depth meter serviced + tank re-calibrated)_

## Summary

The PTdevices depth meter was serviced and now reads the correct water height.
The tank volume is now computed from actual tank geometry rather than an
approximate capacity figure.

## Tank geometry

The tank is a vertical cylinder:

| Property | Value |
|---|---|
| Internal diameter | 7.2 ft = 86.4 in |
| Internal radius | 43.2 in |
| Cross-section area | π·(43.2)² = 5863 in² |
| Volume per inch of depth | 5863 in² ÷ 231 in³/gal ≈ **25.37 gal/in** |
| Overflow height (max) | **60 in** |
| Volume at overflow | 60 × 25.37 ≈ **1522 US gal** |

Because the tank is a straight cylinder, gallons scale linearly with depth, so
the linear conversion in `monitor/tank.py` is exact once it is anchored on the
true 60″ / 1522 gal pair:

```
gallons = (depth_inches / 60) * 1522   ≈ depth_inches × 25.37
```

Reference readings:

| Depth | Gallons |
|---|---|
| 60 in (overflow) | 1522 |
| 58 in (reading at calibration) | 1471 |
| 50 in | 1268 |
| 40 in | 1015 |

## "100%" vs. real capacity

Gallons are always the true, geometry-based figure (1522 = physical overflow).
But **all percentage displays and graphs treat the operational full — 1450 gal,
the level we normally stop at — as 100%.** This keeps every gauge, axis, and %
label anchored on the number we actually care about; the real 1522 peak simply
shows as ~105%.

`TANK_FULL_GALLONS = 1450` is the single knob for this. It is the denominator for:

- `tank.py` percentage (status page, emails, logged `tank_percentage`)
- e-paper + web graph 100% line, Y-axis %, and the big header %
- "hours to full" estimates (web + email)

| Gallons | Shown as | Depth |
|---|---|---|
| 1450 (operational full) | 100% | 57.1 in |
| 1475 (dosatron shutoff) | ~102% | 58.1 in |
| 1522 (physical overflow) | ~105% | 60.0 in |

The e-paper Y-axis cap `EPAPER_MAX_GRAPH_PCT` was raised 102 → 106 so the real
1522 peak still fits above the 100% line instead of clipping.

Physical capacity (1522) is still used where a true bound matters: the accurate
gallons conversion, the "X / 1522 gal" status line, and the fill-estimate ceilings.

> Side effect: percentage-based thresholds (e.g. `EPAPER_CONSERVE_WATER_THRESHOLD`,
> `EPAPER_LOW_WATER_HOURS_THRESHOLD`) now ride on the 1450 reference, so they trip
> at ~5% higher gallons than before. Adjust those %s if the old gallon trigger
> points are preferred.

## Config changes (`monitor/config.py`)

| Constant | Old | New | Reason |
|---|---|---|---|
| `TANK_HEIGHT_INCHES` | 58 | **60** | True overflow height |
| `TANK_CAPACITY_GALLONS` | 1400 | **1522** | Geometric volume at 60″ |
| `OVERRIDE_SHUTOFF_THRESHOLD` | 1400 | **1450** | Turn off supply override when a tank reading exceeds this |
| `OVERRIDE_DOSATRON_SHUTOFF_GALLONS` | 1425 | **1475** | Stop on the running dosatron estimate before a fresh reading arrives |

These constants flow through `tank.py` (gallons + percentage), `check.py`
(status/overflow display), and `epaper_jpg.py` (graph scaling), so all displays
update consistently.

### Overflow protection logic

- **Never fill past 60″ / 1522 gal** — that is the physical overflow point.
- **Tank sensor:** when a real reading exceeds **1450 gal**, the supply override
  turns off (`OVERRIDE_SHUTOFF_THRESHOLD`). ~72 gal / ~2.8″ of headroom.
- **Dosatron estimate:** if the running estimate (last real reading + dosatron
  gallons since) reaches **1475 gal**, the override turns off even without a new
  reading (`OVERRIDE_DOSATRON_SHUTOFF_GALLONS`). This catches high-flow events
  the 5-min sensor poll can't react to fast enough. ~47 gal / ~1.9″ of headroom.

### Override auto-on

- `OVERRIDE_ON_THRESHOLD = 1420` (2026-07-15, was 1370). Turns the supply
  override on when the tank drops to this level. Keeps a 30-gal on/off band
  below the 1450 shutoff, matching the old 1370→1400 spread.

## Chlorine / Dosatron dosing

- Measured free chlorine: **10 ppm** — consistent for the past few months.
- Dosatron injector setting: **0.07**.

(These are recorded for reference; the audio-based `GALLONS_PER_CLICK` counting
constant is separate and unchanged.)
