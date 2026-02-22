# Cloudflare Setup Guide: onblackberryhill.com

Move the sunset timelapse viewer behind Cloudflare CDN so the Pi is never
directly reachable from the public internet.

---

## Status

- ✅ Domain purchased: **onblackberryhill.com** (Cloudflare Registrar)
- ✅ Cloudflare Tunnel installed on Pi (`cloudflared` service, tunnel name: `pumphouse`)
- ✅ KV namespace created for ratings (namespace: `RATINGS`)
- ✅ Rating Worker deployed (`pumphouse-ratings`, routes: `onblackberryhill.com/*`)
- ✅ Pi code updated (KV backend, cache headers, client-side rating widget, `/api/ratings/DATE`)
- ✅ Existing ratings.json migrated to KV
- ⬜ Cache rules configured (Step 8 — one "Cache Everything" rule for `/timelapse/20*` still needed)
- ⬜ Firewall hardened (deferred — do in person; see TODO.md)

---

## Architecture

```
Public browser
  │
  ▼
Cloudflare Edge  (onblackberryhill.com)
  ├─ Redirect Rule: / → /timelapse
  ├─ Cache: MP4s (1 yr), past HTML pages (1 yr), snapshots (1 yr)
  ├─ Worker: POST /timelapse/*/rate  →  write KV  (Pi not involved)
  ├─ Worker: GET  /api/ratings/*     →  read KV   (Pi not involved)
  └─ Cache miss / other routes       →  Tunnel → Pi Flask :6443
       │
       ▼
  cloudflared daemon (outbound from Pi, no open ports)
       │
       ▼
  Flask on https://localhost:6443

Private browser (you only, never published)
  │
  ▼
https://onblackberryhill2.tplinkdns.com:6443   ← direct Pi access
  │
  ▼
Flask on https://localhost:6443
  │  (uses same Cloudflare KV for ratings, via KV API)
  └─ ratings read/write → Cloudflare KV API
```

**Key privacy guarantee:** `onblackberryhill2.tplinkdns.com` never appears in
any Cloudflare config. The tunnel is outbound-only (Pi → Cloudflare); nobody
can trace Cloudflare traffic back to the Pi's real IP or hostname.

---

## Step 1 — Install `cloudflared` on the Pi

```bash
# Add Cloudflare package repo
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
  https://pkg.cloudflare.com/cloudflared any main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt update && sudo apt install -y cloudflared
cloudflared --version   # confirm install
```

---

## Step 2 — Create the Tunnel (browser, then Pi)

**In the Cloudflare dashboard (browser/laptop):**

1. Go to **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel**
2. Choose **Cloudflared** connector
3. Name it: `pumphouse`
4. Click **Next** — the dashboard shows an install command like:
   ```
   cloudflared service install eyJhIjoiABC...
   ```
5. **Copy the token** from that command (the long `eyJ...` string)

**On the Pi:**

```bash
# Install as a system service using the token from the dashboard
sudo cloudflared service install eyJhIjoiABC...   # paste your token

sudo systemctl enable cloudflared
sudo systemctl start cloudflared
systemctl status cloudflared   # should say "active (running)"
```

**Back in the dashboard:**
- The tunnel should now show as **Healthy** with a green dot
- Click **Next** to configure the Public Hostname

---

## Step 3 — Configure Public Hostnames (dashboard)

Still in the tunnel setup wizard (or **Edit** → **Public Hostname** tab later):

| Subdomain | Domain | Path | Service |
|-----------|--------|------|---------|
| *(blank)* | onblackberryhill.com | | `https://localhost:6443` |
| www | onblackberryhill.com | | `https://localhost:6443` |

Under **Additional application settings → TLS**:
- Enable **No TLS Verify** (the Pi's cert is for `tplinkdns.com`, not this domain;
  Cloudflare handles TLS for users — this tunnel leg is internal only)

Click **Save tunnel**.

This automatically creates CNAME DNS records for both hostnames pointing to the
tunnel. No manual DNS changes needed.

