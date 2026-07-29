# Dosing Tank Depth Estimate

_First calibrated: 2026-07-25, from a single measured interval (6/16 → 7/17)._

## Summary

The chlorine dosing tank (the small chemical feed tank the dosatron draws
from — separate from the 1522-gal main water tank) has no depth sensor. We
estimate its current depth from dosatron click counts, anchored to the last
time someone physically measured it. Treat this as a rough gauge for "do I
need to call someone to refill it," not a precise reading — see the
uncertainty note below.

## Tank geometry

Assumed vertical cylinder, diameter estimated at **15 in**:

| Property | Value |
|---|---|
| Diameter | 15 in (estimate) |
| Radius | 7.5 in |
| Cross-section area | π·(7.5)² ≈ 176.7 in² |
| Volume per inch of depth | 176.7 in² ÷ 231 in³/gal ≈ **0.765 gal/in** |
| Rated capacity | 15 gal |
| Depth at rated capacity | 15 ÷ 0.765 ≈ 19.6 in |

The tank was measured at 23 in when full-ish, above the 19.6 in "rated
capacity" mark — so either there's freeboard above the fill line, or the
tank isn't a plain cylinder all the way down (tapered bottom/sump). Doesn't
affect the gal/inch math near the working range, but means "inches
remaining" shouldn't be read as "inches until physically empty."

## Why clicks alone don't work: the fudge factor

Dosatron clicks are logged the same way as the main tank's water-flow
estimate — one detection ≈ one piston stroke, counted only inside
pressure-HIGH windows (see `docs/event-types.md`; idle noise while the pump
is off is excluded). In theory, chemical drawn per click = water volume per
click × the injector's ratio-dial setting. The dial reads **0.07**, which on
a Dosatron D14MZ2-class unit (adjustment range 0.07%–1.6%, i.e. 1:1400–1:62)
means **0.07% ≈ 1:1429**.

That theoretical math didn't hold up against a real measurement:

| | Theoretical (dial ratio) | Actual (measured depth) |
|---|---|---|
| 6/16 → 7/17 dosing tank depth | — | 23 in → 19 in (4 in drop) |
| Implied chemical volume | ~1.16 gal | ~3.06 gal (4 in × 0.765 gal/in) |

The tank lost **~2.6× more liquid** than the dial ratio predicts from the
same 22,101 clicks recorded in that window. The suspected cause: air in the
suction line, so some piston strokes pull air instead of a full stroke of
liquid — the piston still clicks, but less chemical actually moves per click
than the ratio setting implies.

Rather than trust the dial math, `DOSING_TANK_CHEM_GAL_PER_CLICK` is
calibrated directly against the real depth change:

```
3.06 gal ÷ 22,101 clicks ≈ 0.0001385 gal/click
```

This bakes the air-loss effect (and any other systematic error) into the
constant automatically, since it's anchored to what the tank actually lost,
not to how the pump is theoretically supposed to behave.

## Estimating current depth

```
depth_now = anchor_depth − (clicks_since_anchor × DOSING_TANK_CHEM_GAL_PER_CLICK) / DOSING_TANK_GAL_PER_INCH
```

Implemented in `monitor/dosatron.py::estimate_dosing_tank_depth()`. Clicks
since the anchor are summed the same way as the fudge-factor derivation —
inside pressure-HIGH windows only (`_sum_pressure_high_clicks`).

By default (`conservative=True`) the estimated chemical volume is inflated by
`DOSING_TANK_CONSERVATIVE_MARGIN = 1.25` (+25%) before converting to inches,
so the estimate errs toward "less left than you think." This is a blunt
hedge, not a derived number — the fudge factor above is a single calibration
point on a system with a known, not-fully-understood air-loss issue, so
there's no strong basis for trusting it to ±0%.

## Anchor point — how to recalibrate

`DOSING_TANK_ANCHOR_DEPTH_IN` / `DOSING_TANK_ANCHOR_TS` in `config.py` hold
the last physical reading. The estimate is only as good as how stale that
anchor is — update both values every time someone physically checks the
tank:

1. Read the tank depth in inches.
2. Set `DOSING_TANK_ANCHOR_DEPTH_IN` to that value.
3. Set `DOSING_TANK_ANCHOR_TS` to the reading's timestamp (ISO 8601).

Optionally, once a second real reading exists, recompute
`DOSING_TANK_CHEM_GAL_PER_CLICK` from the gap between the two readings
(depth change × gal/inch, divided by clicks in between) rather than
continuing to rely on the single 6/16→7/17 data point above — the more
intervals this is calibrated against, the more it averages out one-off air
pockets vs. a genuinely wrong ratio.

## Where the estimate shows up

- **Daily status email** — current estimated depth, next to the other sensor readings.
- **`daily.csv`** — `dosing_tank_depth_est_in` column, one estimate per day (end-of-day clicks).
- **e-paper JPG display** (`epaper_jpg.py` only, not the guest-facing `/api/epaper.bmp`) — small number in the corner.

## Config (`monitor/config.py`)

| Constant | Value | Meaning |
|---|---|---|
| `DOSING_TANK_DIAMETER_INCHES` | 15 | Estimated tank diameter |
| `DOSING_TANK_GAL_PER_INCH` | 0.765 | Cylinder cross-section, gal/inch |
| `DOSING_TANK_CAPACITY_GALLONS` | 15 | Rated capacity |
| `DOSING_TANK_CHEM_GAL_PER_CLICK` | 0.0001385 | Empirical fudge factor (see derivation above) |
| `DOSING_TANK_CONSERVATIVE_MARGIN` | 1.25 | Safety inflation applied to estimated consumption |
| `DOSING_TANK_ANCHOR_DEPTH_IN` | 22 | Last known physical depth reading (7/17 same-day refill to 22", not the pre-refill 19") |
| `DOSING_TANK_ANCHOR_TS` | 2026-07-17T23:59:59 | Timestamp of that reading |
