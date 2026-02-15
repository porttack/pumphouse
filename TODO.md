# Pumphouse Monitor - TODO List

## Pending Enhancements

### HIGH PRIORITY - Security: Sensitive Data in Repository

- [x] **Remove Wyze camera module** (was CRITICAL)
  - Deleted `monitor/camera.py` — unused, Wyze APIs never worked
  - Credentials were non-functional; no scrub needed
- [x] **Move DDNS hostname to secrets.conf**
  - PUMPHOUSE_HOST and PUMPHOUSE_PORT loaded from secrets.conf
  - All committed files use placeholders; epaper_daemon.py reads secrets directly
- [ ] **Move ntfy.sh topic name to secrets.conf**
  - `monitor/config.py` — anyone with the topic name can spam push notifications
- [ ] **Move tank monitoring URL to secrets.conf**
  - `monitor/config.py`, `README.md`, `check.py` — unique URL could be abused
- [ ] **Remove nohup.out from git tracking**
  - Contains external IP addresses and server logs; add to `.gitignore`
- [ ] **Move email addresses to secrets.conf**
  - `monitor/config.py` — personal Gmail addresses
- [ ] **Move Ambient Weather dashboard URL to secrets.conf**
  - `monitor/config.py` — unique dashboard ID
- [ ] **Scrub git history** after moving secrets
  - Use `git filter-repo` or BFG to remove sensitive data from past commits
  - Without this, secrets remain accessible in git history even after removal

### High Priority

- [ ] **Email confirmation for remote control actions**
  - Send follow-up email when user clicks Override ON/OFF, Bypass ON/OFF, or Purge buttons
  - Email should confirm the action was successful with current system status
  - Subject: e.g., "✓ Override Turned ON - 1320 gal"
  - Include event details and current relay states in email body
- [ ] Change color of dots again on fast-fill

### Medium Priority

- [ ] Every 6-hours, determine if float is really calling (turn overflow off temporarily)
- [ ] automate vacation mode
- [ ] Log estimated GPH
- [ ] Estimate chlorine use
- [ ] Ignore fluctuating sensor readings - pressure & float
  - Or maybe these went away after refactoring gpio stuff ~12/13/25
- [ ] Put sensor changes into graph? (override, occupied, bypass)
- [ ] Matplotlib stuff should not go all the way to zero
- [x] Make a widget out of it for my phone and desktop (2026-02-15 - Scriptable iPhone widget + e-paper display)
- [ ] Camera link should go to the app on iOS
- [ ] Is gpiozero better (less contention, no false readings)
- [ ] Roll and compress logs
- [ ] Backup private config (secrets.conf, etc.) in private github repo?
- [ ] Stagnation region should restart if water drops > 20 gallons (house use)
- [ ] Put availability calendar on web page too? (or more months of reservations)
- [ ] Log all data to a google sheet

### Low Priority

- [ ] _Add your ideas here_

### Future Ideas

- [ ] _Add your ideas here_

---

## Completed Items

### Recent
- [x] one email per day (morning)
- [x] Email notification on new reservations
- [x] Remove Repeat and Booking columns unless authenticated with token (2025-12-21)
- [x] Occupancy tracking and reservation display (2025-12-21)
- [x] Visual stagnation detection on chart with color-coded dots (2025-12-21)
- [x] Consolidated stagnation logic to single 6-hour definition (2025-12-21)
- [x] Log an event when we do a backflush
- [x] What does the alert "Tank Filling" actually mean?
- [x] Change email from address
- [x] Shorten email subject lines
- [x] Commit messages should include entire claude conversation

### 2025-12-20 - Version 2.10.0
- [x] turn on override below a threshold
- [x] Fixed duplicate "Well Recovery Detected" alerts
  - Added persistent notification state to track which recovery events have been alerted
  - State survives service restarts (notification_state.json)
- [x] Persistent relay state across service restarts
  - Override and bypass states saved to disk (relay_state.json)
  - States automatically restored on monitor startup
  - No more accidental override turn-off on restart
- [x] Safety shutoff when tank level cannot be read
  - Override automatically turned off if internet/PT sensor unavailable
  - Prevents tank overflow during network outages
  - Sends urgent notification when safety shutoff triggers

### 2025-12-14 - Version 2.8.0
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