---

## Step 4 — Root Domain Redirect to /timelapse (dashboard)

`onblackberryhill.com/` and `www.onblackberryhill.com/` should both go to
`/timelapse` without hitting the Pi.

**Cloudflare dashboard → your domain → Rules → Redirect Rules → Create rule:**

Rule 1 — Root redirect:
- **Name:** Root to timelapse
- **If:** `(http.host eq "onblackberryhill.com" or http.host eq "www.onblackberryhill.com") and http.request.uri.path eq "/"`
- **Then:** Static redirect → `https://onblackberryhill.com/timelapse` → 301

Rule 2 — www to apex (canonical):
- **Name:** www to apex
- **If:** `http.host eq "www.onblackberryhill.com" and http.request.uri.path ne "/"`
- **Then:** Dynamic redirect → `https://onblackberryhill.com${http.request.uri.path}` → 301

> Alternatively, add `@app.route('/')` → `redirect('/timelapse')` in Flask for
> the tplinkdns direct-access path (see Step 7).

---

## Step 5 — Create KV Namespace for Ratings (dashboard)

Ratings are stored in Cloudflare KV — the Pi reads and writes KV directly via
the API, so ratings are consistent whether accessed via CDN or tplinkdns.

**Cloudflare dashboard → Workers & Pages → KV → Create namespace:**
- Name: `RATINGS`
- Note the **Namespace ID** — you'll need it in Steps 6 and 7

---

## Step 6 — Deploy the Rating Worker (dashboard)

**Cloudflare dashboard → Workers & Pages → Create → Create Worker:**
- Name: `pumphouse-ratings`
- Paste the script below → **Deploy**

```javascript
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Redirect root to /timelapse (belt-and-suspenders; redirect rule handles it too)
    if (url.pathname === '/') {
      return Response.redirect(url.origin + '/timelapse', 301);
    }

    // GET /api/ratings/YYYY-MM-DD  →  read from KV
    const getMatch = url.pathname.match(/^\/api\/ratings\/(\d{4}-\d{2}-\d{2})$/);
    if (getMatch && request.method === 'GET') {
      return handleGet(getMatch[1], env);
    }

    // POST /timelapse/YYYY-MM-DD/rate  →  write to KV (Pi not involved)
    const postMatch = url.pathname.match(/^\/timelapse\/(\d{4}-\d{2}-\d{2})\/rate$/);
    if (postMatch && request.method === 'POST') {
      return handlePost(postMatch[1], request, env);
    }

    // Everything else passes through to Pi via tunnel
    return fetch(request);
  }
};

async function handleGet(dateStr, env) {
  const data = await readRating(dateStr, env);
  return new Response(JSON.stringify(data), {
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'public, max-age=60',
      'Access-Control-Allow-Origin': 'https://onblackberryhill.com',
    },
  });
}

async function handlePost(dateStr, request, env) {
  const cookie = request.headers.get('Cookie') || '';
  if (cookie.includes(`tl_rated_${dateStr}=`)) {
    // Already rated — return current stats without writing
    return new Response(JSON.stringify(await readRating(dateStr, env)), {
      headers: { 'Content-Type': 'application/json' },
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

  const current = await readRating(dateStr, env);
  const updated = { count: current.count + 1, sum: current.sum + rating };
  await env.RATINGS.put(dateStr, JSON.stringify(updated));

  const avg = Math.round(updated.sum / updated.count * 10) / 10;
  const resp = new Response(
    JSON.stringify({ count: updated.count, avg }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  const expires = new Date(Date.now() + 365 * 24 * 3600 * 1000).toUTCString();
  resp.headers.append('Set-Cookie',
    `tl_rated_${dateStr}=${rating}; Path=/; Expires=${expires}; SameSite=Lax`);
  return resp;
}

async function readRating(dateStr, env) {
  const raw = await env.RATINGS.get(dateStr);
  return raw ? JSON.parse(raw) : { count: 0, sum: 0 };
}
```

