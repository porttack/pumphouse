# Cloudflare CDN Setup

Serves `onblackberryhill.com` via Cloudflare CDN with a Tunnel — no open inbound ports on the Pi.

---

## Status

- ✅ Domain: **onblackberryhill.com** (Cloudflare Registrar)
- ✅ Cloudflare Tunnel installed (`cloudflared` service, tunnel name: `pumphouse`)
- ✅ KV namespace created for ratings (`RATINGS`)
- ✅ Rating Worker deployed (`pumphouse-ratings`, routes: `onblackberryhill.com/*`)
- ✅ Pi code updated (KV backend, cache headers, client-side rating widget, `/api/ratings/DATE`)
- ✅ Existing ratings.json migrated to KV
- ✅ Worker handles `/snapshot` and `/frame` (5-min CDN cache, crop=1 enforced)
- ⬜ Cache Rule for timelapse HTML pages (`/timelapse/20*`) — Step 8 — still needed
- ⬜ Cache Rule — Ignore Query String for `/timelapse/*` — Step 9 — DoS mitigation
- ⬜ Firewall hardening (deferred — do in person)

---

## Architecture

```
Public browser
  │
  ▼
Cloudflare Edge  (onblackberryhill.com)
  ├─ Redirect Rule: / → /timelapse
  ├─ Worker: GET /snapshot, /frame   →  5-min CDN cache; Pi hit ≤ once/5 min
  ├─ Worker: GET /api/ratings/*      →  read KV   (Pi not involved)
  ├─ Worker: POST /timelapse/*/rate  →  write KV  (Pi not involved)
  ├─ Cache Rule (/timelapse/20*): Cache Everything; respects origin Cache-Control
  │    today + yesterday → 5-min TTL  |  2+ days old → 1-hour TTL
  ├─ Auto-cached by extension: *.mp4 (1 yr), snapshot *.jpg (1 yr)
  └─ Everything else     →  Tunnel → Pi Flask :6443
       │
       ▼
  cloudflared daemon (outbound from Pi, no open ports)
       │
       ▼
  Flask on https://localhost:6443

Private access (you only, never published):
  https://your-hostname.tplinkdns.com:6443  ← direct Pi via router
```

**Key privacy guarantee:** The `tplinkdns.com` hostname never appears in any Cloudflare configuration. The tunnel is outbound-only (Pi → Cloudflare); no one can trace Cloudflare traffic back to the Pi's real IP or hostname.

---

## Step 1 — Install `cloudflared` on the Pi

```bash
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
  https://pkg.cloudflare.com/cloudflared any main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt update && sudo apt install -y cloudflared
cloudflared --version
```

---

## Step 2 — Create the Tunnel

**In the Cloudflare dashboard:**

1. Zero Trust → Networks → Tunnels → Create a tunnel
2. Name: `pumphouse`
3. Copy the install command token (`eyJ...` string)

**On the Pi:**

```bash
sudo cloudflared service install eyJhIjoiABC...   # paste your token
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
systemctl status cloudflared   # should say "active (running)"
```

Back in the dashboard, the tunnel should show as **Healthy**.

---

## Step 3 — Configure Public Hostnames (dashboard)

Tunnel → Edit → Public Hostname tab:

| Subdomain | Domain | Service |
|-----------|--------|---------|
| *(blank)* | onblackberryhill.com | `https://localhost:6443` |
| www | onblackberryhill.com | `https://localhost:6443` |

Under Additional application settings → TLS: enable **No TLS Verify** (Pi cert is for `tplinkdns.com`, not this domain; Cloudflare handles TLS for users).

---

## Step 4 — Redirect Rules (dashboard)

Your domain → Rules → Redirect Rules → Create rule:

**Rule 1 — Root to timelapse:**
- If: `(http.host eq "onblackberryhill.com" or http.host eq "www.onblackberryhill.com") and http.request.uri.path eq "/"`
- Then: Static redirect → `https://onblackberryhill.com/timelapse` → 301

**Rule 2 — www to apex:**
- If: `http.host eq "www.onblackberryhill.com" and http.request.uri.path ne "/"`
- Then: Dynamic redirect → `https://onblackberryhill.com${http.request.uri.path}` → 301

---

## Step 5 — Create KV Namespace for Ratings (dashboard)

Workers & Pages → KV → Create namespace → Name: `RATINGS`

Note the **Namespace ID** — needed in Steps 6 and 7.

---

## Step 6 — Deploy the Rating Worker (dashboard)

Workers & Pages → Create → Create Worker → Name: `pumphouse-ratings`

Paste the worker source from `cloudflare/ratings-worker.js` → Deploy.

**Bind KV to the Worker:**

