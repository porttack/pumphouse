# Cloudflare CDN Setup — Conversation Log

Session date: 2026-02-22. Continuing from the timelapse development session.
Domain `onblackberryhill.com` had just been purchased via Cloudflare Registrar.

---

## Session Summary

Completed the full Cloudflare CDN setup for `onblackberryhill.com`, moving the
public-facing timelapse viewer behind Cloudflare (tunnel + Worker + KV), while
keeping private direct access via `tplinkdns.com:6443` unchanged.

---

## Cloudflare Dashboard Work (Steps 1–6)

### Tunnel setup (Steps 1–3)
- Installed `cloudflared` on the Pi via Cloudflare package repo
- Created tunnel `pumphouse` in Zero Trust → Networks → Tunnels
- Configured public hostnames for both apex and www (both → `https://localhost:6443`,
  TLS verify disabled — Pi cert is for tplinkdns, not this domain)
- DNS CNAME records were auto-created by the tunnel setup

### Redirect rules (Step 4)
- **Q: needed `concat()` syntax for dynamic redirect** — user had tried
  `${http.request.uri.path}` (JS template literal) which is not valid in
  Cloudflare's Ruleset Engine expression language.
  **Fix:** Dynamic redirect URL expression is `concat("https://onblackberryhill.com", http.request.uri.path)`
- **Decided to skip www→apex redirect** — both hostnames are tunnel public
  hostnames and work fine; canonicalization not worth the complexity for a
  personal site.
- **Only Rule 1 created:** Root redirect:
  - Match: `(http.host eq "onblackberryhill.com" or http.host eq "www.onblackberryhill.com") and http.request.uri.path eq "/"`
  - Static → `https://onblackberryhill.com/timelapse` → 301
- **HTTP→HTTPS template rule**: not needed — Cloudflare's "Always Use HTTPS"
  (on by default) handles it. Redundant template deleted.

### KV namespace (Step 5)
- Created namespace `RATINGS` in Workers & Pages → KV
- Namespace ID noted for secrets.conf and wrangler.toml

### Worker deployment (Step 6)
- Worker script committed to repo at `cloudflare/ratings-worker.js`
- `cloudflare/wrangler.toml` created for future CLI deploys
- Deployed via dashboard paste (GitHub integration skipped — overkill for
  rarely-changed worker)
- KV binding added under Worker → Bindings tab (not Settings/Variables)
- Routes added: `onblackberryhill.com/*` and `www.onblackberryhill.com/*`
- Failure mode: **Fail closed** (protects Pi if free tier 100k/day limit hit)

