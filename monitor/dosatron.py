#!/usr/bin/env python3
"""
Dosatron click detector — background listener process.

Two things happen simultaneously:
  1. Individual click detection: amplitude spike → short WAV clip saved to clips/
  2. Cycle recording: full audio from PRESSURE_HIGH to PRESSURE_LOW + 30 s saved to cycles/
     The cycle recorder is driven by pressure_signal.json written by poll.py.

Usage:
    python -m monitor.dosatron                   # start listener
    python -m monitor.dosatron --calibrate       # 10 s noise floor check

Threshold is re-read from config.json every 30 s — no restart needed.
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
import wave
from collections import deque
from datetime import datetime

import numpy as np

# ── audio ──────────────────────────────────────────────────────────────────────
SAMPLE_RATE      = 44100
CHANNELS         = 1
BYTES_PER_SAMPLE = 2
CHUNK_SAMPLES    = 512        # ~12 ms per chunk
CHUNK_BYTES      = CHUNK_SAMPLES * CHANNELS * BYTES_PER_SAMPLE
CHUNKS_PER_SEC   = SAMPLE_RATE // CHUNK_SAMPLES   # ≈ 86
DEVICE           = "dosatron_in"   # dsnoop virtual device (see ~/.asoundrc)

# ── dosatron calibration ──────────────────────────────────────────────────────
# Each dosatron firing produces a click + clack (2 sounds). Together they
# deliver 0.14 gal.  Our detector may count each sound separately, so:
GALLONS_PER_CLICK = 0.07    # 0.14 gal ÷ 2 sounds per firing

# ── click detection defaults ───────────────────────────────────────────────────
DEFAULT_THRESHOLD     = 2000
DEBOUNCE_SECS         = 1.0
PRE_ROLL_SECS         = 1.0
POST_ROLL_SECS        = 5.0   # 1 s pre + 5 s post = 6 s clip

# ── cycle recording ────────────────────────────────────────────────────────────
MAX_CYCLE_SECS    = 3600    # hard cap: 60 minutes
POST_LOW_SECS     = 30      # keep recording after PRESSURE_LOW
CYCLE_RETAIN_DAYS = 14
CLIP_RETAIN_DAYS  = 7

# ── bypass / flow detection ────────────────────────────────────────────────────
# FFT-based detection: the bypass water makes a hollow 190–210 Hz resonance.
# We score each 1-second window by the ratio of energy in the 100–400 Hz band
# (the hollow-flow band) to energy in the 1000–4000 Hz band (flat reference).
# FFT analysis of a real bypass event showed:
#   quiet ambient:  ratio ≈ 11–12
#   flowing water:  ratio ≈ 39–40   (3–4× above ambient)
DEFAULT_FLOW_RATIO     = 22.0   # score threshold between quiet(12) and flow(39)
FLOW_SIGNAL_BAND       = (100, 400)    # Hz — hollow resonance lives here
FLOW_REF_BAND          = (1000, 4000)  # Hz — flat background reference
FLOW_FFT_SECS          = 1.0    # accumulate this many seconds before scoring
FLOW_START_SECS        = 5.0    # score must stay above threshold this long to start
FLOW_STOP_SECS         = 10.0   # score must stay below threshold this long to stop
FLOW_MIN_SECS          = 15.0   # discard recordings shorter than this
RELAY_POLL_SECS        = 5      # how often to check relay_state.json

# Keep the old RMS threshold as a minimum-activity gate (prevents FFT noise
# in near-silence from producing spurious ratios).
DEFAULT_FLOW_THRESHOLD = 75     # min RMS — if below this, skip FFT entirely

# ── paths ──────────────────────────────────────────────────────────────────────
DATA_DIR          = os.path.expanduser("~/.local/share/pumphouse/dosatron")
CLIPS_DIR         = os.path.join(DATA_DIR, "clips")
CYCLES_DIR        = os.path.join(DATA_DIR, "cycles")
FLOW_CYCLES_DIR   = os.path.join(DATA_DIR, "flow_cycles")
JSONL_FILE        = os.path.join(DATA_DIR, "detections.jsonl")
CYCLES_JSONL      = os.path.join(DATA_DIR, "cycles.jsonl")
FLOW_CYCLES_JSONL = os.path.join(DATA_DIR, "flow_cycles.jsonl")
CYCLE_LABELS      = os.path.join(DATA_DIR, "cycle_labels.json")
FLOW_CYCLE_LABELS = os.path.join(DATA_DIR, "flow_cycle_labels.json")
CONFIG_FILE       = os.path.join(DATA_DIR, "config.json")
SIGNAL_FILE       = os.path.join(DATA_DIR, "pressure_signal.json")
PID_FILE          = os.path.join(DATA_DIR, "listener.pid")
BYPASS_VALVE_PIN  = 26   # BCM pin — active-low relay (0 = ON); mirrors relay.py

# Optional imports from the wider monitor package — used for event logging and
# notifications.  Wrapped in a try/except so the listener still runs if the
# monitor package is unavailable (e.g. during unit tests).
try:
    from monitor.logger import log_event as _log_event
    from monitor.ntfy   import send_notification as _send_notification
    from monitor.config import (
        EVENTS_FILE          as _EVENTS_FILE,
        BYPASS_FLOW_WATCH_FILE as _BYPASS_FLOW_WATCH_FILE,
        DASHBOARD_URL        as _DASHBOARD_URL,
    )
    _MONITOR_OK = True
except Exception:
    _MONITOR_OK = False
    _EVENTS_FILE = _BYPASS_FLOW_WATCH_FILE = _DASHBOARD_URL = None  # type: ignore


# ── public utility ─────────────────────────────────────────────────────────────

def count_clicks(start_unix: float, end_unix: float) -> int:
    """Count Dosatron detections between two Unix timestamps.

    Each entry in detections.jsonl is one Dosatron firing ≈ one gallon.
    Returns 0 on any error so callers can treat 0 as "unknown".
    """
    try:
        start_dt = datetime.fromtimestamp(start_unix)
        end_dt   = datetime.fromtimestamp(end_unix)
        count = 0
        with open(JSONL_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts_str = rec.get("ts", "")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is not None:
                        ts = ts.replace(tzinfo=None)
                    if start_dt <= ts <= end_dt:
                        count += 1
                except Exception:
                    pass
        return count
    except FileNotFoundError:
        return 0
    except Exception:
        return 0


# ── internal helpers ───────────────────────────────────────────────────────────

def _ensure_dirs():
    os.makedirs(CLIPS_DIR, exist_ok=True)
    os.makedirs(CYCLES_DIR, exist_ok=True)
    os.makedirs(FLOW_CYCLES_DIR, exist_ok=True)


def _write_pid():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid():
    try:
        os.unlink(PID_FILE)
    except FileNotFoundError:
        pass


def _read_threshold(default: int) -> int:
    try:
        with open(CONFIG_FILE) as f:
            return int(json.load(f).get("threshold", default))
    except Exception:
        return default


def _read_signal() -> tuple[str | None, float]:
    """Return (state, unix_ts) from pressure_signal.json, or (None, 0)."""
    try:
        with open(SIGNAL_FILE) as f:
            sig = json.load(f)
        return sig.get("state"), float(sig.get("ts", 0))
    except Exception:
        return None, 0.0


def _write_detection(ts: datetime, peak: int, rms: int, threshold: int, clip: str):
    record = {"ts": ts.isoformat(), "peak": peak, "rms": rms,
              "threshold": threshold, "clip": clip}
    with open(JSONL_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


def _save_clip(chunks: list, ts: datetime) -> str:
    name = f"click_{ts.strftime('%Y%m%d_%H%M%S_%f')[:-3]}.wav"
    with wave.open(os.path.join(CLIPS_DIR, name), "w") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(BYTES_PER_SAMPLE)
        wf.setframerate(SAMPLE_RATE)
        for chunk in chunks:
            wf.writeframes(chunk)
    return name


def _write_cycle_record(high_ts: datetime, low_ts: datetime | None,
                        duration_s: float, click_count: int,
                        audio_file: str, truncated: bool = False):
    record = {
        "high_ts":     high_ts.isoformat(),
        "low_ts":      low_ts.isoformat() if low_ts else None,
        "duration_s":  round(duration_s, 1),
        "click_count": click_count,
        "audio_file":  audio_file,
        "truncated":   truncated,
    }
    with open(CYCLES_JSONL, "a") as f:
        f.write(json.dumps(record) + "\n")


def _write_flow_record(start_ts: datetime, end_ts: datetime,
                       duration_s: float, audio_file: str, truncated: bool = False):
    record = {
        "start_ts":   start_ts.isoformat(),
        "end_ts":     end_ts.isoformat(),
        "duration_s": round(duration_s, 1),
        "audio_file": audio_file,
        "truncated":  truncated,
    }
    with open(FLOW_CYCLES_JSONL, "a") as f:
        f.write(json.dumps(record) + "\n")


def _on_bypass_flow_complete(start_ts: datetime, end_ts: datetime,
                             duration_s: float, audio_file: str,
                             prev_start_ts: datetime | None, log) -> None:
    """Log a BYPASS_FLOW event to events.csv and optionally send a notification."""
    if not _MONITOR_OK:
        return
    dm, ds = divmod(int(duration_s), 60)
    dur_str = f"{dm}m {ds}s" if dm else f"{ds}s"

    # Duty cycle = flow_duration / (this_start - prev_start)
    duty_str = ""
    if prev_start_ts is not None:
        cycle_period = (start_ts - prev_start_ts).total_seconds()
        if cycle_period > 0:
            duty_pct = duration_s / cycle_period * 100
            duty_str = f", Duty: {duty_pct:.0f}%"

    notes = f"Duration: {dur_str}{duty_str}, Audio: {audio_file}"

    # Log to events.csv — pressure_state/tank data N/A in bypass mode
    try:
        _log_event(
            str(_EVENTS_FILE), "BYPASS_FLOW",
            pressure_state=None, float_state=None,
            tank_gallons=None,   tank_depth=None,   tank_percentage=None,
            estimated_gallons=None,  # TODO: gallons from bypass GPM
            relay_status={"bypass": "ON", "supply_override": ""},
            notes=notes,
        )
    except Exception as exc:
        log.warning("Could not write BYPASS_FLOW event: %s", exc)

    _write_bypass_flow_prediction(log)

    # Send notification if watch file is present
    try:
        if _BYPASS_FLOW_WATCH_FILE and _BYPASS_FLOW_WATCH_FILE.exists():
            _send_notification(
                title=f"Bypass Flow ended ({dur_str}{duty_str})",
                message=f"Water flowed through bypass for {dur_str}{duty_str}",
                priority="default",
                tags=["droplet"],
                click_url=str(_DASHBOARD_URL) if _DASHBOARD_URL else None,
            )
    except Exception as exc:
        log.warning("Could not send BYPASS_FLOW notification: %s", exc)


def _write_bypass_flow_prediction(log) -> None:
    """Predict next bypass flow cycle from flow_cycles.jsonl — same logic as
    the pressure-cycle prediction in poll.py but driven by audio detections."""
    try:
        records = []
        with open(FLOW_CYCLES_JSONL) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    records.append({
                        "start":      datetime.fromisoformat(rec["start_ts"]),
                        "end":        datetime.fromisoformat(rec["end_ts"]),
                        "duration_s": float(rec["duration_s"]),
                    })
                except Exception:
                    pass

        if not records:
            return

        records.sort(key=lambda r: r["start"])
        recent  = records[-21:]     # last 20 intervals max
        last    = recent[-1]
        pred: dict = {
            "last_start_ts": last["start"].isoformat(),
            "last_end_ts":   last["end"].isoformat(),
            "based_on_n":    0,
        }

        if len(recent) >= 2:
            intervals    = [(recent[i+1]["start"] - recent[i]["start"]).total_seconds()
                            for i in range(len(recent) - 1)]
            avg_interval = sum(intervals) / len(intervals)
            avg_duration = sum(r["duration_s"] for r in recent) / len(recent)
            next_start   = last["start"] + timedelta(seconds=avg_interval)
            next_end     = next_start    + timedelta(seconds=avg_duration)
            pred.update({
                "predicted_next_start_ts": next_start.isoformat(),
                "predicted_next_end_ts":   next_end.isoformat(),
                "avg_interval_minutes":    round(avg_interval / 60, 1),
                "avg_duration_s":          round(avg_duration, 1),
                "based_on_n":              len(intervals),
            })

        out = os.path.join(DATA_DIR, "bypass_prediction.json")
        tmp = out + ".tmp"
        with open(tmp, "w") as f:
            json.dump(pred, f)
        os.replace(tmp, out)
    except Exception as exc:
        log.warning("Could not write bypass flow prediction: %s", exc)


def _is_bypass_on() -> bool:
    """Read bypass state directly from GPIO — active-low relay, pin LOW (0) = ON."""
    try:
        result = subprocess.run(
            ["gpio", "-g", "read", str(BYPASS_VALVE_PIN)],
            capture_output=True, text=True, timeout=1,
        )
        return result.returncode == 0 and result.stdout.strip() == "0"
    except Exception:
        return False


def _read_flow_config(default_min_rms: int, default_ratio: float) -> tuple[int, float]:
    """Return (min_rms_gate, ratio_threshold) from config.json."""
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        return (int(cfg.get("flow_threshold", default_min_rms)),
                float(cfg.get("flow_ratio_threshold", default_ratio)))
    except Exception:
        return default_min_rms, default_ratio


def _cleanup_old_files(log):
    now = time.time()
    for directory, retain_days in [(CLIPS_DIR, CLIP_RETAIN_DAYS),
                                   (CYCLES_DIR, CYCLE_RETAIN_DAYS),
                                   (FLOW_CYCLES_DIR, CYCLE_RETAIN_DAYS)]:
        cutoff = now - retain_days * 86400
        removed = 0
        for fn in os.listdir(directory):
            path = os.path.join(directory, fn)
            if os.path.getmtime(path) < cutoff:
                try:
                    os.unlink(path)
                    removed += 1
                except OSError:
                    pass
        if removed:
            log.info("Cleaned up %d file(s) from %s (>%d days)",
                     removed, os.path.basename(directory), retain_days)


def _open_arecord():
    return subprocess.Popen(
        ["arecord", "-D", DEVICE, "-r", str(SAMPLE_RATE),
         "-c", str(CHANNELS), "-f", "S16_LE", "--buffer-size", "8192"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )


# ── cycle recorder ─────────────────────────────────────────────────────────────

class CycleRecorder:
    """Manages a single pressure-cycle WAV recording.

    Lifecycle: IDLE → RECORDING (on PRESSURE_HIGH) → POST_LOW (on PRESSURE_LOW)
               → save & back to IDLE.
    A 60-minute cap forces a save even if pressure never goes LOW.
    """
    IDLE       = "idle"
    RECORDING  = "recording"
    POST_LOW   = "post_low"

    _MAX_CHUNKS    = int(MAX_CYCLE_SECS  * SAMPLE_RATE / CHUNK_SAMPLES)
    _POST_CHUNKS   = int(POST_LOW_SECS   * SAMPLE_RATE / CHUNK_SAMPLES)

    def __init__(self):
        self.state        = self.IDLE
        self.high_ts: datetime | None = None
        self.low_ts:  datetime | None = None
        self._wav:    wave.Wave_write | None = None
        self._tmp:    str | None = None
        self._fname:  str | None = None
        self.click_count = 0
        self._chunks     = 0
        self._post_remain = 0

    @property
    def active(self) -> bool:
        return self.state != self.IDLE

    def on_pressure_high(self, ts: datetime, log) -> None:
        if self.state != self.IDLE:
            return
        self.high_ts     = ts
        self.low_ts      = None
        self.click_count = 0
        self._chunks     = 0
        self._fname      = f"cycle_{ts.strftime('%Y%m%d_%H%M%S')}.wav"
        self._tmp        = os.path.join(CYCLES_DIR, self._fname + ".tmp")
        self._wav        = wave.open(self._tmp, "w")
        self._wav.setnchannels(CHANNELS)
        self._wav.setsampwidth(BYTES_PER_SAMPLE)
        self._wav.setframerate(SAMPLE_RATE)
        self.state = self.RECORDING
        log.info("CYCLE recording started: %s", self._fname)

    def on_pressure_low(self, ts: datetime, log) -> None:
        if self.state != self.RECORDING:
            return
        self.low_ts       = ts
        self._post_remain = self._POST_CHUNKS
        self.state        = self.POST_LOW
        log.info("CYCLE post-roll started (%.0f s after HIGH)", ts.timestamp() - self.high_ts.timestamp())  # noqa: E501

    def write(self, raw: bytes) -> bool:
        """Write one audio chunk.  Returns True to keep going, False if max reached."""
        if not self.active:
            return True
        self._wav.writeframes(raw)
        self._chunks += 1
        if self._chunks >= self._MAX_CHUNKS:
            return False
        return True

    def tick_post_low(self) -> bool:
        """Call each chunk during POST_LOW.  Returns True when done."""
        self._post_remain -= 1
        return self._post_remain <= 0

    def increment_click(self) -> None:
        if self.active:
            self.click_count += 1

    def finish(self, log, truncated: bool = False) -> str | None:
        """Close WAV, move to final path, write cycle record."""
        if self._wav:
            self._wav.close()
            self._wav = None
        if not self._tmp or not os.path.exists(self._tmp):
            self.state = self.IDLE
            return None
        final = os.path.join(CYCLES_DIR, self._fname)
        os.rename(self._tmp, final)
        duration_s = self._chunks * CHUNK_SAMPLES / SAMPLE_RATE
        _write_cycle_record(self.high_ts, self.low_ts, duration_s,
                            self.click_count, self._fname, truncated)
        log.info("CYCLE saved: %s  (%.0f s, %d clicks%s)",
                 self._fname, duration_s, self.click_count,
                 ", TRUNCATED" if truncated else "")
        self.state = self.IDLE
        self._tmp  = None
        return self._fname


# ── flow recorder (bypass mode) ────────────────────────────────────────────────

class FlowRecorder:
    """Detects and records water flow during bypass mode.

    Uses FFT spectral scoring rather than raw RMS.  The bypass water makes a
    hollow resonance around 190–210 Hz.  We score each 1-second window as:

        score = energy(100–400 Hz) / energy(1000–4000 Hz)

    Calibrated from a real bypass event:
        quiet ambient → score ≈ 12
        water flowing → score ≈ 39   (DEFAULT_FLOW_RATIO = 22 sits between them)

    A minimum RMS gate (DEFAULT_FLOW_THRESHOLD) prevents spurious FFT scores
    during near-silence.
    """
    IDLE    = "idle"
    FLOWING = "flowing"

    _FFT_N   = int(FLOW_FFT_SECS  * SAMPLE_RATE)  # samples per FFT window
    _START_N = int(FLOW_START_SECS * SAMPLE_RATE / CHUNK_SAMPLES)
    _STOP_N  = int(FLOW_STOP_SECS  * SAMPLE_RATE / CHUNK_SAMPLES)
    _MIN_N   = int(FLOW_MIN_SECS   * SAMPLE_RATE / CHUNK_SAMPLES)
    _MAX_N   = int(MAX_CYCLE_SECS  * SAMPLE_RATE / CHUNK_SAMPLES)

    # Precompute frequency bin indices once (class-level)
    _FREQS     = np.fft.rfftfreq(_FFT_N, 1 / SAMPLE_RATE)
    _SIG_MASK  = (_FREQS >= FLOW_SIGNAL_BAND[0]) & (_FREQS < FLOW_SIGNAL_BAND[1])
    _REF_MASK  = (_FREQS >= FLOW_REF_BAND[0])    & (_FREQS < FLOW_REF_BAND[1])
    _HANN      = np.hanning(_FFT_N).astype(np.float32)

    def __init__(self):
        self.state    = self.IDLE
        self.start_ts: datetime | None = None
        self._wav:    wave.Wave_write | None = None
        self._tmp:    str | None = None
        self._fname:  str | None = None
        self._chunks  = 0
        self._above_n = 0
        self._below_n = 0
        self._fft_buf: list[np.ndarray] = []   # accumulates samples for FFT
        # Set by _finish() when a recording is saved; caller must clear after reading.
        self.last_finished: dict | None = None

    def _score(self, min_rms: int) -> float | None:
        """Return FFT ratio score, or None if accumulated samples < 1 window."""
        total = sum(len(b) for b in self._fft_buf)
        if total < self._FFT_N:
            return None
        # flatten and take exactly _FFT_N samples from the tail
        flat = np.concatenate(self._fft_buf)[-self._FFT_N:]
        rms  = float(np.sqrt(np.mean(flat ** 2)) * 32768)
        if rms < min_rms:
            return 0.0   # near-silence — treat as not-flowing
        fft  = np.abs(np.fft.rfft(flat / 32768.0 * self._HANN))
        sig  = fft[self._SIG_MASK].mean()
        ref  = fft[self._REF_MASK].mean() + 1e-9
        return float(sig / ref)

    def feed(self, raw: bytes, rms: int, min_rms: int, ratio_thresh: float, log) -> None:
        """Feed one audio chunk.  min_rms gates near-silence; ratio_thresh triggers flow."""
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        self._fft_buf.append(samples)
        # keep only the last _FFT_N samples worth of history
        while sum(len(b) for b in self._fft_buf) > self._FFT_N * 2:
            self._fft_buf.pop(0)

        score = self._score(min_rms)
        above = (score is not None) and (score >= ratio_thresh)

        if self.state == self.IDLE:
            if above:
                self._above_n += 1
                if self._above_n >= self._START_N:
                    self._start(log)
            else:
                self._above_n = max(0, self._above_n - 1)  # gradual decay
        elif self.state == self.FLOWING:
            if self._wav:
                self._wav.writeframes(raw)
            self._chunks += 1
            if above:
                self._below_n = 0
            else:
                self._below_n += 1
                if self._below_n >= self._STOP_N:
                    self._finish(log)
                    return
            if self._chunks >= self._MAX_N:
                self._finish(log, truncated=True)

    def _start(self, log) -> None:
        self.state    = self.FLOWING
        self.start_ts = datetime.now()
        self._chunks  = 0
        self._below_n = 0
        self._fname   = f"flow_{self.start_ts.strftime('%Y%m%d_%H%M%S')}.wav"
        self._tmp     = os.path.join(FLOW_CYCLES_DIR, self._fname + ".tmp")
        self._wav     = wave.open(self._tmp, "w")
        self._wav.setnchannels(CHANNELS)
        self._wav.setsampwidth(BYTES_PER_SAMPLE)
        self._wav.setframerate(SAMPLE_RATE)
        log.info("FLOW recording started: %s", self._fname)

    def _finish(self, log, truncated: bool = False) -> None:
        end_ts = datetime.now()
        if self._wav:
            self._wav.close()
            self._wav = None
        duration_s = self._chunks * CHUNK_SAMPLES / SAMPLE_RATE
        if self._chunks < self._MIN_N:
            if self._tmp and os.path.exists(self._tmp):
                os.unlink(self._tmp)
            log.info("FLOW discarded (%.0fs < %.0fs min)", duration_s, FLOW_MIN_SECS)
            self.last_finished = None
        else:
            final = os.path.join(FLOW_CYCLES_DIR, self._fname)
            if self._tmp and os.path.exists(self._tmp):
                os.rename(self._tmp, final)
            _write_flow_record(self.start_ts, end_ts, duration_s, self._fname, truncated)
            log.info("FLOW saved: %s (%.0fs%s)", self._fname, duration_s,
                     ", TRUNCATED" if truncated else "")
            self.last_finished = {
                "start_ts":  self.start_ts,
                "end_ts":    end_ts,
                "duration_s": duration_s,
                "audio_file": self._fname,
            }
        self.state    = self.IDLE
        self._above_n = 0
        self._below_n = 0
        self._tmp     = None

    def cancel(self, log) -> None:
        """Cancel an in-progress recording (bypass turned off mid-flow)."""
        if self.state == self.FLOWING:
            if self._wav:
                self._wav.close()
                self._wav = None
            if self._tmp and os.path.exists(self._tmp):
                os.unlink(self._tmp)
            log.info("FLOW recording cancelled (bypass off)")
        self.state    = self.IDLE
        self._above_n = 0
        self._below_n = 0
        self._tmp     = None


# ── calibration ────────────────────────────────────────────────────────────────

def calibrate():
    print(f"Calibrating 10 s on {DEVICE}")
    print(f"{'Time':>8}  {'Peak':>6}  {'RMS':>6}  bar")
    proc = _open_arecord()
    t_end = time.monotonic() + 10.0
    try:
        while time.monotonic() < t_end:
            raw = proc.stdout.read(CHUNK_BYTES)
            if not raw or len(raw) < CHUNK_BYTES:
                break
            s    = np.frombuffer(raw, dtype=np.int16)
            peak = int(np.max(np.abs(s)))
            rms  = int(np.sqrt(np.mean(s.astype(np.float32) ** 2)))
            bar  = "#" * min(50, peak // 200)
            print(f"{10 - (t_end - time.monotonic()):>7.1f}s  {peak:>6}  {rms:>6}  {bar}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
    print("\nSet --threshold to ~3–5× your ambient peak.")


# ── main listener ──────────────────────────────────────────────────────────────

def _setup_logging():
    log_file = os.path.join(DATA_DIR, "detections.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )


def listen(cli_threshold: int):
    log = logging.getLogger(__name__)
    _write_pid()

    pre_roll_n  = int(PRE_ROLL_SECS  * SAMPLE_RATE / CHUNK_SAMPLES) + 1
    post_roll_n = int(POST_ROLL_SECS * SAMPLE_RATE / CHUNK_SAMPLES) + 1

    threshold      = _read_threshold(cli_threshold)
    last_cfg_check = time.monotonic()
    last_cleanup   = time.monotonic()

    log.info("=== Dosatron listener started (PID %d) ===", os.getpid())
    log.info("Device: %s  threshold: %d  clip: %.0fs+%.0fs  cycle max: %.0f min",
             DEVICE, threshold, PRE_ROLL_SECS, POST_ROLL_SECS, MAX_CYCLE_SECS / 60)

    proc = _open_arecord()

    # per-click state
    pre_roll         = deque(maxlen=pre_roll_n)
    last_detection   = 0.0
    post_roll_remain = 0
    detect_snapshot  = []
    post_roll_buf    = []
    detect_ts        = None

    # cycle recorder (normal mode)
    cycle_rec         = CycleRecorder()
    signal_counter    = 0
    last_signal_state: str | None = None

    # flow recorder (bypass mode)
    flow_rec              = FlowRecorder()
    flow_min_rms, flow_ratio = _read_flow_config(DEFAULT_FLOW_THRESHOLD, DEFAULT_FLOW_RATIO)
    bypass_active         = False
    relay_counter         = 0
    _RELAY_POLL_N         = CHUNKS_PER_SEC * RELAY_POLL_SECS
    last_bypass_start_ts: datetime | None = None   # for duty cycle calculation

    def _handle_signal(sig, frame):
        log.info("Signal %d — shutting down", sig)
        if cycle_rec.active:
            cycle_rec.finish(log, truncated=True)
        if flow_rec.state == FlowRecorder.FLOWING:
            flow_rec.cancel(log)
        proc.terminate()
        _remove_pid()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    # drain startup transient (~1 s)
    warmup = int(1.0 * SAMPLE_RATE / CHUNK_SAMPLES)
    log.info("Warming up (%d chunks)…", warmup)
    for _ in range(warmup):
        proc.stdout.read(CHUNK_BYTES)
    log.info("Ready")

    # read initial signal state so we don't falsely trigger on startup
    sig_state, sig_ts = _read_signal()
    last_signal_state = sig_state

    try:
        while True:
            raw = proc.stdout.read(CHUNK_BYTES)
            if not raw or len(raw) < CHUNK_BYTES:
                log.error("arecord stream ended")
                break

            s    = np.frombuffer(raw, dtype=np.int16)
            peak = int(np.max(np.abs(s)))
            rms  = int(np.sqrt(np.mean(s.astype(np.float32) ** 2)))
            now  = time.monotonic()

            # config / cleanup polls (every 30 s)
            if now - last_cfg_check > 30:
                new_t = _read_threshold(threshold)
                if new_t != threshold:
                    log.info("Threshold %d → %d", threshold, new_t)
                    threshold = new_t
                new_min, new_ratio = _read_flow_config(flow_min_rms, flow_ratio)
                if new_min != flow_min_rms or new_ratio != flow_ratio:
                    log.info("Flow config updated: min_rms %d→%d  ratio %.1f→%.1f",
                             flow_min_rms, new_min, flow_ratio, new_ratio)
                    flow_min_rms, flow_ratio = new_min, new_ratio
                last_cfg_check = now
            if now - last_cleanup > 86400:
                _cleanup_old_files(log)
                last_cleanup = now

            # ── bypass state poll (every ~5 s) ─────────────────────────────
            relay_counter += 1
            if relay_counter >= _RELAY_POLL_N:
                relay_counter = 0
                new_bypass = _is_bypass_on()
                if new_bypass != bypass_active:
                    bypass_active = new_bypass
                    if bypass_active:
                        log.info("Bypass ON — switching to flow detection mode")
                    else:
                        log.info("Bypass OFF — returning to click detection mode")
                        flow_rec.cancel(log)

            # ── pressure signal poll (every ~1 s, normal mode only) ────────
            signal_counter += 1
            if signal_counter >= CHUNKS_PER_SEC:
                signal_counter = 0
                if not bypass_active:
                    new_state, new_ts = _read_signal()
                    if new_state != last_signal_state:
                        last_signal_state = new_state
                        ts_dt = datetime.fromtimestamp(new_ts) if new_ts else datetime.now()
                        if new_state == "HIGH":
                            cycle_rec.on_pressure_high(ts_dt, log)
                        elif new_state == "LOW":
                            cycle_rec.on_pressure_low(ts_dt, log)

            # ── cycle recording (normal mode) ──────────────────────────────
            pre_roll.append(raw)
            if not bypass_active and cycle_rec.active:
                keep_going = cycle_rec.write(raw)
                if not keep_going:
                    cycle_rec.finish(log, truncated=True)
                elif cycle_rec.state == CycleRecorder.POST_LOW:
                    if cycle_rec.tick_post_low():
                        cycle_rec.finish(log)

            # ── flow detection (bypass mode) ───────────────────────────────
            if bypass_active:
                flow_rec.feed(raw, rms, flow_min_rms, flow_ratio, log)
                if flow_rec.last_finished:
                    info = flow_rec.last_finished
                    flow_rec.last_finished = None
                    _on_bypass_flow_complete(
                        info["start_ts"], info["end_ts"],
                        info["duration_s"], info["audio_file"],
                        last_bypass_start_ts, log,
                    )
                    last_bypass_start_ts = info["start_ts"]

            # ── per-click detection (normal mode individual clips) ─────────
            elif post_roll_remain > 0:
                post_roll_buf.append(raw)
                post_roll_remain -= 1
                _extend = peak > threshold and (now - last_detection) > DEBOUNCE_SECS
                if _extend:
                    last_detection = now
                    log.info("CLICK (extended) | peak=%d rms=%d", peak, rms)
                    _write_detection(datetime.now(), peak, rms, threshold, "")
                    cycle_rec.increment_click()
                    post_roll_remain = post_roll_n
                if post_roll_remain == 0:
                    clip = _save_clip(detect_snapshot + post_roll_buf, detect_ts)
                    log.info("  clip: %s", clip)
                    _backfill_clip_name(clip)
                    post_roll_buf = []

            elif peak > threshold and (now - last_detection) > DEBOUNCE_SECS:
                last_detection = now
                detect_ts      = datetime.now()
                log.info("CLICK | peak=%d rms=%d threshold=%d", peak, rms, threshold)
                _write_detection(detect_ts, peak, rms, threshold, "")
                cycle_rec.increment_click()
                detect_snapshot  = list(pre_roll)
                post_roll_remain = post_roll_n
                post_roll_buf    = []

    finally:
        if cycle_rec.active:
            cycle_rec.finish(log, truncated=True)
        proc.terminate()
        _remove_pid()
        log.info("=== Dosatron listener stopped ===")


def _backfill_clip_name(clip_name: str):
    """Patch the empty clip field in the most recent detections.jsonl record."""
    try:
        with open(JSONL_FILE, "rb+") as f:
            f.seek(0, 2)
            pos = f.tell() - 2
            while pos > 0:
                f.seek(pos)
                if f.read(1) == b"\n":
                    break
                pos -= 1
            line_start = pos + 1 if pos > 0 else 0
            f.seek(line_start)
            line = f.read().rstrip(b"\n")
            rec = json.loads(line)
            if rec.get("clip") == "":
                rec["clip"] = clip_name
                new_line = (json.dumps(rec) + "\n").encode()
                f.seek(line_start)
                f.write(new_line)
                f.truncate(line_start + len(new_line))
    except Exception:
        pass


# ── entry point ────────────────────────────────────────────────────────────────

def main():
    _ensure_dirs()
    parser = argparse.ArgumentParser(description="Dosatron click detector")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()

    if args.calibrate:
        calibrate()
    else:
        _setup_logging()
        listen(args.threshold)


if __name__ == "__main__":
    main()
