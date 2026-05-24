# Event Types

All events are appended to `~/.local/share/pumphouse/events.csv` with columns:
`timestamp, event_type, pressure_state, float_state, tank_gallons, tank_depth, tank_percentage, estimated_gallons, relay_bypass, relay_supply_override, notes`

---

## System lifecycle

| Event type | Source | Description |
|---|---|---|
| `INIT` | poll.py | Service startup; also logged when relay states are restored from disk |
| `SHUTDOWN` | poll.py | Clean shutdown (SIGTERM / KeyboardInterrupt) |
| `CRASH` | poll.py | Unhandled exception; notes contain exception type, message, and last traceback line |
| `EXCESSIVE_RESTARTS` | restart_tracker.py | Crash-loop guard triggered — more than 4 restarts in 24 hours |

---

## Pressure / pump

| Event type | Source | Description |
|---|---|---|
| `PRESSURE_HIGH` | poll.py | Pressure crossed ≥10 PSI — pump started |
| `PRESSURE_LOW` | poll.py | Pressure crossed <10 PSI — pump stopped; notes include duration and dosatron gallons (e.g. `Duration: 262.9s, Dosatron: 2.10 gal (30 clicks)`) |
| `PRESSURE_RECOVERY` | poll.py | Short pressure dropout recovered within the gap-tolerance window; notes include gap duration |

---

## Float / tank level

| Event type | Source | Description |
|---|---|---|
| `FLOAT_CALLING` | poll.py | Float sensor changed to CALLING — tank needs water |
| `FLOAT_FULL` | poll.py | Float sensor changed to FULL — tank topped off |
| `TANK_LEVEL` | poll.py | Tank level reading changed by more than 0.1 gal; notes show delta (e.g. `Changed by +35.0 gal`) |
| `TANK_OUTAGE_RECOVERY` | poll.py | Tank sensor data recovered after a read-failure gap |

---

## Valve control

| Event type | Source | Description |
|---|---|---|
| `PURGE` | poll.py | Bypass relay pulsed for a filter purge cycle |
| `OVERRIDE_AUTO_ON` | poll.py | Supply override relay was automatically turned ON (fill cycle started) |
| `OVERRIDE_SHUTOFF` | poll.py | Supply override relay was automatically turned OFF (tank full or safety shutoff) |
| `REMOTE_CONTROL` | web.py | Relay toggled from the web dashboard; notes contain the action taken |

---

## Flow detection

| Event type | Source | Description |
|---|---|---|
| `FULL_FLOW` | poll.py | Extended full-flow period detected (pressure ~100% continuously, or GPH surge); notes include start time, duration, gallons pumped, tank gain, and estimated GPH |
| `BYPASS_FLOW` | dosatron.py | Dosatron bypass valve is open and water is flowing through the bypass path |

---

## Notifications sent

These are logged at the moment an alert is dispatched. They do not change system state.

| Event type | Description |
|---|---|
| `NOTIFY_TANK_FULL` | Tank float confirmed FULL for 3+ consecutive readings |
| `NOTIFY_TANK_DECREASING_<N>` | Tank level crossed below threshold N gallons (e.g. `NOTIFY_TANK_DECREASING_1000`) |
| `NOTIFY_TANK_INCREASING_<N>` | Tank level crossed above threshold N gallons |
| `NOTIFY_WELL_RECOVERY` | Tank gained 50+ gallons after a stagnation period |
| `NOTIFY_WELL_DRY` | No 50+ gallon refill detected in several days |
| `NOTIFY_HIGH_FLOW` | Tank filling at unusually high GPH (fast fill mode) |
| `NOTIFY_BACKFLUSH` | Carbon filter backflush detected; notes include estimated gallons used |
| `NOTIFY_FULL_FLOW` | Full-flow notification dispatched (see `FULL_FLOW` above) |
| `NOTIFY_OVERRIDE_ON` | Alert sent when supply override turned on |
| `NOTIFY_OVERRIDE_OFF` | Alert sent when supply override turned off (includes safety shutoffs) |
| `NOTIFY_TANK_OUTAGE` | Tank sensor data unavailable for an extended period |

---

## Scheduled / housekeeping

| Event type | Source | Description |
|---|---|---|
| `DAILY_STATUS_EMAIL` | poll.py | Daily status email sent; notes include current tank level and whether a check-in is scheduled |
| `CHECKOUT_REMINDER` | poll.py | Checkout reminder email sent; notes include guest name |
| `VEHICLE_DETECTED` | poll.py | Vehicle count increased while property is unoccupied |
| `VEHICLE_DEPARTED` | poll.py | Vehicle count decreased while property is unoccupied |
| `TIMELAPSE_DELETED` | sunset_timelapse.py | Timelapse video deleted; notes contain reason |
| `TIMELAPSE_EMAIL` | sunset_timelapse.py | Timelapse email sent |
| `gph_daily` | log_daily_gph.py | Daily GPH summary logged by cron job |

---

## Reservations

Logged by `bin/check_new_reservations.py` (runs via cron).

| Event type | Description |
|---|---|
| `CHECK-IN` | Guest checked in (occupancy changed to occupied) |
| `CHECK-OUT` | Guest checked out (occupancy changed to unoccupied) |
| `NEW_RESERVATION` | New reservation detected in the booking system |
| `CANCELED_RESERVATION` | Reservation was canceled |
| `CHANGED_RESERVATION` | Existing reservation dates or details changed |

---

## Work orders

| Event type | Source | Description |
|---|---|
| `NEW_WORK_ORDER` | bin/check_new_work_orders.py | New maintenance work order created |
