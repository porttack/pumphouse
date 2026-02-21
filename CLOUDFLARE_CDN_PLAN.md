# Cloudflare CDN & Tunnel Plan for Pumphouse

Move the pumphouse web dashboard behind Cloudflare so the Raspberry Pi is
protected, cached, and never directly reachable from the internet.

---

## Goals

- Hide the Pi's IP address — nobody can reach it directly
- Absorb DDoS and bot traffic at Cloudflare's edge
- Cache timelapse MP4s and HTML pages so the Pi rarely gets hit
- Move the star rating system to Cloudflare (Worker + KV) so rating data
  lives at the edge, HTML pages become fully cacheable, and the Pi is out
  of the rating loop entirely
- No open inbound ports on the Pi or router

---

## Architecture

```
Browser
  │
  ▼
Cloudflare Edge  (onblackberryhill.com)
  │  ├── Cache: timelapse HTML pages, MP4 files, weather pages
  │  ├── Worker: handles /api/ratings/* and POST /timelapse/*/rate
  │  │     └── Cloudflare KV: stores rating count + sum per date
  │  └── Cache miss / dynamic content: tunnel to Pi
  │
  ▼
cloudflared daemon (runs on Pi, outbound tunnel — no open ports)
  │
  ▼
Flask on localhost:6443
```

---

## Step 1 — Buy the Domain

Buy **onblackberryhill.com** from Cloudflare Registrar
(cloudflare.com → Domain Registration).

Buying directly from Cloudflare means:
- No third-party nameserver transfer needed — it's already on Cloudflare
- At-cost pricing (~$10/yr for .com), no markup
- Auto-renewed, managed in the same dashboard as everything else

---

## Step 2 — Cloudflare Account Setup

Already have a Cloudflare login — just add the new domain to the same account.
If bought through Cloudflare Registrar it appears automatically.

---

## Step 3 — Cloudflare Tunnel

The tunnel replaces the current dynamic DNS setup (`tplinkdns.com`).
The Pi makes an **outbound** connection to Cloudflare — no inbound ports needed.

### Install cloudflared on the Pi

```bash
# Add Cloudflare's package repo
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
  https://pkg.cloudflare.com/cloudflared any main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt update && sudo apt install cloudflared
```

### Authenticate (needs a browser — do this on your laptop, or use API token)

**Option A — API token (Pi-friendly):**
1. Cloudflare dashboard → My Profile → API Tokens → Create Token
2. Use the "Edit Cloudflare Workers" template, add Tunnel permissions
3. On the Pi: `export CLOUDFLARE_API_TOKEN=your_token`

**Option B — Browser login:**
```bash
cloudflared tunnel login --no-browser
# Prints a URL — open it on your laptop to authorize
```

### Create and configure the tunnel

```bash
cloudflared tunnel create pumphouse
# Note the tunnel ID printed — you'll need it below

cloudflared tunnel route dns pumphouse onblackberryhill.com
# Also route any subdomains you want, e.g.:
cloudflared tunnel route dns pumphouse www.onblackberryhill.com
```

### Tunnel config file

```yaml
# /home/pi/.cloudflared/config.yml
tunnel: <your-tunnel-id>
credentials-file: /home/pi/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: onblackberryhill.com
    service: https://localhost:6443
    originRequest:
      noTLSVerify: true        # Pi's cert is for the old hostname; Cloudflare
                                # handles TLS for users, this is internal only
  - hostname: www.onblackberryhill.com
    service: https://localhost:6443
    originRequest:
      noTLSVerify: true
  - service: http_status:404
```

### Install as a system service

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

### Lock down the Pi firewall

Once the tunnel is working, block direct external access to port 6443.
The tunnel is outbound-only so it is unaffected.

```bash
# Allow LAN access (for local testing)
sudo ufw allow from 192.168.1.0/24 to any port 6443
# Block everything else
sudo ufw deny 6443
sudo ufw enable
```

You can also remove the router's port forwarding rule for 6443 entirely.

### Dynamic DNS

