# TODO & Planning

Pending tasks, security items, ideas, and completed history.

---

## Pending

### Security (High Priority)

- [x] **`pumphouse/certs/privkey.pem`** — excluded from git; `certs/` added to `.gitignore`; deploy hook manages cert updates
- [x] **Remove `nohup.out` from git tracking** — added to `.gitignore`, removed from git index
- [ ] **Upgrade dashboard auth from Basic to Digest (or session-based)**
  - Current: HTTP Basic Auth (`requires_auth` decorator in `monitor/web.py`)
  - Basic over TLS is acceptable (credentials encrypted in transit), but Digest
    would hash credentials client-side before sending, removing dependence on TLS
  - Options: Flask-HTTPAuth Digest (low effort), or session cookie login (better UX, logout support)
  - README security section should be updated once changed
- [ ] **Move email addresses to secrets.conf** — personal Gmail addresses are in `monitor/config.py`
- [ ] **Move Ambient Weather dashboard URL to secrets.conf** — unique dashboard ID in `monitor/config.py`
- [ ] **Scrub git history** after moving secrets — use `git filter-repo` or BFG to remove sensitive data from past commits (without this, secrets remain accessible in git history even after removal)

### High Priority

- [ ] **Rotate Cloudflare KV API token** — token was briefly visible in chat (not in git)
  - Dashboard → My Profile → API Tokens → KV token → Roll
  - Update `~/.config/pumphouse/secrets.conf` → `sudo systemctl restart pumphouse-web`

- [ ] **Cloudflare Cache Rule for HTML pages** (Step 8 of [docs/cloudflare.md](cloudflare.md))
  - Dashboard → onblackberryhill.com → Caching → Cache Rules → Create rule
  - Match: URI path matches `/timelapse/20*`
  - Setting: Cache everything; Edge TTL: Respect origin

- [ ] **Firewall hardening after Cloudflare tunnel is verified** (deferred — 10 hrs from Pi)
  - `sudo ufw allow from 192.168.1.0/24 to any port 6443`
  - `sudo ufw deny 6443` then `sudo ufw enable`
  - Optionally remove router port-forward for 6443
  - **Verify tunnel works end-to-end before doing this**

- [ ] **Email confirmation for remote control actions**
  - Send follow-up email when user clicks Override ON/OFF, Bypass ON/OFF, or Purge buttons
  - Subject: e.g., "✓ Override Turned ON — 1320 gal"

- [ ] **Install Node.js + wrangler on Pi** for CLI worker deploys (optional)
  - See [docs/cloudflare.md](cloudflare.md) — CLI deploy section

### Medium Priority

- [ ] Every 6 hours, determine if float is really calling (turn overflow off temporarily)
- [ ] Automate vacation mode (Ecobee — blocked by web scraping limitations)
- [ ] Log estimated GPH per pump cycle
- [ ] Estimate chlorine use (Dosatron click counting)
- [ ] Matplotlib chart: don't go all the way to zero on Y-axis
- [ ] Stagnation region should restart if water drops >20 gallons (house use)
- [ ] Change color of dots again on fast-fill
- [ ] Put sensor state changes on graph (override on/off, occupied, bypass)
- [ ] Camera link in iOS should open the Amcrest app
- [ ] Investigate gpiozero as alternative to RPi.GPIO (less contention, fewer false readings)
- [ ] Roll and compress logs
- [ ] Backup private config (secrets.conf, etc.) to a private GitHub repo
- [ ] Put availability calendar on web dashboard (or more months of reservations)
- [ ] Log all data to a Google Sheet

### Low Priority

- [ ] _Add your ideas here_

---

## Completed

### 2026-02-15 — Version current
- [x] Make a widget for iPhone and desktop (Scriptable iPhone widget + e-paper display)

### 2025-12-26
- [x] GPH tracking integrated into dashboard and email

### 2025-12-21 — Version 2.10.x
- [x] One email per day (morning status)
- [x] Email notification on new reservations
- [x] Remove Repeat and Booking columns from reservations table unless authenticated
- [x] Occupancy tracking and reservation display
- [x] Visual stagnation detection on chart with color-coded dots
- [x] Consolidated stagnation logic to single 6-hour definition
- [x] Log event when backflush detected
- [x] Clarify what "Tank Filling" alert means
- [x] Change email From address
- [x] Shorten email subject lines (gallons first for mobile)

### 2025-12-20 — Version 2.10.0
- [x] Turn on override below a threshold (OVERRIDE_ON_THRESHOLD)
- [x] Fixed duplicate "Well Recovery Detected" alerts
  - Persistent notification state (notification_state.json)
  - State survives service restarts
- [x] Persistent relay state across service restarts (relay_state.json)
- [x] Safety shutoff when tank level cannot be read
  - Override auto-off if internet/PT sensor unavailable
  - Urgent notification on safety shutoff

### 2025-12-14 — Version 2.8.0
- [x] Remote control via email with secret URL tokens
- [x] Camera link in emails
- [x] Vertical table headers to save width
- [x] Auto-sizing first column in tables
- [x] Recent events in emails with filtering
- [x] Configurable max events (500 for ~7 days)
- [x] Gallons in all email subject lines
- [x] Tank threshold alerts show > or < prominently
- [x] Email header matches subject instead of generic alert
- [x] Human-friendly timestamps (Day HH:MM format)

### Security — Completed
- [x] Remove Wyze camera module (was CRITICAL — credentials were non-functional)
- [x] Move DDNS hostname to secrets.conf (PUMPHOUSE_HOST / PUMPHOUSE_PORT)
- [x] Move ntfy.sh topic name to secrets.conf
- [x] Move tank monitoring URL to secrets.conf

---

## Implementation Plans (Historical)

### Automatic Override Shutoff (implemented v2.5.0)

Added `ENABLE_OVERRIDE_SHUTOFF` and `OVERRIDE_SHUTOFF_THRESHOLD` to `monitor/config.py`.
Checks on every tank poll (60 s); uses `gpio` command to avoid Python GPIO conflicts.
Config reloaded on each check — threshold changes take effect without restart.
