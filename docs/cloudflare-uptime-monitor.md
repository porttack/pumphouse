# Internet Uptime Monitor

A Cloudflare Worker that polls your Cloudflare Tunnel status every minute, stores history in KV, and serves a visual uptime dashboard at `onblackberryhill.com/internet`.

## How It Works

```
Every 1 minute (Cron Trigger):
  Worker calls Cloudflare REST API → checks tunnel status (healthy/down/degraded)
  Writes { timestamp, up, status } entry to Workers KV (7-day TTL)

On request to onblackberryhill.com/internet:
  Worker reads all KV entries (paginated)
  Renders HTML dashboard with three timeline graphs + uptime percentages
```

The tunnel status acts as an internet connectivity proxy — if the Pi at Blackberry Hill loses internet, the cloudflared tunnel drops, and Cloudflare records it as `down`.

## Dashboard Features

- **Current status** — Online / Offline with last-checked time (Pacific time)
- **Three timeline graphs** — Past 4 hours, 24 hours, and 7 days
- **Color coding** — Green = up, amber = degraded (>50% but <90%), red = down, dark = no data
- **Uptime percentage + downtime duration** — e.g. `97.3% uptime — 1h 17m down`
- **Hover tooltips on red segments** — shows exact outage window e.g. `2:14 AM – 3:31 AM`
- **Auto-refreshes** every 60 seconds
- **Raw JSON** available at `/internet.json`
- **Manual trigger** at `/internet/trigger` for testing

---

## Setup

### Prerequisites

- Domain (`onblackberryhill.com`) managed by Cloudflare (orange cloud proxied)
- A running Cloudflare Tunnel (cloudflared) connecting the Raspberry Pi
- A Cloudflare account with Workers and KV available (free tier is sufficient)

---

### Step 1 — Create Two KV Namespaces

In the Cloudflare dashboard: **Workers & Pages → KV → Create namespace**

Create two namespaces and note their IDs:

| Purpose | Suggested Name |
|---|---|
| Per-minute log entries | `blackberry-uptime-log` |
| Latest status cache | `blackberry-uptime-current` |

---

### Step 2 — Create an API Token

**My Profile (top right) → API Tokens → Create Token → Create Custom Token**

| Field | Value |
|---|---|
| Permission | `Account` → `Cloudflare Tunnel` → `Read` |
| Account Resources | Your account |

Copy the token — you only see it once.

---

### Step 3 — Find Your IDs

| Value | Where to find it |
|---|---|
| `CF_ACCOUNT_ID` | Workers & Pages → Overview (right sidebar), or URL of any account page |
| `CF_TUNNEL_ID` | Zero Trust → Networks → Connectors → Cloudflare Tunnels → click your tunnel → UUID in URL |
| `CF_API_TOKEN` | From Step 2 above |

---

### Step 4 — Create the Worker

**Workers & Pages → Create → Worker**

Name it `blackberry-uptime`, dismiss the Hello World stub, and paste the full worker code (see `internet-uptime-worker.js` in this repo).

---

### Step 5 — Bind KV Namespaces

Worker → **Settings → Variables → KV Namespace Bindings → Add binding**

| Variable Name | KV Namespace |
|---|---|
| `UPTIME_LOG` | `blackberry-uptime-log` |
| `UPTIME_CURRENT` | `blackberry-uptime-current` |

---

### Step 6 — Add Secrets

Worker → **Settings → Variables → Environment Variables → Add variable**

Add each as type **Secret**:

| Secret Name | Value |
|---|---|
| `CF_API_TOKEN` | API token from Step 2 |
| `CF_ACCOUNT_ID` | Your account ID |
| `CF_TUNNEL_ID` | Your tunnel UUID |

---

### Step 7 — Add the Cron Trigger

Worker → **Triggers → Cron Triggers → Add Cron Trigger**

Enter: `* * * * *` (every minute)

---

### Step 8 — Add the Route

Worker → **Triggers → Routes → Add Route**

| Field | Value |
|---|---|
| Route | `onblackberryhill.com/internet*` |
| Zone | `onblackberryhill.com` |