### Worker bug fix
- `handleGet` was returning raw KV data `{count, sum}` instead of computing avg.
  JS received `avg: undefined`. Fixed `handleGet` to compute and return
  `{count, avg}` (matching the Pi's `/api/ratings/DATE` response format).

---

## KV Connectivity Test (Step 7a/b)

Created `cloudflare/test_kv.py` — PUT/GET/DELETE a test key to verify credentials.

```
Account ID:   ed470c2946465a5af270f2695e20e205
Namespace ID: 66127f44c97b49b1b6194e20ed375d3e
KV connectivity OK.
```

Secrets added to `~/.config/pumphouse/secrets.conf`:
```
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_KV_NAMESPACE_ID=...
CLOUDFLARE_KV_API_TOKEN=...   # NOTE: rotate this token — it was briefly visible in chat
RATINGS_BACKEND=cloudflare_kv
```

---

## Ratings Migration (Step 7d)

Migrated `timelapses/ratings.json` → KV. One entry: `2026-02-19: {count:1, sum:5}`.

---

## Flask Code Changes (Step 7c)

All changes in `monitor/web.py`:

### KV helpers added (after ratings lock, ~line 1182)
- `_load_cf_config()` — reads secrets.conf for CF credentials
- `_CF_CONFIG`, `_RATINGS_BACKEND` — module-level config
- `_kv_write_rating(date_str, entry)` — PUT single key to KV API
- `_kv_read_rating(date_str)` — GET single key from KV API (404 → `{count:0, sum:0}`)

### New route: `GET /api/ratings/<date_str>`
Reads from KV (if `RATINGS_BACKEND=cloudflare_kv`) or local file. Returns
`{"count": N, "avg": X.X}`. Used by the client-side rating widget and served
directly by the Worker for CDN requests.

### Updated `timelapse_rate` POST route
After writing to local file, also writes to KV when backend is `cloudflare_kv`.
This ensures ratings submitted via tplinkdns direct access also land in KV.

### Client-side rating widget
Removed server-injected `user_rating`, `rating_count`, `rating_avg` from
`timelapse_view()` — these made HTML pages non-cacheable (per-user content).

Widget now:
1. Reads `tl_rated_DATE` cookie client-side via `getCookie()`
2. Shows "You rated X★" immediately if cookie present; freezes stars
3. Fetches `/api/ratings/DATE` (→ Worker → KV for CDN; → Pi for tplinkdns)
4. Updates display with live count/avg

Cookie-based deduplication still works:
- CDN path: Worker checks cookie server-side on POST
- tplinkdns path: JS freezes stars client-side if cookie present

### Rating display format
Changed from plain text `"Avg 5★ (1)"` to HTML matching the list style:
`5.0 ★★★★★ (1)` with `.ls.lit` gold stars. Uses `innerHTML` instead of
`textContent`.

### Cache headers
- **Past HTML pages** (`date_str < today`): `Cache-Control: public, max-age=31536000, immutable`
- **Today's HTML page**: `Cache-Control: public, max-age=600, must-revalidate`
- **Past MP4 files**: `max_age=31536000` via `send_file()`
- **Today's MP4**: `max_age=600`
- **`/api/ratings/DATE`**: `Cache-Control: public, max-age=60`

### Step 8 (cache rules) — approach
Flask `Cache-Control` headers are honored by Cloudflare for binary assets
(MP4, JPEG) automatically. For **HTML pages**, Cloudflare doesn't cache HTML
by default — one Cloudflare Cache Rule is still needed:
- "Cache Everything" for URI path matching `/timelapse/20*`
- TTL comes from Flask's `Cache-Control` header (Cloudflare respects it)

---

## Wrangler CLI (optional, not yet used)

Node.js not yet installed on Pi. To install for future wrangler deploys:
```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g wrangler
wrangler login   # open URL on laptop browser; or use CLOUDFLARE_API_TOKEN env var
cd /home/pi/src/pumphouse/cloudflare
wrangler deploy
```

---

## Firewall Hardening (Step 9 — deferred)

Deferred until next in-person visit (10 hours away). Added to TODO.md:
```bash
sudo ufw allow from 192.168.1.0/24 to any port 6443
sudo ufw deny 6443
sudo ufw enable
```
Verify tunnel works end-to-end before doing this.

---

## Architecture as Deployed

```
Public browser
  │
  ▼
Cloudflare Edge  (onblackberryhill.com)
  ├─ Redirect Rule: / → /timelapse (301)
  ├─ Worker: GET  /api/ratings/DATE  → read KV  (Pi not involved)
  ├─ Worker: POST /timelapse/DATE/rate → write KV (Pi not involved)
  └─ Everything else → Tunnel → Pi Flask :6443
       │
       ▼
  cloudflared daemon (outbound from Pi, no open ports)
       │
       ▼
  Flask on https://localhost:6443

Private browser (owner only)
  │
  ▼
https://onblackberryhill2.tplinkdns.com:6443  ← direct Pi access
  └─ Ratings read/write → Pi → Cloudflare KV API
```

---

## Key Files

| File | Purpose |
|------|---------|
| `cloudflare/ratings-worker.js` | Worker: edge ratings + pass-through |
| `cloudflare/wrangler.toml` | Wrangler deploy config (fill in KV namespace ID) |
| `cloudflare/test_kv.py` | KV connectivity test script |
| `CLOUDFLARE_CDN_PLAN.md` | Step-by-step setup guide (updated) |
| `monitor/web.py` | Flask: KV helpers, `/api/ratings/DATE`, cache headers |
| `~/.config/pumphouse/secrets.conf` | CF account ID, namespace ID, API token |

## Git History (this session)
```
(pending commit)  Cloudflare CDN live: tunnel + Worker + KV ratings + cache headers
2bce2e5           CLOUDFLARE_CDN_PLAN: full step-by-step setup guide
9b240b7           Persist speed/pause in localStorage; 1/2/4/8 speed shortcuts
```

## Open Items
- [ ] Rotate CLOUDFLARE_KV_API_TOKEN (was briefly visible in chat)
- [ ] Add Cloudflare Cache Rule: "Cache Everything" for `/timelapse/20*` (Step 8)
- [ ] Install Node.js + wrangler on Pi for CLI deploys
- [ ] Firewall hardening (Step 9) — do in person