Worker → Settings → Variables → KV Namespace Bindings → Add:
- Variable name: `RATINGS`
- KV Namespace: `RATINGS`

**Add Worker routes:**

Worker → Settings → Triggers → Add route:
```
onblackberryhill.com/*
www.onblackberryhill.com/*
```

**To redeploy after code changes:**

Option A — Dashboard paste: Worker → Edit code → paste updated `ratings-worker.js` → Deploy

Option B — CLI via `cloudflare/deploy.sh` (requires Node.js + wrangler):
```bash
cd ~/src/pumphouse
./cloudflare/deploy.sh
```

---

## Step 7 — Pi Secrets for KV Access

Create a KV API token:
Cloudflare dashboard → My Profile → API Tokens → Create Token → Permissions: `Workers KV Storage → Edit`

Add to `~/.config/pumphouse/secrets.conf`:
```ini
CLOUDFLARE_ACCOUNT_ID=your_account_id
CLOUDFLARE_KV_NAMESPACE_ID=your_namespace_id
CLOUDFLARE_KV_API_TOKEN=your_api_token
RATINGS_BACKEND=cloudflare_kv
```

Your Account ID appears in the Cloudflare dashboard URL and Workers overview page.

---

## Step 8 — Cache Rule: Timelapse HTML pages (dashboard)

Cloudflare will not cache `text/html` responses by default. This rule overrides that for the timelapse viewer pages.

Your domain → **Caching → Cache Rules → Create rule:**

- **Rule name:** Timelapse HTML pages
- **When incoming requests match:** `URI Path` starts with `/timelapse/20`
- **Cache status:** Cache everything
- **Edge TTL:** Respect origin TTL *(the Pi sends `max-age=300` for today/yesterday and `max-age=3600` for older pages — Cloudflare will honour those values)*

Leave all other settings at their defaults.

> ⬜ **Not yet created.** Without this rule, Cloudflare passes every timelapse page view through to the Pi.

MP4 and JPEG files do **not** need a Cache Rule — Cloudflare caches them automatically based on file extension and the `Cache-Control` headers the Pi sends (`max-age=31536000` for past files).

---

## Step 9 — Cache Rule: Ignore Query String (DoS mitigation)

Without this rule, a script can bypass Cloudflare's cache by appending random query parameters (`?x=1`, `?x=2`, …), forcing the Pi to serve a fresh response — including potentially streaming a 4 MB MP4 — on every request.

Your domain → **Caching → Cache Rules → Create rule:**

- **Rule name:** Ignore query string — timelapse
- **When incoming requests match:** `URI Path` starts with `/timelapse/`
- **Cache status:** Cache everything
- **Cache key → Query string:** Ignore all

This makes `/timelapse/2026-01-01_1750.mp4?x=1` and `?x=2` resolve to the same cache entry. Timelapse pages and MP4s don't use query parameters for content, so ignoring them is safe.

> ⬜ **Not yet created.**

**Optional belt-and-suspenders:** Cloudflare dashboard → Security → WAF → Rate Limiting Rules → limit any single IP to 60 requests/minute to the origin.

---

## Step 10 — Firewall Hardening (do in person)

After verifying the tunnel works end-to-end:

```bash
# Allow LAN access
sudo ufw allow from 192.168.1.0/24 to any port 6443
# Block external direct access to port 6443
sudo ufw deny 6443
sudo ufw enable
sudo ufw status
```

You can also remove the router's port forward for 6443 entirely — the tunnel is unaffected. The `tplinkdns.com` address will only work on the LAN after this.

**Verify tunnel works first:**
```bash
# From any browser: https://onblackberryhill.com/timelapse
# Rate a sunset — confirm rating appears
curl -k https://localhost:6443/timelapse
```

---

## URL Reference

| URL | Who Uses It | Via |
|-----|-------------|-----|
| `https://onblackberryhill.com/timelapse` | Public | Cloudflare CDN → Tunnel → Pi |
| `https://www.onblackberryhill.com` | Public | Redirects to apex |
| `https://your-hostname.tplinkdns.com:6443` | You only (private) | Router → Pi direct |

---

## Fallback: Disable CDN

If you want to abandon the CDN without losing ratings:

1. Set `RATINGS_BACKEND=local` in secrets.conf
2. Stop cloudflared: `sudo systemctl stop cloudflared`
3. Re-open router port forward for 6443
4. Share the `tplinkdns.com:6443` URL directly

All Pi code works identically in both modes.

---

## Node.js + Wrangler (Optional)

For CLI-based Worker deploys from the Pi:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs && sudo npm install -g wrangler
cd ~/src/pumphouse && ./cloudflare/deploy.sh
```