The trailing `*` wildcard is required so that `/internet.json` and `/internet/trigger` also match.

---

### Step 9 — Test

Visit `https://onblackberryhill.com/internet/trigger` a handful of times in your browser to seed initial data points into KV. Then visit `https://onblackberryhill.com/internet` — you should see the dashboard with your first data. The cron will fill in data every minute from this point.

To verify data is landing in KV: **Workers & Pages → KV → blackberry-uptime-log → View** — you should see `log:2026-...` keys appearing.

---

## Architecture Notes

### KV Storage

Each entry is stored as:
```json
{ "ts": "2026-03-13T05:00:00.000Z", "up": true, "status": "healthy" }
```

Key format: `log:2026-03-13T05:00:00.000Z` — ISO timestamps sort lexicographically, so KV list order is chronological.

TTL is set to 7 days on every entry, so old data expires automatically. No cleanup needed.

### KV Pagination

Cloudflare KV `list()` has a hard limit of 1000 keys per call. At 1 entry/minute, 7 days = 10,080 entries. The worker uses cursor-based pagination to fetch all pages:

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

### Timeline Graphs

Each graph divides its time window into fixed buckets:

| Graph | Buckets | Bucket size |
|---|---|---|
| 4 hours | 240 | 1 minute |
| 24 hours | 288 | 5 minutes |
| 7 days | 336 | 30 minutes |

Each bucket is colored based on the ratio of up-entries within it:
- ≥ 90% up → green
- 50–89% up → amber (degraded)
- < 50% up → red (down)
- No entries → dark grey (no data)

### Hover Tooltips

Tooltips are pure CSS using `::after` pseudo-elements with `data-tip` attributes — no JavaScript required. Each red/amber bar carries a `data-tip` with the start–end time of that bucket in Pacific time.

### Timezone

All displayed times use `America/Los_Angeles`, which automatically handles PST/PDT transitions.

---

## Cloudflare Free Tier Usage

| Resource | Usage | Free Limit |
|---|---|---|
| Worker requests | ~1,500/day (cron) + dashboard views | 100,000/day |
| KV writes | ~1,440/day | 1,000/day **(see note)** |
| KV reads | ~10,080 per dashboard load | 10,000,000/day |
| KV storage | ~10,080 entries × ~100 bytes ≈ 1 MB | 1 GB |

> **Note:** Cloudflare's free KV tier allows 1,000 writes/day. At 1,440 writes/day (one per minute), you will exceed the free limit. You have two options:
> - **Upgrade to Workers Paid ($5/month)** — includes 1M KV writes/day
> - **Reduce cron frequency** — change `* * * * *` to `*/2 * * * *` (every 2 minutes) to stay under 720 writes/day

---

## Files

| File | Description |
|---|---|
| `internet-uptime-monitor.md` | This document |
| `internet-uptime-worker.js` | Full Cloudflare Worker source code |

---

## Troubleshooting

### Error 1101 — Worker threw exception
Check **Workers & Pages → your worker → Logs → Begin log stream**, then reload the page. Common causes:
- KV namespace bindings not added (check Settings → Variables)
- Secrets missing or mis-named
- KV list limit exceeded (should be fixed by pagination)

### Dashboard shows "No data yet"
- Hit `/internet/trigger` several times to seed data
- Check KV namespace has entries: **KV → blackberry-uptime-log → View**
- Verify cron trigger is set to `* * * * *`

### Status always shows "unknown"
- Verify `CF_TUNNEL_ID` is the correct UUID for your tunnel
- Verify `CF_API_TOKEN` has Cloudflare Tunnel: Read permission
- Test the API directly: `curl https://api.cloudflare.com/client/v4/accounts/YOUR_ACCOUNT_ID/cfd_tunnel/YOUR_TUNNEL_ID -H "Authorization: Bearer YOUR_TOKEN"`

### Route not matching / still getting 404
- Confirm route pattern is `onblackberryhill.com/internet*` (with the `*`)
- Confirm the domain's DNS record is proxied (orange cloud, not grey)
- Check Worker → Triggers → Routes shows the route

---

*Last updated: March 2026*