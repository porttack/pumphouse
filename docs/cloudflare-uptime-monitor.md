# Internet Uptime Monitor

A Cloudflare Worker that probes the Pi's `/ping` endpoint on a cron schedule, stores history in KV, and serves a visual uptime dashboard at `onblackberryhill.com/internet`.

## How It Works

```
Every N minutes (Cron Trigger):
  Worker fetches https://onblackberryhill.com/ping (10s timeout)
  Records { timestamp, up: true/false } in Workers KV (7-day TTL)

On request to onblackberryhill.com/internet:
  Worker reads all KV entries (paginated)
  Renders HTML dashboard with timeline graphs, uptime stats, and outage list
```

This is an **end-to-end probe**: the Worker makes a real HTTP request through the Cloudflare Tunnel to the Pi's Flask server and checks for a successful response. If the Pi loses internet, the tunnel drops, and the probe fails. If the tunnel stalls (TCP alive but no data flowing), the 10-second timeout catches it.

The `/ping` route on the Pi simply returns `ok` with HTTP 200 — it does no real work, making it cheap and reliable.

## Dashboard Features

- **Current status** — Online / Offline with last-checked time (Pacific time)
- **Estimated poll interval** — shown in the subtitle, derived from recent entry timestamps
- **Three timeline graphs** — Past 4 hours, 24 hours, and 7 days
- **Color coding** — Green = up, red = down, dark = no data
- **Uptime percentage + downtime duration** — time-based, matching the outage list exactly
- **Hover tooltips on red segments** — shows exact outage window e.g. `2:14 AM – 3:31 AM`
- **Outage list** — table of all outages in the past 7 days with start, end, and duration (newest first)
- **Raw JSON** available at `/internet.json`
- **Manual trigger** at `/internet/trigger` for testing

---

## Setup

### Prerequisites

- Domain (`onblackberryhill.com`) managed by Cloudflare (orange cloud proxied)
- A running Cloudflare Tunnel (cloudflared) connecting the Raspberry Pi
- A Cloudflare account with Workers and KV available (free tier is sufficient)
- The Pi's Flask web server running with the `/ping` route

---

### Step 1 — Add the `/ping` Route to the Pi

In `monitor/web.py`, add:

```python
@app.route('/ping')
def ping():
    return 'ok', 200
```

Restart the web service:

```bash
sudo systemctl restart pumphouse-web.service
```

Verify locally: `curl -sk https://localhost:6443/ping` should return `ok`.

---

### Step 2 — Create Two KV Namespaces

In the Cloudflare dashboard: **Workers & Pages → KV → Create namespace**

Create two namespaces and note their IDs:

| Purpose | Suggested Name |
|---|---|
| Per-poll log entries | `blackberry-uptime-log` |
| Latest status cache | `blackberry-uptime-current` |

---

### Step 3 — Create the Worker

**Workers & Pages → Create → Worker**

Name it `blackberry-uptime`, dismiss the Hello World stub, and paste the full worker code (`blackberry-uptime.js` in this repo).

---

### Step 4 — Bind KV Namespaces

Worker → **Settings → Variables → KV Namespace Bindings → Add binding**

| Variable Name | KV Namespace |
|---|---|
| `UPTIME_LOG` | `blackberry-uptime-log` |
| `UPTIME_CURRENT` | `blackberry-uptime-current` |

No secrets or API tokens are required — the worker makes an outbound HTTP request to your own domain, not to the Cloudflare API.

---

### Step 5 — Add the Cron Trigger

Worker → **Triggers → Cron Triggers → Add Cron Trigger**

Recommended: `*/4 * * * *` (every 4 minutes) — 360 writes/day, well within the free KV write limit.

Update `POLL_INTERVAL_MS` in the worker config section to match your chosen interval.

---

### Step 6 — Add the Route

Worker → **Triggers → Routes → Add Route**

| Field | Value |
|---|---|
| Route | `onblackberryhill.com/internet*` |
| Zone | `onblackberryhill.com` |

The trailing `*` wildcard is required so that `/internet.json` and `/internet/trigger` also match.

---

### Step 7 — Test

Visit `https://onblackberryhill.com/internet/trigger` a handful of times to seed initial data, then visit `https://onblackberryhill.com/internet`. The cron will fill in data automatically going forward.

To verify data is landing in KV: **Workers & Pages → KV → blackberry-uptime-log → View** — you should see `log:2026-...` keys appearing.

---

## Architecture Notes

### KV Storage

Each entry is stored as:
```json
{ "ts": "2026-03-13T05:00:00.000Z", "up": true }
```

Key format: `log:2026-03-13T05:00:00.000Z` — ISO timestamps sort lexicographically, so KV list order is chronological.

TTL is set to 7 days on every entry, so old data expires automatically. No cleanup needed.

### KV Pagination

Cloudflare KV `list()` has a hard limit of 1000 keys per call. At 4-minute intervals, 7 days = ~2,520 entries. The worker uses cursor-based pagination to fetch all pages:

```javascript
async function listAllKeys(env, prefix) {
  let keys = [];
  let cursor = undefined;
  do {
    const opts = { prefix, limit: 1000 };
    if (cursor) opts.cursor = cursor;
    const result = await env.UPTIME_LOG.list(opts);
    keys = keys.concat(result.keys);
    cursor = result.list_complete ? undefined : result.cursor;
  } while (cursor);
  return keys;
}
```

### Downtime Accounting

Downtime is computed from **timestamp differences**, not entry counts. The `downtimeIntervals()` function walks the sorted entry array, recording the timestamp of each first-down entry and the timestamp of the next up entry as `{start, end}` pairs. This is how both the uptime percentage and the outage table are computed, so they always agree — even if the cron interval has changed over time.

### Timeline Graphs

Each graph divides its time window into fixed buckets:

| Graph | Buckets | Bucket size |
|---|---|---|
| 4 hours | 240 | 1 minute |
| 24 hours | 288 | 5 minutes |
| 7 days | 336 | 30 minutes |

Buckets with no entries inherit the last known state if the gap is small (expected between polls), or show as grey if the gap is large (genuine no-data period). A bucket with any failed probe is red; any bucket with at least one successful probe is green.

### Estimated Poll Interval

The subtitle shows the actual observed poll interval, estimated from the median gap between the 10 most recent KV entries. This reflects what the cron is really doing rather than relying on the hardcoded `POLL_INTERVAL_MS` constant.

### Hover Tooltips

Tooltips are pure CSS using `::after` pseudo-elements with `data-tip` attributes — no JavaScript required. Each red bar carries a `data-tip` with the start–end time of that bucket in Pacific time.

### Timezone

All displayed times use `America/Los_Angeles`, which automatically handles PST/PDT transitions.

---

## Cloudflare Free Tier Usage

At `*/4 * * * *` (every 4 minutes):

| Resource | Usage | Free Limit |
|---|---|---|
| Worker requests | ~360/day (cron) + dashboard views | 100,000/day |
| KV writes | ~360/day | 1,000/day |
| KV reads | ~2,520 per dashboard load | 10,000,000/day |
| KV storage | ~2,520 entries × ~80 bytes ≈ 200 KB | 1 GB |

At 4-minute intervals you stay comfortably within the free KV write limit of 1,000/day.

---

## Files

| File | Description |
|---|---|
| `cloudflare-uptime-monitor.md` | This document |
| `blackberry-uptime.js` | Full Cloudflare Worker source code |

---

## Troubleshooting

### Error 1101 — Worker threw exception
Check **Workers & Pages → your worker → Logs → Begin log stream**, then reload. Common causes:
- KV namespace bindings not added (check Settings → Variables)
- KV list limit exceeded (should be handled by pagination)

### Dashboard shows "No data yet"
- Hit `/internet/trigger` several times to seed data
- Check KV namespace has entries: **KV → blackberry-uptime-log → View**
- Verify cron trigger is set correctly

### Status always shows Offline but internet is working
- Verify the `/ping` route is deployed on the Pi and the service is running
- Test end-to-end: `curl https://onblackberryhill.com/ping` should return `ok`
- Check that the Cloudflare Tunnel is connected (Zero Trust → Networks → Tunnels)

### Route not matching / still getting 404
- Confirm route pattern is `onblackberryhill.com/internet*` (with the `*`)
- Confirm the domain's DNS record is proxied (orange cloud, not grey)
- Check Worker → Triggers → Routes shows the route

---

## Historical Note: Tunnel Status API Approach

The original implementation (prior to March 2026) did not probe connectivity at all. Instead, it polled the **Cloudflare REST API** to read the self-reported health status of the tunnel:

```
GET https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/cfd_tunnel/{CF_TUNNEL_ID}
Authorization: Bearer {CF_API_TOKEN}
```

The API returned a `status` field with one of four values: `healthy`, `degraded`, `down`, or `inactive`. The worker stored `{ ts, up, status }` and set `up = status === 'healthy'`.

This required three additional secrets in the Worker environment: `CF_API_TOKEN`, `CF_ACCOUNT_ID`, and `CF_TUNNEL_ID`.

**Why we moved away from it:**

1. **TCP keepalives can lie.** Cloudflare's tunnel status reflects whether the TCP connections between `cloudflared` and Cloudflare's edge are alive. TCP keepalives can keep a connection appearing healthy even when the actual data path is broken — there's a lag between real failure and status reflection.

2. **The probe is more honest.** By making a real HTTP request through the tunnel, we test the full path: Cloudflare edge → tunnel → `cloudflared` → Pi's Flask server → back. A stale TCP connection that can't carry data will fail this test correctly (caught by the 10-second timeout).

3. **"Degraded" was misleading.** The tunnel API reports `degraded` when some but not all of the tunnel's connections to Cloudflare's edge have failed health checks. With multiple tunnel connections, this was a real state, but it didn't map cleanly to "is the internet actually working?" The probe-based approach reduces to a clean binary: the HTTP request either succeeds or it doesn't.

4. **Fewer moving parts.** Removing the API token dependency means one less secret to rotate, and the worker no longer depends on the Cloudflare API being available and responsive.

*Last updated: March 2026*
