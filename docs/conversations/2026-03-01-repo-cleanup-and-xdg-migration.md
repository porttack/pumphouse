# Repo Cleanup and XDG Data Migration — 2026-03-01

Summary of several related refactoring sessions that cleaned up the repository
structure and moved application data to the XDG standard location.

---

## 1. Cloudflare Terraform Infrastructure

Created `terraform/cloudflare/` to manage the public-facing Cloudflare
infrastructure as code. Previously all setup was manual (dashboard clicks).

**What Terraform now manages:**
- Zero Trust Tunnel (`pumphouse`) + ingress rules — `tunnel.tf`
- Apex and www CNAME records → tunnel — `tunnel.tf`
- `RATINGS` KV namespace + `pumphouse-ratings` Worker — `worker.tf`
- Worker routes (apex + www) — `worker.tf`
- Redirect rules (/ → /timelapse, www → apex) — `redirects.tf`
- Cache rules (HTML caching, Ignore Query String DoS mitigation) — `cache.tf`

**Key decisions:**
- `cloudflare/ratings-worker.js` stays in `cloudflare/`; `worker.tf` references
  it with a relative path (`../../cloudflare/ratings-worker.js`). No duplication.
- `rules.tf` was subsequently split into `redirects.tf` and `cache.tf` for clarity.
- Terraform state (`terraform.tfstate`) is gitignored — contains sensitive tunnel
  token. Keep it safe; if lost, rebuild with `terraform import`.

---

## 2. Repository Root Cleanup

Removed a large amount of clutter from the project root across several commits.

**Deleted:**
- 14 duplicate root-level `.md` files whose content had moved to `docs/`
  (`CLOUDFLARE_CDN_PLAN.md`, `ECOBEE_SETUP.md`, `EMAIL_SETUP.md`, etc.)
- Junk/test files: `check.py`, `snapshots.bak`, `test_ambient_weather.py`,
  `update_monitor_part1.sh`, `overview.md`
- `pressure/` directory (old scratch files: `change1.txt`, `changes2.bak`,
  `pressure_log.txt`)
