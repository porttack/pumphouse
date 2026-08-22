# Main-tank chlorine estimate and shock dosing

Two related things live here: the running **ppm estimate** for the 1522-gal main
tank, and the **dose calculator** that turns that estimate into "how many fluid
ounces of 12% chlorine should someone pour in."

Both are rough. Read the uncertainty section before acting on either.

## The ppm estimate

Model: well-mixed, constant-volume tank. Each source blends the estimate toward
its own assumed inflow concentration, proportional to gallons delivered:

    ppm_new = c_in + (ppm_old - c_in) * exp(-gallons / tank_gallons)

- **Dosatron water** enters at `CHLORINE_STEADY_STATE_PPM` (10.0), so the
  estimate recovers toward steady state whenever the dosatron is running.
  Gallons come from the click count — real ground truth.
- **Bypass water** enters at 0 ppm, so the estimate declines while bypassing.
  Gallons come from FFT-detected flow seconds × the calibrated bypass GPM
  (`monitor/bypass_calibration.py`), *not* relay-open time.

State persists in `chlorine_ppm_state.json`, advanced once per ~15-min snapshot
by `monitor/poll.py`. `/water2` projects it forward live during active bypass.

`ppm_series_by_day()` replays the same math from `CHLORINE_ANCHOR_PPM` /
`CHLORINE_ANCHOR_TS` to produce historical per-day values for `daily.csv`,
since the live path only persists one current number.

## The dose calculator

`monitor/chlorine.py::dose_floz(current_ppm, tank_gallons, target_ppm)`.

`CHLORINE_DOSE_PERCENT` (12.0) is read as **trade percent** — grams of available
chlorine as Cl2 per 100 mL, which is how liquid chlorine is normally sold. So
12% = 120 g/L. A jug labeled 12% *by weight* is slightly stronger (~136 g/L),
so the trade reading errs toward a marginally larger pour. That is deliberate:
jugs lose strength with age, and the owner prefers overshoot because the house
carbon filter strips the excess and getting maintenance on site is difficult.

Useful anchors for a ~1350 gal tank:

| amount of 12% | ppm added |
|---|---|
| 1.4 fl oz | ~1 |
| 1 cup (8 fl oz) | ~6 |
| 1 quart | ~24 |
| **1 gallon** | **~100 — never** |

Targets are `CHLORINE_DOSE_TARGET_PPM` (15, primary) and
`CHLORINE_DOSE_TARGET_PPM_ALT` (10, for comparison). The calculator assumes the
whole dose stays in the tank — no demand satisfied on contact — so a real pour
may land slightly under target.

## Where it surfaces

- `/water2` — "Est. chlorine (rough)" plus a "12% chlorine to add" row.
- Daily status email — a "Tank Chlorine (est.)" tile with both dose targets, and
  `Cl~` / `oz→15` / `oz→10` columns in the daily table.
- `daily.csv` — `chlorine_ppm_est`, `chlorine_floz_to_15`, `chlorine_floz_to_10`.

## Recording a manual chlorine addition

The model only sees water movement, so a physical pour is invisible to it — every
ppm figure after an unrecorded pour stays wrong. Record it one of two ways:

**From /water2** — "+ Log a chlorine pour" under the chlorine rows. Amount (fl oz
or cups), strength, how many hours ago, and a note. Auth-gated, since it mutates
the running estimate.

**From the CLI:**

    venv/bin/python3 bin/add_chlorine.py 12
    venv/bin/python3 bin/add_chlorine.py 1.5 --cups --note "after filter swap"
    venv/bin/python3 bin/add_chlorine.py 12 --ago 6h        # poured 6 hours ago
    venv/bin/python3 bin/add_chlorine.py 12 --dry-run       # preview only
    venv/bin/python3 bin/add_chlorine.py --list

Either path calls `monitor/chlorine.py::record_addition()`, which:

1. converts fl oz → ppm via `ppm_from_floz()` against the live tank volume,
2. decays the bump by tank throughput since the pour, if it is being logged after
   the fact (`--ago` / `hours_ago`) — dosatron clicks plus FFT bypass flow,
3. re-reads `chlorine_ppm_state.json` immediately before writing (the snapshot
   updater owns the same file — a stale cached copy is exactly how the
   `relay_state.json` clobbering bug happened),
4. appends the full record to `chlorine_additions.jsonl`,
5. logs a `CHLORINE_ADDED` event so it shows up in the dashboard.

`ppm_series_by_day()` re-applies each pour at its own point in the timeline using
the *undecayed* `ppm_bump_raw`, so `daily.csv` and the live estimate agree. Pours
dated before `CHLORINE_ANCHOR_TS` are skipped — the anchor already reflects them.

A pour is a step change with no error bars of its own, so it does not make the
estimate more trustworthy — it just stops it from being wrong by the size of the
pour. The uncertainty below still applies afterward.

## Uncertainty (analysis 2026-08-22)

Monte Carlo over every input put the estimate's 90% range at roughly
**4.7–10.5 ppm** against a point estimate of 7.0 — call it ±40%. What drives the
width, largest first:

| input | contribution to spread |
|---|---|
| steady-state anchor (strip reading) | 4.1 ppm |
| chlorine demand of incoming raw water | 2.5 ppm |
| bypass GPM | 2.1 ppm |
| mixing / short-circuiting | 0.8 ppm |
| tank volume + sensor | 0.3 ppm |
| the manual ppm anchor | 0.02 ppm |

Two structural facts worth knowing:

**The anchor washes out.** Error decays as `exp(-gallons/tank_gallons)`. After
4 turnovers only ~2% of an anchor error survives, so `CHLORINE_ANCHOR_PPM`
stops mattering about 10 days after it is set. The flip side: the estimate has
no memory beyond the last 1–2 turnovers, so it is only as good as the *recent*
inflow accounting.

**Bypass-only stretches are the weak spot.** In dosatron mode, clicks give an
independent check on inflow. In bypass mode nothing does — it all rests on
flow-seconds × GPM. Uncertainty grows for as long as bypass carries the house.

The two dominant errors are one-sided and oppose each other: unmodeled chlorine
demand can only push the true value **down**, while a steady state above 10 (the
strips cannot resolve 10–30 ppm) can only push it **up**. They roughly cancel
near the middle, which is why 7.0 survives as the median.

### Known biases in the inputs

- Flow detection nets out ~4% **under**-counted (see below), which understates
  dilution and biases ppm high. Partly self-cancelling, since the same
  undercount inflates the calibrated GPM that the flow time is multiplied by.
- `FlowRecorder.cancel()` discards the entire in-progress recording when bypass
  turns off or the service restarts — 55 min of real flow lost since May,
  including single episodes of 31 and 11 minutes. Episodes under
  `FLOW_MIN_SECS` (15 s) are also dropped (~6 min).
- Against that, the 5 s start / 10 s stop hysteresis adds ~5 s per episode
  (+29 min), so the net is a modest undercount.

## Recalibrating

The single highest-value measurement is a **titration reading of the
dosatron-fed steady state** (a DPD kit, not strips). It collapses the largest
error term and makes the demand question answerable. Failing a kit: dilute a
tank sample 1:1 with distilled water, read the strip, double the result — that
brings a 14 ppm tank into the readable band.

Do not add a chlorine-demand factor to the model without a real data point:
known bypass gallons with a trustworthy before/after ppm reading. Same
manual-recalibration-only stance as `docs/dosing-tank-calibration.md`.
