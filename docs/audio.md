# Audio Listener (Dosatron & Bypass Flow Detection)

The audio listener runs as `pumphouse-audio` and uses a USB microphone to detect two things:

- **Dosatron clicks** — the mechanical click/clack of the Dosatron fertilizer injector firing. Each firing delivers ~0.14 gal (counted as two clicks at 0.07 gal each). Used to estimate fertilizer applied per pump cycle.
- **Bypass flow** — a hollow 190–210 Hz resonance in the audio spectrum that appears when water flows through the bypass line. Detected via FFT spectral ratio scoring rather than raw amplitude.

The web dashboard Dosatron page shows listener status, recorded cycles, click counts, and a live audio stream.

---

## Hardware

**USB PnP Sound Device** — a generic USB audio adapter with a microphone positioned near the Dosatron.

Verify it's recognized:
```bash
arecord -l
# Should show: card N: Device [USB PnP Sound Device], device 0: USB Audio
```

---

## ALSA Configuration (`~/.asoundrc`)

The listener and the live-stream endpoint both need to read from the mic simultaneously. ALSA doesn't allow two processes to open a hardware capture device at once, so a **dsnoop** (direct-snooping) virtual device is used to share it.

`~/.asoundrc` must exist with this content:

```
pcm.dosatron_in {
    type dsnoop
    ipc_key 1024
    slave {
        pcm "hw:CARD=Device,DEV=0"
        channels 1
        rate 44100
        format S16_LE
    }
}
```

Using `CARD=Device` (the card's stable name) rather than `hw:3,0` (a card number that changes on reboot) prevents the device from silently breaking after a reboot.

**This file is not tracked in git.** If it goes missing, the listener auto-creates it on startup.

Verify the device works:
```bash
arecord -D dosatron_in -d 1 -r 44100 -c 1 -f S16_LE /dev/null
# Should print: Recording WAVE '/dev/null' : Signed 16 bit Little Endian, Rate 44100 Hz, Mono
```

---

## Running as a Systemd Service (Recommended)

```bash
# Install services and configure ~/.asoundrc (one-time)
bin/install-services.sh
sudo systemctl enable --now pumphouse-audio

# Check status
sudo systemctl status pumphouse-audio

# View live logs
sudo journalctl -u pumphouse-audio -f

# Restart after code changes
sudo systemctl restart pumphouse-audio
```

The service restarts automatically on failure (`RestartSec=10`), so a momentary USB device drop or ALSA hiccup will recover within 10 seconds.

---

## Running Manually (for Testing)

```bash
# Start in foreground (Ctrl-C to stop)
cd ~/src/pumphouse
source venv/bin/activate
python -m monitor.dosatron

# Start in background
nohup venv/bin/python -m monitor.dosatron >> ~/.local/share/pumphouse/dosatron/detections.log 2>&1 &

# Calibrate: show ambient noise levels for 10 seconds
python -m monitor.dosatron --calibrate

# Check if running
cat ~/.local/share/pumphouse/dosatron/listener.pid
ps aux | grep monitor.dosatron

# Stop
kill $(cat ~/.local/share/pumphouse/dosatron/listener.pid)
```

---

## Click Detection Calibration

Run the calibration tool to see your ambient noise floor before choosing a threshold:

```bash
python -m monitor.dosatron --calibrate
```

It prints peak and RMS for 10 seconds. Set `--threshold` to roughly 3–5× the ambient peak. The default is 2000.

The threshold can also be changed live from the Dosatron web page (no restart needed — re-read every 30 s from `~/.local/share/pumphouse/dosatron/config.json`).

---

## Bypass Flow Detection Calibration

Flow detection uses an FFT spectral ratio:

```
score = energy(100–400 Hz) / energy(1000–4000 Hz)
```

Calibrated values from a real bypass event:
- Quiet ambient: score ≈ 11–12
- Water flowing: score ≈ 39–40

The default ratio threshold is **22.0** (midpoint). Adjustable from the Dosatron web page alongside the minimum RMS gate (default 75) that prevents near-silence from producing spurious scores.

---

## Data Files

All data lives under `~/.local/share/pumphouse/dosatron/`:

| File | Contents |
|------|----------|
| `detections.jsonl` | One record per detected click (ts, peak, rms, threshold, clip filename) |
| `cycles.jsonl` | One record per pressure cycle (high_ts, low_ts, duration, click_count, audio_file) |
| `flow_cycles.jsonl` | One record per bypass flow event (start_ts, end_ts, duration, audio_file) |
| `clips/` | Short WAV clips around each click (7-day retention) |
| `cycles/` | Full-cycle WAV recordings (14-day retention) |
| `flow_cycles/` | Bypass flow WAV recordings (14-day retention) |
| `config.json` | Current threshold settings (edited via web UI) |
| `listener.pid` | PID of the running listener (absent when stopped) |
| `prediction.json` | Next pump cycle prediction (written by poll.py) |
| `bypass_prediction.json` | Next bypass flow prediction (written by listener) |
| `detections.log` | Listener log (startup, clicks, cycle events) |

---

## Troubleshooting

**Dashboard shows "Listener not running"**

1. Check the service: `sudo systemctl status pumphouse-audio`
2. Check the log: `sudo journalctl -u pumphouse-audio -n 50`
3. Check the PID file: `cat ~/.local/share/pumphouse/dosatron/listener.pid`
4. If the log shows `arecord stream ended` or `Unknown PCM dosatron_in`:
   - Verify `~/.asoundrc` exists with the content above
   - Test the device: `arecord -D dosatron_in -d 1 -r 44100 -c 1 -f S16_LE /dev/null`
   - Check the USB device is present: `arecord -l`

**`Unknown PCM dosatron_in`**

`~/.asoundrc` is missing or malformed. Re-run `bin/install-services.sh` (it will append the `dosatron_in` stanza if absent), then restart the service. Or recreate it manually from the content in the ALSA Configuration section above.

**Card number changed after reboot**

If `~/.asoundrc` uses `hw:3,0` (hardcoded number) instead of `hw:CARD=Device,DEV=0` (stable name), a reboot can break it. Fix: update `~/.asoundrc` to use `hw:CARD=Device,DEV=0`.

**Double log lines in `detections.log`**

Expected when starting manually with stdout redirected to the same log file — the FileHandler and the redirected stdout both write there. No impact on function. Not an issue when running under systemd (stdout goes to journal only).
