# Pumphouse TODO / Known Issues

## Disabled — Needs Investigation

### NOTIFY_FULL_FLOW (disabled 2026-04-10)
- Detection logic is noisy and triggers false positives
- Both pressure-based (`check_full_flow_status`) and GPH-surge-based
  (`check_bypass_full_flow_status`) paths affected
- Sends spurious emails and pollutes the event log
- `NOTIFY_FULL_FLOW_ENABLED = False` in config.py
- `FULL_FLOW` and `NOTIFY_FULL_FLOW` hidden in `DASHBOARD_HIDE_EVENT_TYPES`
- To fix: review `find_full_flow_periods()` in stats.py and the GPH surge
  thresholds; add better hysteresis before re-enabling

### NOTIFY_TANK_OUTAGE (disabled 2026-04-10)
- Outage detection / recovery not working as intended
- `NOTIFY_TANK_OUTAGE_ENABLED = False` in config.py
- To fix: review `send_tank_outage_notification()` and `log_tank_outage_recovery()`
  in poll.py; clarify what triggers "outage" vs normal tank poll gaps

## Fixed

### Bypass timer cancel left bypass ON (fixed 2026-04-10)
- `SECRET_BYPASS_CANCEL_TIMER_TOKEN` action removed the timer file but did
  not call `set_bypass('OFF')`, leaving bypass running indefinitely
- Confirmed from events.csv: Apr 9 bypass ran overnight until manually
  turned off Apr 10 at 7:32 AM
- Fixed: cancel timer now calls `set_bypass('OFF')` and logs
  "Bypass timer cancelled, bypass turned OFF"