`tplinkdns.com` is no longer needed — the Pi's external IP doesn't matter
since all traffic goes through the tunnel. Disable or ignore it.

---

## Step 4 — Cloudflare Worker + KV for Star Ratings

The rating system moves entirely off the Pi. The Pi has no `/rate` endpoint,
no `ratings.json` file, no cookie inspection — HTML pages become fully
cacheable.

### Create KV namespace (Cloudflare dashboard on your laptop)

Workers & Pages → KV → Create namespace → name it `RATINGS`

Note the namespace ID.

### Worker script

Create this in the Cloudflare dashboard: Workers & Pages → Create Worker.
Name it `pumphouse-ratings`.

```javascript
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // GET /api/ratings/YYYY-MM-DD  →  return current stats
    const getMatch = url.pathname.match(/^\/api\/ratings\/(\d{4}-\d{2}-\d{2})$/);
    if (getMatch && request.method === 'GET') {
      return handleGet(getMatch[1], env);
    }

    // POST /timelapse/YYYY-MM-DD/rate  →  record a rating
    const postMatch = url.pathname.match(/^\/timelapse\/(\d{4}-\d{2}-\d{2})\/rate$/);
    if (postMatch && request.method === 'POST') {
      return handlePost(postMatch[1], request, env);
    }

    // Everything else passes through to the Pi (origin)
    return fetch(request);
  }
};

async function handleGet(dateStr, env) {
  const data = await readRating(dateStr, env);
  const headers = corsHeaders({
    'Content-Type': 'application/json',
    'Cache-Control': 'public, max-age=60',   // stale for 60s is fine
  });
  return new Response(JSON.stringify(data), { headers });
}

async function handlePost(dateStr, request, env) {
  // Check cookie — only allow one rating per date per user
  const cookie = request.headers.get('Cookie') || '';
  const alreadyRated = cookie.includes(`tl_rated_${dateStr}=`);
  if (alreadyRated) {
    const data = await readRating(dateStr, env);
    return new Response(JSON.stringify(data), {
      headers: corsHeaders({ 'Content-Type': 'application/json' }),
    });
  }

  let body;
  try { body = await request.json(); } catch {
    return new Response('Bad request', { status: 400 });
  }
  const rating = parseInt(body.rating);
  if (![3, 4, 5].includes(rating)) {
    return new Response('Rating must be 3, 4, or 5', { status: 400 });
  }

  // Read-modify-write (low traffic; eventual consistency is fine)
  const current = await readRating(dateStr, env);
  const updated = {
    count: current.count + 1,
    sum:   current.sum   + rating,
  };
  await env.RATINGS.put(dateStr, JSON.stringify(updated));

  const avg = updated.sum / updated.count;
  const resp = new Response(
    JSON.stringify({ count: updated.count, avg: Math.round(avg * 10) / 10 }),
    { headers: corsHeaders({ 'Content-Type': 'application/json' }) }
  );

  // Set cookie for 1 year so the user can't re-rate
  const expires = new Date(Date.now() + 365 * 24 * 3600 * 1000).toUTCString();
  resp.headers.append('Set-Cookie',
    `tl_rated_${dateStr}=${rating}; Path=/; Expires=${expires}; SameSite=Lax`);
  return resp;
}

async function readRating(dateStr, env) {
  const raw = await env.RATINGS.get(dateStr);
  return raw ? JSON.parse(raw) : { count: 0, sum: 0 };
}

function corsHeaders(extra = {}) {
  return {
    'Access-Control-Allow-Origin': '*',   // tighten to onblackberryhill.com in prod
    'Access-Control-Allow-Headers': 'Content-Type',
    ...extra,
  };
}
```

### Bind KV to the Worker (dashboard)

Worker Settings → Variables → KV Namespace Bindings:
- Variable name: `RATINGS`
- KV Namespace: select the `RATINGS` namespace created above

### Worker route (dashboard)

Workers & Pages → your Worker → Settings → Triggers → Add route:
```
onblackberryhill.com/api/ratings/*
onblackberryhill.com/timelapse/*/rate
```