**Bind KV to the Worker:**

Worker page → **Settings** → **Variables** → **KV Namespace Bindings** → Add:
- Variable name: `RATINGS`
- KV Namespace: `RATINGS` (the one created in Step 5)

**Add Worker routes:**

Worker page → **Settings** → **Triggers** → **Add route**:
```
onblackberryhill.com/*
www.onblackberryhill.com/*
```
Zone: `onblackberryhill.com`

> This routes ALL traffic through the Worker. Non-rating requests fall through
> to `fetch(request)` which hits the Pi via the tunnel.

**To redeploy the Worker after code changes:**

The worker source lives in `cloudflare/ratings-worker.js`. Two ways to deploy:

Option A — Dashboard paste (no extra tools needed):
1. Worker page → **Edit code** → paste updated `ratings-worker.js` → **Deploy**

Option B — CLI via `cloudflare/deploy.sh` (requires Node.js + wrangler):
```bash
# One-time setup (if not done):
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs && sudo npm install -g wrangler

# Deploy (reads credentials from secrets.conf automatically):
cd /home/pi/src/pumphouse
./cloudflare/deploy.sh
```
`deploy.sh` reads `CLOUDFLARE_KV_NAMESPACE_ID` and `CLOUDFLARE_KV_API_TOKEN`
from `~/.config/pumphouse/secrets.conf` — nothing sensitive is hardcoded or
committed to git.

---

## Step 7 — Pi Code Changes

These changes make the Pi use Cloudflare KV for ratings (even on direct
tplinkdns access), add a root redirect, and add proper cache headers.

### 7a. Create a KV API token (browser)

**Cloudflare dashboard → My Profile → API Tokens → Create Token:**
- Template: **Edit Cloudflare Workers** (or Custom)
- Permissions: `Account → Workers KV Storage → Edit`
- Account resources: your account
- **Save** — copy the token (shown once)

### 7b. Add to Pi secrets

```bash
# Edit ~/.config/pumphouse/secrets.conf and add:
CLOUDFLARE_ACCOUNT_ID=your_account_id        # dashboard URL: /.../<account_id>/...
CLOUDFLARE_KV_NAMESPACE_ID=your_namespace_id  # from Step 5
CLOUDFLARE_KV_API_TOKEN=your_api_token        # from Step 7a
RATINGS_BACKEND=cloudflare_kv                 # set to 'local' to fall back to ratings.json
```

Your Account ID appears in the Cloudflare dashboard URL and on the Workers
overview page (right sidebar).

### 7c. Pi code changes (`monitor/web.py`)

The following changes are needed in the Flask app:

1. **Root route** — redirect `/` to `/timelapse` for tplinkdns direct access
2. **KV-backed ratings** — `_read_ratings()` and `_write_ratings()` use KV API
   when `RATINGS_BACKEND=cloudflare_kv`, fallback to `ratings.json` when `local`
3. **Client-side rating widget** — remove server-injected `userRated`/`rCount`/`rAvg`;
   the JS widget fetches `/api/ratings/DATE` on load and reads cookie client-side
   so HTML pages are fully cacheable (no per-user dynamic content)
4. **Cache headers** — past dates get `Cache-Control: public, max-age=31536000`;
   today gets `public, max-age=600` (preview updates every 40 min)
5. **`GET /api/ratings/DATE`** — new route the JS widget fetches; reads from KV
   (or local file if `RATINGS_BACKEND=local`)

> These Pi code changes will be implemented in a follow-up coding session once
> the tunnel and Worker are verified working.

### 7d. Migrate existing ratings.json to KV

After the Worker is deployed and the KV namespace exists:

