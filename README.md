# Pumphouse Monitor & Timelapse

Remote monitoring and control system for a well-water pump house serving an Airbnb property on the Oregon coast. Monitors tank levels, pump activity, water quality, and captures daily sunset timelapses — all managed remotely from a Raspberry Pi 4. Daily sunset timelapses can be seen at [onblackberryhill.com](https://onblackberryhill.com).

---

**Built and maintained by [Eric Brown](https://porttack.com/) — engineer, teacher, sailor.** Eric teaches Computer Science at San Lorenzo Valley High School (CA) and previously worked as an engineer at Khan Academy, Apple, Adobe, Sony, Kodak, and others. This project grew from a practical problem into a teaching vehicle; the timelapse system is used as a real-world case study in his AP Computer Science Principles course. See his website and blog at [porttack.com](https://porttack.com/).

**AP CSP lesson plans using this project:**
- [Part 1 — From a Single JPEG to an Interactive Web Viewer](docs/lessons/ap-csp-timelapse-part1.md) (4 × 30 min: HTTP, networking, APIs, compression)
- [Part 2 — From Single Server to Global Edge Infrastructure](docs/lessons/ap-csp-timelapse-part2.md) (4 × 30 min: CDN, DoS, edge computing, Terraform/IaC)

---

## Why

- **Remote operation**: The property is a 10-hour round trip. Every issue that can be diagnosed and resolved remotely is a trip saved.
- **Guest water reliability**: Airbnb guests depend on a continuously-filled ~1,400-gallon holding tank. Automated monitoring and alerts catch problems before guests notice.
- **Automated maintenance**: Automatic spindown filter purging reduced service visits from weekly to every 6–8 months (~$2,400/year savings).
- **Opportunistic filling**: Well pump power is controlled by the highest-elevation tank in the system (not ours). Override lets us fill whenever power happens to be on, not just when our float switch calls for water.
- **Guest water guidance**: An e-paper display at the property shows guests current water availability and when to delay laundry or other high-use activities — reducing the risk of running low during a stay.
- **Marketing**: Daily sunset timelapses are published at [onblackberryhill.com](https://onblackberryhill.com/timelapse) to showcase the property.

## How: Three Daemons

| Service | Source | Role |
|---------|--------|------|
| `pumphouse-monitor` | `monitor/` | Core monitoring loop: pressure, float, tank level, relay control, CSV logging, notifications |
| `pumphouse-web` | `monitor/web.py` | Flask/gunicorn HTTPS server: dashboard, API endpoints, timelapse viewer, Cloudflare tunnel target |
| `pumphouse-timelapse` | `sunset_timelapse.py` | Captures RTSP frames around sunset, assembles MP4, serves preview during capture |

All three run as systemd services. See [docs/installation.md](docs/installation.md) for setup.

## Inputs & Sensors

| Sensor | Type | Location | Method |
|--------|------|----------|--------|
| Pressure switch | 10 PSI NC switch | On-site (pipe) | GPIO 17, polled every 5 s |
| Float switch | NO float switch | On-site (tank) | GPIO 27, hardwired to supply valve + software monitoring |
| Tank depth sensor | PT sensor | Off-site | Scraped from mypt.in every 60 s |
| Temp / humidity | AHT20 I2C | On-site (Pi enclosure) | I2C bus 1, sampled with each snapshot |
| Sunset camera | Amcrest IP camera | On-site | RTSP stream + HTTPS digest-auth snapshots |
| Thermostats | Ecobee (2×) | On-site | Web scraping (API program closed) |
| STR reservations | TrackHS | Off-site | Web scraping 2×/day |
| Weather station | Ambient Weather | On-site | [integration TBD] |
| Weather (observed) | NWS KONP station | Off-site | NWS API, cached per-day |
| Weather (historical) | Open-Meteo ERA5 | Off-site | ERA5 API, cached per-day |

See [docs/hardware-reference.md](docs/hardware-reference.md) for GPIO pin assignments, wiring diagrams, valve configuration, and full water system flow.

## Clients & Consumers

| Client | How It Accesses | Purpose |
|--------|-----------------|---------|
| Float switch (hardware) | Hardwired to supply valve relay | Primary tank fill control; opens/closes at 80 % / 95 % |
| Web dashboard | LAN DDNS hostname, port 6443 (HTTPS) | Owner monitoring: live sensor readings, tank chart, reservations table |
| E-paper display | Fetches `/api/epaper.bmp` every 5 min | 2.13″ e-ink at vacation rental: tank level, usage graph, occupancy bar; "Save Water" warning for guests |
| iPhone widget | Scriptable app fetches `/api/epaper.bmp` | Same BMP as e-paper on iPhone home screen |
| Sunset timelapse | `https://onblackberryhill.com/timelapse` | Public-facing via Cloudflare CDN; MP4 viewer with weather strip, ratings, keyboard navigation |
| Email alerts | Gmail SMTP | Rich HTML emails with embedded chart, sensor readings, one-click relay control buttons |
| Push notifications | ntfy.sh | Real-time phone alerts for tank thresholds, well recovery, high flow, backflush |

## Security

- **Secrets file**: All credentials in `~/.config/pumphouse/secrets.conf` (mode 600, excluded from git)
- **TLS**: Let's Encrypt cert on the `tplinkdns.com` hostname for direct LAN access; Cloudflare manages TLS for the public domain
- **Authentication**: HTTP Basic Auth over TLS protects the web dashboard — credentials are encrypted in transit by the TLS layer
- **Remote relay control**: 256-bit random tokens in the secrets file; one-click buttons embedded in email alerts; URLs not guessable
- **No publicly published open ports**: Public access exclusively via Cloudflare Tunnel (outbound-only connection from Pi); the LAN hostname and real IP are never exposed

## Repository Layout

```
pumphouse/
├── monitor/                     # Main monitoring package (pumphouse-monitor + pumphouse-web)
│   ├── config.py                # All configuration constants; loads secrets.conf + monitor.conf
│   ├── main.py                  # Entry point: CLI arg parsing, GPIO init, starts monitor loop
│   ├── poll.py                  # Main monitoring loop (pressure, float, tank, relay, snapshots)
│   ├── web.py                   # Flask web server & all API routes (dashboard, /api/*, timelapse)
│   ├── web_timelapse.py         # Timelapse viewer routes and MP4/JPG serving
│   ├── state.py                 # SystemState dataclass — shared live sensor state
│   ├── stats.py                 # Snapshot-based statistics (GPH, pressure %, averages)
│   ├── logger.py                # CSV event & snapshot logging
│   ├── notifications.py         # Notification rule engine (triggers, cooldowns, dispatch)
│   ├── email_notifier.py        # HTML email construction & SMTP sending
│   ├── ntfy.py                  # ntfy.sh push notifications
│   ├── tank.py                  # mypt.in tank sensor scraping & timestamp parsing
│   ├── gpio_helpers.py          # GPIO access with retry logic and mock support
│   ├── relay.py                 # Relay open/close control
│   ├── relay_state.py           # Relay state persistence (relay_state.json)
│   ├── purge.py                 # Automatic filter purge relay control
│   ├── gph_calculator.py        # Well GPH calculation & caching (gph_cache.csv)
│   ├── ecobee.py                # Ecobee thermostat data (web scraping, token refresh)
│   ├── occupancy.py             # Occupancy detection from reservations.csv
│   ├── ambient_weather.py       # Ambient Weather station API integration
│   ├── weather_api.py           # NWS & Open-Meteo ERA5 weather data (cached per-day)
│   ├── epaper_jpg.py            # E-paper BMP/JPG image generation for display & widget
│   ├── restart_tracker.py       # Crash loop detection (restart_tracker.json)
│   ├── check.py                 # Status checker & diagnostic test runner (--test-email, etc.)
│   └── templates/status.html   # Web dashboard Jinja2 template
├── pistat/                      # E-paper display daemon (runs on separate Pi)
│   ├── epaper_daemon.py
│   ├── IPHONE_WIDGET.md         # iPhone Scriptable widget setup
│   └── README.md                # Display daemon installation & troubleshooting
├── cloudflare/                  # Cloudflare Worker & deploy scripts
│   ├── ratings-worker.js        # KV-backed ratings Worker source
│   ├── deploy.sh                # Deploy worker (reads credentials from secrets.conf)
│   └── wrangler.toml            # Wrangler deploy config
├── terraform/                   # Infrastructure configuration
│   ├── cloudflare/              # Cloudflare infrastructure as code (Terraform)
│   │   ├── main.tf              # Provider config & required versions
│   │   ├── variables.tf         # Input variables (account ID, zone ID, tunnel secret)
│   │   ├── tunnel.tf            # Zero Trust Tunnel + DNS records
│   │   ├── worker.tf            # KV namespace, Worker script, routes
│   │   ├── redirects.tf         # Redirect rules (/ → /timelapse, www → apex)
│   │   ├── cache.tf             # Cache rules (HTML caching, DoS mitigations)
│   │   ├── outputs.tf           # Tunnel token, KV namespace ID
│   │   └── terraform.tfvars.example
│   ├── services/                # systemd service unit files
│   │   ├── pumphouse-monitor.service
│   │   ├── pumphouse-web.service
│   │   └── pumphouse-timelapse.service
│   └── cron/                    # Crontab reference and install docs
│       ├── crontab.example      # All scheduled jobs with correct paths
│       └── README.md
├── ecobee/                      # Ecobee thermostat scripts (historical / reference)
│   ├── ecobee_control.py        # Direct Ecobee API control
│   ├── fetch_ecobee_data.py     # Fetch thermostat data
│   ├── fetch_ecobee_temp.py     # Fetch temperature readings
│   ├── fetch_ecobee_temp_cron.sh # Cron wrapper for temperature fetch
│   ├── scrape_ecobee.py         # Web scraping fallback
│   └── scrape_ecobee_selenium.py # Selenium-based scraping
├── bin/                         # Scripts: setup/admin (run once) + cron-called scripts
│   ├── install-services.sh      # Copies & enables all systemd services
│   ├── generate_cert.sh         # Self-signed SSL certificate generator
│   ├── deploy-pumphouse-certs.sh # Copies Let's Encrypt certs, restarts web service
│   ├── setup_reservation_cron.sh # Installs reservation-scraper cron entries
│   ├── update_reservations.sh   # Cron wrapper: scrape + check + notify
│   ├── scrape_reservations.py   # TrackHS reservation downloader
│   └── check_new_reservations.py # New/changed/canceled booking detector
├── docs/                        # All documentation
│   ├── conversations/           # Development session notes
│   └── lessons/                 # Educational content
├── sunset_timelapse.py          # Timelapse capture daemon (pumphouse-timelapse)
├── control.sh                   # Safe relay control script (no GPIO conflicts)
├── log_daily_gph.py             # Daily GPH metric logger (runs via cron)
├── system_health_monitor.sh     # System vitals logger (runs via cron)
├── requirements.txt
└── CHANGELOG.md
```

## Files & Directories Outside the Repository

```
~/.config/pumphouse/
    secrets.conf              # All credentials (600 permissions, never in git)
    monitor.conf              # Optional config overrides (see docs/installation.md)
    ecobee_tokens.json        # Ecobee OAuth tokens (auto-generated on first run)

~/.local/share/pumphouse/    # Application data (XDG data dir)
    events.csv                # All state-change events
    reservations.csv          # Current TrackHS reservations
    reservations_snapshot.csv # Previous snapshot for change detection

~/timelapses/                 # Timelapse archive (written by pumphouse-timelapse)
    YYYY-MM-DD_HHMM.mp4       # Daily MP4s (sunset time in filename)
    snapshots/
        YYYY-MM-DD.jpg        # Post-sunset camera snapshots
    weather/
        YYYY-MM-DD.json       # Cached weather data (NWS + ERA5)
    ratings.json              # Local ratings mirror (canonical store is Cloudflare KV)
    timelapse.log             # Daemon log

/tmp/timelapse-frames/        # tmpfs — RTSP frame staging during active capture
                              # Frames never touch SD card; deleted after MP4 assembly

/etc/systemd/system/          # Installed service units
    pumphouse-monitor.service
    pumphouse-web.service
    pumphouse-timelapse.service
    cloudflared.service       # Cloudflare Tunnel (installed by cloudflared)

# Data files in the repo directory (excluded from git):
snapshots.csv                 # 15-minute aggregated summaries
gph_cache.csv                 # GPH calculation cache (24 h TTL)
gph_log.txt                   # Daily GPH logger output
system_health.log             # System vitals log
reservation_updates.log       # Cron reservation update log
cert.pem / key.pem            # TLS certificate files
```

## Documentation Index

| Document | Contents |
|----------|----------|
| [docs/pi-setup.md](docs/pi-setup.md) | Full Pi setup from scratch: OS, WiFi, packages, WiringPi, services, cron |
| [docs/hardware-reference.md](docs/hardware-reference.md) | GPIO pins, wiring, valve config, water flow, all sensors & data sources |
| [docs/installation.md](docs/installation.md) | Python venv, configuration file reference, running & testing |
| [docs/data-format.md](docs/data-format.md) | CSV event & snapshot format, all event types, water volume estimation |
| [docs/relay-control.md](docs/relay-control.md) | Relay control script, auto-purge, override valve automation |
| [docs/web-dashboard.md](docs/web-dashboard.md) | Web dashboard setup, SSL, features, custom credentials |
| [docs/email-setup.md](docs/email-setup.md) | Email notification setup, Gmail App Password, troubleshooting |
| [docs/push-notifications.md](docs/push-notifications.md) | ntfy.sh push notifications, all alert types, configuration |
| [docs/epaper.md](docs/epaper.md) | E-paper BMP endpoint, display modes, query parameters |
| [docs/timelapse.md](docs/timelapse.md) | Timelapse viewer routes, keyboard/swipe nav, weather strip, ratings, caching |
| [docs/cloudflare.md](docs/cloudflare.md) | Cloudflare Tunnel + CDN setup, KV ratings Worker, step-by-step |
| [docs/occupancy.md](docs/occupancy.md) | Occupancy detection, check-in/out event logging, reservations in dashboard |
| [docs/reservations.md](docs/reservations.md) | TrackHS scraper, cron setup, new/changed/canceled notification events |
| [docs/ecobee.md](docs/ecobee.md) | Ecobee thermostat integration, web scraping, current capabilities |
| [docs/gph-tracking.md](docs/gph-tracking.md) | Well GPH tracking, calculation method, caching, historical logging |
| [docs/system-health.md](docs/system-health.md) | System health monitor, throttling bitmask, journal persistence |
| [docs/todo.md](docs/todo.md) | Pending tasks, security items, ideas, completed history |
| [CHANGELOG.md](CHANGELOG.md) | Version history |
| [pistat/README.md](pistat/README.md) | E-paper daemon installation & troubleshooting |
| [pistat/IPHONE_WIDGET.md](pistat/IPHONE_WIDGET.md) | iPhone Scriptable widget setup |
| [docs/conversations/](docs/conversations/) | Development session logs — design decisions, debugging, implementation history |
| [docs/lessons/ap-csp-timelapse-part1.md](docs/lessons/ap-csp-timelapse-part1.md) | AP CSP Part 1 — From a Single JPEG to an Interactive Web Viewer (4 × 30 min: HTTP, networking, APIs, compression) |
| [docs/lessons/ap-csp-timelapse-part2.md](docs/lessons/ap-csp-timelapse-part2.md) | AP CSP Part 2 — From Single Server to Global Edge Infrastructure (4 × 30 min: CDN, DoS, edge computing, Terraform/IaC) |