- `cert.pem` and `key.pem` from root — these were accidentally committed
  self-signed certs. Active certs are in `certs/` (Let's Encrypt, gitignored).
  Added `cert.pem`, `key.pem`, `*.pem` to `.gitignore`.
- Test scripts (`test_ifttt_ecobee.py`, `test_tank_outage.py`,
  `test_timelapse.py`, `test_reservations.csv`) — gitignored and deleted.

**Reorganized into subdirectories:**

| From (root) | To | Reason |
|-------------|----|--------|
| 6 ecobee scripts | `ecobee/` | Group related historical scripts |
| `install-services.sh`, `generate_cert.sh`, `deploy-pumphouse-certs.sh`, `setup_reservation_cron.sh` | `bin/` | Setup/admin scripts run once or rarely |
| `pumphouse-*.service` | `terraform/services/` | System config alongside other IaC |
| `scrape_reservations.py`, `check_new_reservations.py`, `update_reservations.sh` | `bin/` | Operational scripts (cron-called) |

**`bin/install-services.sh`** updated to reference `terraform/services/` path.
**`bin/setup_reservation_cron.sh`** updated: `SCRIPT_DIR` now points to project
root (one level up from `bin/`).

---

## 3. Terraform Services and Cron Documentation

Created `terraform/services/` for systemd service units and `terraform/cron/`
for cron documentation.

- `terraform/services/` — the three `.service` files; installed by
  `bin/install-services.sh` via `sudo cp`.
- `terraform/cron/crontab.example` — the authoritative crontab reference.
  Install with `crontab terraform/cron/crontab.example`.
- `terraform/cron/README.md` — job table and install instructions.
- `terraform/README.md` — overview of all three subdirectories.

**Note:** Cron and systemd are not actually managed by Terraform — the directory
name reflects "infrastructure configuration" broadly, not Terraform specifically.

---

## 4. XDG Data Directory Migration

Moved application data from the project root (CWD) to the XDG standard location.

### Background

The project had been using the process CWD (project root) for data files, which
is an anti-pattern for server applications. The XDG Base Directory Specification
defines standard locations:

| Purpose | Directory |
|---------|-----------|
| Configuration | `~/.config/pumphouse/` ← already correct |
| Persistent data | `~/.local/share/pumphouse/` ← migrated to this |
| Cache | `~/.cache/pumphouse/` ← future (epaper cache, etc.) |
| State/logs | `~/.local/state/pumphouse/` ← not used (prefer share/) |

### Files migrated

| File | Old location | New location |
|------|-------------|-------------|
| `events.csv` | project root | `~/.local/share/pumphouse/` |
| `reservations.csv` | project root | `~/.local/share/pumphouse/` |
| `reservations_snapshot.csv` | project root | `~/.local/share/pumphouse/` |

**Not yet migrated** (future work):
- `snapshots.csv` — stays in project root for now
- `gph_cache.csv`, `gph_log.txt` — stays in project root
- `epaper_cache*.bmp` — future: `~/.cache/pumphouse/`
- `system_health.log`, `reservation_updates.log` — stays in project root

### Code changes

**`monitor/config.py`** — single source of truth for data paths:
```python
DATA_DIR                   = Path.home() / '.local' / 'share' / 'pumphouse'
EVENTS_FILE                = DATA_DIR / 'events.csv'
RESERVATIONS_FILE          = DATA_DIR / 'reservations.csv'
RESERVATIONS_SNAPSHOT_FILE = DATA_DIR / 'reservations_snapshot.csv'
DEFAULT_EVENTS_FILE        = str(EVENTS_FILE)   # used by main.py CLI args
```
`DATA_DIR.mkdir(parents=True, exist_ok=True)` runs on import so the directory
is always created on first use.

**`main.py`** picks up `DEFAULT_EVENTS_FILE` automatically via the `--events`
CLI arg default — no change needed there.

**Updated to use config constants** (replacing hardcoded `'events.csv'` /
`'reservations.csv'` strings):
- `monitor/web.py` — `EVENTS_FILE`, `RESERVATIONS_FILE`
- `monitor/poll.py` — `RESERVATIONS_FILE`
- `monitor/email_notifier.py` — `EVENTS_FILE`, `RESERVATIONS_FILE`
- `monitor/occupancy.py` — `RESERVATIONS_FILE` (was `Path(__file__).parent.parent / 'reservations.csv'`)
- `monitor/epaper_jpg.py` — `RESERVATIONS_FILE` (default param → `None` with guard)

**`bin/check_new_reservations.py`** (moved from root):
- `sys.path` fixed: `Path(__file__).parent.parent` (project root) not `parent / 'monitor'`
- All file paths use `EVENTS_FILE`, `RESERVATIONS_FILE`, `RESERVATIONS_SNAPSHOT_FILE`

**`bin/scrape_reservations.py`** (moved from root):
- `--output` default changed from `'reservations.csv'` to `str(DATA_DIR / 'reservations.csv')`
- `DATA_DIR.mkdir(parents=True, exist_ok=True)` added

**`bin/update_reservations.sh`** (moved from root):
- Uses `BIN_DIR` / `PROJECT_DIR` / `DATA_DIR` variables
- Calls scripts by full path (`$BIN_DIR/scrape_reservations.py`, etc.)
- Removed `cd "$SCRIPT_DIR"` dependency

### Crontab update

Live crontab updated: `update_reservations.sh` path changed from project root
to `bin/`. `terraform/cron/crontab.example` updated to match.

---

## 5. AP CSP Lesson Plans Split

Split `docs/lessons/ap-csp-timelapse-unit.md` into two files:

- **Part 1** (`ap-csp-timelapse-part1.md`) — Sessions 1–4: HTTP/networking,
  data/APIs, compression, TLS. Covers the single-Pi timelapse setup up to the
  HTML/CSS/JS viewer.
- **Part 2** (`ap-csp-timelapse-part2.md`) — Sessions 5–8: CDN physics/DoS,
  edge computing (Cloudflare Worker), Terraform/IaC, Security, Ethics. Covers
  global CDN infrastructure and the `pumphouse-ratings` Worker.

---

## 6. Other Changes in This Session Period

- **Snapshot page**: Added `(cached until HH:MM)` to title; title centered over
  image (max-width 860px); `crop=1` confirmed as default (privacy).
- **Dashboard/Now buttons**: Dashboard button hidden on Cloudflare-served pages;
  Now button always shown on timelapse page.
- **Cloudflare Worker caching**: `cf: { cacheEverything: true, cacheTtlByStatus: { '200': 300 } }`
  — `caches.default.put()` silently refuses text/html; this approach works.
- **cert.pem security fix**: Self-signed certs were accidentally committed.
  Removed with `git rm --cached`, added `*.pem` to `.gitignore`. Active certs
  in `certs/` (Let's Encrypt) are gitignored and unaffected.
- **README author bio**: Added Eric Brown bio with `porttack.com` link near top.
- **README lesson plan links**: Added Part 1 and Part 2 links.