```bash
# On the Pi — one-time migration script
python3 - <<'EOF'
import json, urllib.request, os

account_id   = "YOUR_ACCOUNT_ID"
namespace_id = "YOUR_NAMESPACE_ID"
api_token    = "YOUR_API_TOKEN"

with open('/home/pi/timelapses/ratings.json') as f:
    ratings = json.load(f)

for date_str, data in ratings.items():
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
           f"/storage/kv/namespaces/{namespace_id}/values/{date_str}")
    req = urllib.request.Request(url,
        data=json.dumps(data).encode(),
        method='PUT',
        headers={
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json',
        })
    with urllib.request.urlopen(req) as r:
        print(f"{date_str}: {r.status}")
EOF
```

---

## Step 8 — Cache Rules (dashboard)

**Cloudflare dashboard → your domain → Caching → Cache Rules → Create rule:**

| Rule name | Match | Cache behavior |
|-----------|-------|----------------|
| Timelapse MP4s | URI path matches `*.mp4` | Cache everything; Edge TTL 1 year |
| Past timelapse pages | URI path matches `/timelapse/20*` | Cache everything; Edge TTL 1 year |
| Snapshot JPEGs | URI path matches `/timelapse/*/snapshot` | Cache everything; Edge TTL 1 year |
| Rating API | URI path matches `/api/ratings/*` | Cache 60 seconds |
| Live frame | URI path matches `/frame*` | Bypass cache |
| Dashboard / sensor | URI path matches `/` | Bypass cache |

> Past timelapse pages are safe to cache for 1 year because content never
> changes after a day is complete. The rating widget is now fully client-side
> (async fetch) so cached HTML is always correct.

---

## Step 9 — Verify, then Harden (Pi firewall)

**Verify the tunnel works first:**
```bash
# From any browser, confirm these work:
# https://onblackberryhill.com/timelapse
# https://www.onblackberryhill.com/  (should redirect to /timelapse)
# https://onblackberryhill.com/timelapse/YYYY-MM-DD  (check weather, stars)
# Rate a sunset — confirm rating appears from KV

# From Pi, confirm direct access still works:
curl -k https://localhost:6443/timelapse
```

**Then harden the firewall:**
```bash
# Allow LAN (for local testing)
sudo ufw allow from 192.168.1.0/24 to any port 6443
# Block all external direct access to port 6443
sudo ufw deny 6443
sudo ufw enable
sudo ufw status
```

You can also **remove the router's port forward for 6443** entirely — the tunnel
is outbound-only so it's unaffected. The tplinkdns address will no longer work
externally (which is fine — you only use it on your local network).

> **If you want tplinkdns to keep working from outside your LAN**, skip the
> router port-forward removal and the `ufw deny 6443`. The Pi's existing
> Let's Encrypt cert and basic auth still protect it.

---

## Fallback: Disable CDN

If you ever want to abandon the CDN (without losing ratings):

1. Set `RATINGS_BACKEND=local` in secrets.conf
2. Run the migration script in reverse (KV → ratings.json)
3. Stop cloudflared: `sudo systemctl stop cloudflared`
4. Re-open router port forward for 6443
5. Share `tplinkdns.com:6443` URL directly

All Pi code is designed to work identically in both modes.

---

## What Each Address Does

| URL | Who uses it | Via |
|-----|-------------|-----|
| `https://onblackberryhill.com/timelapse` | Public | Cloudflare CDN → Tunnel → Pi |
| `https://www.onblackberryhill.com` | Public | Redirects to apex |
| `https://onblackberryhill2.tplinkdns.com:6443` | You only (private) | Router → Pi direct |

The tplinkdns hostname **never appears** in any Cloudflare configuration.

---

## Open Questions / Later

- **Cloudflare Access** (Zero Trust auth): could protect the whole site or
  just the pumphouse sensor dashboard from anonymous access — free for ≤50 users
- **Origin CA cert**: replace the Pi's Let's Encrypt cert with a Cloudflare
  Origin CA cert (free, works only behind Cloudflare, no renewal needed)
- **Analytics**: Cloudflare free tier includes basic visit analytics —
  useful once the site is public
- **Email routing**: onblackberryhill@gmail.com is registered; Cloudflare can
  route `*@onblackberryhill.com` to it if you ever want a custom email address
