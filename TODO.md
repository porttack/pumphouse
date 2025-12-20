# Pumphouse Monitor - TODO List

## Pending Enhancements

### High Priority

- [ ] **Email confirmation for remote control actions**
  - Send follow-up email when user clicks Override ON/OFF, Bypass ON/OFF, or Purge buttons
  - Email should confirm the action was successful with current system status
  - Subject: e.g., "✓ Override Turned ON - 1320 gal"
  - Include event details and current relay states in email body
- [ ] one email per day (morning)
- [ ] turn on override below 1300 maybe?
- [ ] Option to turn on overide N times per day if < certain value

### Medium Priority

- [ ] Change email from address
- [ ] Shorten email subject lines
- [ ] Commit messages should include entire claude conversation
- [ ] Tank Full Confirmed - 1430 gal (this should be 3 tank reads, not 3 60-sec heartbeats)
- [ ] What does the alert "Tank Filling" actually mean?
- [ ] Log an event when we do a backflush
- [ ] Log estimated GPH
- [ ] Estimate chlorine use
- [ ] Ignore fluctuating sensor readings - pressure & float
  - Or maybe these went away after refactoring gpio stuff ~12/13/25
- [ ] Just show me 6 days (more than that and days of week get confusing)
- [ ] Do we even need snapshot logs?
  - Can help identify when float starts/stops calling
  - Put sensor changes into graph?
- [ ] Matplotlib stuff should not go all the way to zero
- [ ] Make a widget out of it for my phone and desktop
- [ ] Camera link should go to the app on iOS
- [ ] Is gpiozero better (less contention, no false readings)

### Low Priority

- [ ] _Add your ideas here_

### Future Ideas

- [ ] _Add your ideas here_

---

## Completed Items

### 2025-12-20 - Version 2.10.0
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