---

## Step 5 — Pi Code Changes

### Remove from `monitor/web.py`

- `RATINGS_FILE` constant
- `_ratings_lock` and `_threading` import
- `_read_ratings()` and `_write_ratings()` helpers
- `timelapse_rate()` Flask route (`POST /timelapse/<date>/rate`)
- Cookie check (`request.cookies.get(f'tl_rated_{date_str}')`) from `timelapse_view`
- `rating_count`, `rating_avg`, `user_rating`, `rating_avg_js` variables
- Server-injected JS variables `userRated`, `rCount`, `rAvg` from the template

### Change in `timelapse_view` response

Add cache headers so Cloudflare knows it can cache the page:

```python
# Past dates: cache indefinitely (content never changes)
# Today: short TTL (preview MP4 updates every 10 min)
from datetime import date as _date
is_today = (date_str == _date.today().isoformat())
cache_header = 'public, max-age=600' if is_today else 'public, max-age=31536000'
return Response(html, mimetype='text/html',
                headers={'Cache-Control': cache_header})
```

```python
# MP4 serve route — cache forever (files are immutable once written)
return send_file(path, mimetype='video/mp4',
                 max_age=365*24*3600, conditional=True)
```

### Update star rating JS in the template

Replace the server-injected rating state with:
1. Cookie read in client-side JS
2. Fetch current stats from the Worker API

```javascript
(function() {
  const dateStr  = '2026-02-19';                 // injected by Flask as before
  const workerBase = 'https://onblackberryhill.com';  // same origin in prod

  // Read user's previous rating from cookie (no server needed)
  const cookieMatch = document.cookie.match(/tl_rated_2026-02-19=(\d)/);
  let userRated = cookieMatch ? parseInt(cookieMatch[1]) : null;

  // Fetch current avg/count from Worker KV
  fetch(workerBase + '/api/ratings/2026-02-19')
    .then(r => r.json())
    .then(d => {
      rCount = d.count;
      rAvg   = d.count ? Math.round(d.sum / d.count * 10) / 10 : null;
      showInfo(userRated, rCount, rAvg);
    });

  // ... rest of star widget logic unchanged ...
  // POST target changes to workerBase + '/timelapse/' + dateStr + '/rate'
})();
```

---

## Step 6 — Cloudflare Cache Rules (dashboard)

Workers & Pages → your domain → Caching → Cache Rules:

| Rule | Match | Cache Behavior |
|---|---|---|
| Timelapse MP4s | `*.mp4` | Cache everything, Edge TTL 1 year |
| Past timelapse pages | `/timelapse/20*` | Cache everything, Edge TTL 1 year |
| Camera snapshot | `/sunset` | Bypass cache |
| Rating API | `/api/ratings/*` | Cache 60s |
| Dashboard / home | `/` | Bypass cache (has live sensor data) |

---

## Summary of What Changes

| Thing | Before | After |
|---|---|---|
| Pi reachable directly | Yes (port 6443 open) | No (tunnel only) |
| Pi's IP exposed | Yes | No |
| MP4 serve load on Pi | Every request | First request only |
| HTML page caching | None (cookie-specific) | Full CDN cache |
| Rating storage | `ratings.json` on Pi SD | Cloudflare KV |
| Rating endpoint | Flask on Pi | Cloudflare Worker |
| Dynamic DNS | tplinkdns.com needed | Not needed |
| SSL cert on Pi | Let's Encrypt (public) | Can be self-signed (internal only) |
| Router port forwarding | Required | Not needed |

---

## Open Questions / Later

- Whether to password-protect the dashboard via **Cloudflare Access**
  (zero-trust auth, free for up to 50 users) instead of the current
  HTTP Basic Auth baked into Flask
- Whether to route the existing Let's Encrypt cert renewal through the
  new domain or switch to Cloudflare's origin CA cert (simpler, free,
  works with `noTLSVerify: false`)
- Subdomain structure: `onblackberryhill.com` vs `pumphouse.onblackberryhill.com`
  if the domain is ever used for other things
