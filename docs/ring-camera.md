# Ring Camera Integration

Displays a live snapshot from a Ring camera on the dashboard alongside the e-paper display and the most recent sunset timelapse frame. On desktop the three images share a row at equal height; on mobile the Ring snapshot stacks below the e-paper display.

## How it works

- The dashboard fetches `/ring-snapshot`, which proxies a JPEG from Ring's API.
- Results are cached to disk for 10 minutes (`RING_CACHE_MINUTES`) and shared across all processes (gunicorn workers, monitor daemon, email notifier) — Ring's API is called at most once per 10 minutes regardless of load.
- The OAuth token is saved to disk and refreshed automatically — no re-authentication needed unless you change your Ring password.
- If Ring is unreachable the image is silently hidden and the other two images resize to fill the row.

## One-time setup

```bash
cd /home/pi/src/pumphouse && source venv/bin/activate
python3 bin/ring_setup.py
```

The script will:
1. Prompt for your Ring account email and password.
2. Handle the 2FA code sent to your phone or email.
3. Save an OAuth token to `~/.config/pumphouse/ring_token.json`.
4. Print the names of all Ring cameras on the account.

## Configuration

`~/.config/pumphouse/secrets.conf` — optional, only needed if you have more than one Ring camera:

```
RING_CAMERA_NAME=Front Door
```

Leave this unset to use the first camera found on the account.

`config.py` constants (not normally changed):

| Constant | Default | Purpose |
|---|---|---|
| `RING_CAMERA_NAME` | `''` | Camera name; first device if blank |
| `RING_TOKEN_FILE` | `~/.config/pumphouse/ring_token.json` | OAuth token path |
| `RING_CACHE_FILE` | `~/.config/pumphouse/ring_snapshot_cache.jpg` | Shared JPEG cache |
| `RING_CACHE_MINUTES` | `10` | Cache TTL in minutes |

## Token management

The token file is created by `ring_setup.py` and updated automatically by the web server whenever Ring issues a refreshed token. The refresh token is valid for roughly 12 months. If it expires (or you change your Ring password), run `ring_setup.py` again.

The token file contains sensitive OAuth credentials — it is not committed to git (it lives outside the repo in `~/.config/`).

## Snapshot freshness

Ring uploads snapshots periodically from the camera. Without an active live-view session, snapshots are typically 1–5 minutes old. The 10-minute server-side cache adds up to 10 additional minutes of lag.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Ring image missing from dashboard | Check `/ring-snapshot` in browser — error message indicates the cause |
| `Ring not configured` | Run `bin/ring_setup.py` |
| `Ring token unreadable` | Delete `~/.config/pumphouse/ring_token.json` and re-run setup |
| `Ring snapshot error: ...` | Usually a transient network issue; stale cache is served if available |
| Unclosed session warnings (setup script) | Cosmetic only; token was saved successfully |

## Logs

All Ring activity is logged to the systemd journal. Filter with:

```bash
# Live (both services)
sudo journalctl -u pumphouse-web -u pumphouse-monitor -f | grep -i "ring"

# History only
sudo journalctl -u pumphouse-web -u pumphouse-monitor | grep -i "ring"
```

Log levels: **INFO** on successful fetch, **WARNING** on recoverable issues (missing token, no devices, None returned), **ERROR** on API failures.
