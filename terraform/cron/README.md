# Cron Jobs

Scheduled tasks that run on the Pumphouse Pi. These are **not** managed by
Terraform — they are installed manually via `crontab -e`. The scripts
themselves live in the project root (or `ecobee/`) and are called by path.

See [`crontab.example`](crontab.example) for the complete set of entries.

---

## Installing

```bash
# Review the entries first:
cat terraform/cron/crontab.example

# Install (replaces entire crontab — merge carefully if you have others):
crontab terraform/cron/crontab.example

# Or append to existing crontab:
(crontab -l; cat terraform/cron/crontab.example) | crontab -

# Verify:
crontab -l
```

---

## Jobs

| Schedule | Script | Purpose |
|----------|--------|---------|
| 5 AM, 4 PM, 10 PM | `update_reservations.sh` | Scrape TrackHS reservations; notify on changes |
| Every 5 min | `system_health_monitor.sh` | Log CPU temp, throttle flags, disk, memory |
| 5:30 AM, 9 PM | `ecobee/fetch_ecobee_temp_cron.sh` | Fetch thermostat readings |
| 2 AM | `log_daily_gph.py` | Calculate and log daily well GPH |

---

## Known issue — Ecobee path

When the ecobee scripts were moved to `ecobee/` during repo cleanup, the
live crontab on the Pi was **not** updated. The entry still reads:

```
/home/pi/src/pumphouse/fetch_ecobee_temp_cron.sh   # ← old path, broken
```

Fix with `crontab -e`, changing the path to:

```
/home/pi/src/pumphouse/ecobee/fetch_ecobee_temp_cron.sh
```

The `crontab.example` in this directory already has the correct path.
