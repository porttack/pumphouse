# Ring Camera Integration

Displays a live snapshot from a Ring camera on the dashboard alongside the e-paper display and the most recent sunset timelapse frame. On desktop the three images share a row at equal height; on mobile the Ring snapshot stacks below the e-paper display.

## How it works

- The dashboard fetches `/ring-snapshot`, which proxies a JPEG from Ring's API.
- Results are cached in memory for 60 seconds to stay within Ring's rate limits.
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

## Token management

The token file is created by `ring_setup.py` and updated automatically by the web server whenever Ring issues a refreshed token. The refresh token is valid for roughly 12 months. If it expires (or you change your Ring password), run `ring_setup.py` again.

The token file contains sensitive OAuth credentials — it is not committed to git (it lives outside the repo in `~/.config/`).

## Snapshot freshness

Ring uploads snapshots periodically from the camera. Without an active live-view session, snapshots are typically 1–5 minutes old. The 60-second server-side cache adds at most one additional minute of lag.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Ring image missing from dashboard | Check `/ring-snapshot` in browser — error message will indicate the cause |
| `Ring not configured` | Run `bin/ring_setup.py` |
| `Ring token unreadable` | Delete `~/.config/pumphouse/ring_token.json` and re-run setup |
| `Ring error: ...` | Usually a transient network issue; the 60-second cache serves stale data if available |
| Unclosed session warnings (setup script) | Cosmetic only; token was saved successfully |
