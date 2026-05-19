#!/usr/bin/env python3
"""
Detect Dosatron clicks via USB microphone using amplitude threshold detection.
Logs each detection and saves a short WAV clip (pre-roll + post-roll) for review.

Background usage:
    nohup python3 dosatron_listener.py > /dev/null 2>&1 &
    tail -f ~/src/pumphouse/logs/dosatron/detections.log

Calibration (see ambient noise levels before picking a threshold):
    python3 dosatron_listener.py --calibrate

Adjust threshold if too many false positives or misses:
    python3 dosatron_listener.py --threshold 2000
"""

import argparse
import logging
import os
import subprocess
import time
import wave
from collections import deque
from datetime import datetime

import numpy as np

# Audio capture settings
SAMPLE_RATE = 44100
CHANNELS = 1
BYTES_PER_SAMPLE = 2          # 16-bit PCM
CHUNK_SAMPLES = 512           # ~12ms per chunk at 44100 Hz
CHUNK_BYTES = CHUNK_SAMPLES * CHANNELS * BYTES_PER_SAMPLE

# Detection settings
DEFAULT_THRESHOLD = 3000      # peak abs amplitude (0–32767); adjust via --threshold
DEBOUNCE_SECS = 2.0           # ignore detections within this window after a click
PRE_ROLL_SECS = 0.5           # audio to save before the detected click
POST_ROLL_SECS = 1.5          # audio to save after the detected click

DEVICE = "hw:3,0"             # USB PnP Sound Device (card 3)

LOG_DIR = os.path.expanduser("~/.local/share/pumphouse/dosatron")
AUDIO_DIR = os.path.join(LOG_DIR, "clips")
LOG_FILE = os.path.join(LOG_DIR, "detections.log")


def setup_logging():
    os.makedirs(AUDIO_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(),
        ],
    )


def save_clip(chunks, ts):
    fname = os.path.join(AUDIO_DIR, f"click_{ts.strftime('%Y%m%d_%H%M%S_%f')[:-3]}.wav")
    with wave.open(fname, "w") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(BYTES_PER_SAMPLE)
        wf.setframerate(SAMPLE_RATE)
        for chunk in chunks:
            wf.writeframes(chunk)
    return fname


def open_arecord():
    cmd = [
        "arecord",
        "-D", DEVICE,
        "-r", str(SAMPLE_RATE),
        "-c", str(CHANNELS),
        "-f", "S16_LE",
        "--buffer-size", "8192",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def calibrate():
    """Print peak and RMS for 10 seconds so you can pick a threshold."""
    print(f"Calibrating for 10 seconds on {DEVICE}...")
    print(f"{'Time':>8}  {'Peak':>6}  {'RMS':>6}  bar")
    proc = open_arecord()
    t_end = time.monotonic() + 10.0
    try:
        while time.monotonic() < t_end:
            raw = proc.stdout.read(CHUNK_BYTES)
            if not raw or len(raw) < CHUNK_BYTES:
                break
            samples = np.frombuffer(raw, dtype=np.int16)
            peak = int(np.max(np.abs(samples)))
            rms = int(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
            bar = "#" * min(50, peak // 200)
            remaining = t_end - time.monotonic()
            print(f"{10 - remaining:>7.1f}s  {peak:>6}  {rms:>6}  {bar}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
    print("\nAmbient peak values above are your noise floor.")
    print("Set --threshold to ~3-5x your ambient peak to catch clicks without false positives.")


def listen(threshold):
    log = logging.getLogger(__name__)

    pre_roll_n = int(PRE_ROLL_SECS * SAMPLE_RATE / CHUNK_SAMPLES) + 1
    post_roll_n = int(POST_ROLL_SECS * SAMPLE_RATE / CHUNK_SAMPLES) + 1

    log.info("=== Dosatron listener started ===")
    log.info("Device: %s  threshold: %d  debounce: %.1fs", DEVICE, threshold, DEBOUNCE_SECS)
    log.info("Clips: pre=%.1fs post=%.1fs  saved to %s", PRE_ROLL_SECS, POST_ROLL_SECS, AUDIO_DIR)

    proc = open_arecord()
    pre_roll = deque(maxlen=pre_roll_n)   # rolling buffer of raw PCM chunks
    last_detection = 0.0
    post_roll_remaining = 0
    detection_snapshot = []               # pre_roll chunks captured at detection moment
    post_roll_buffer = []
    detection_ts = None

    # Drain startup transient: arecord often produces a large spike on init
    warmup_chunks = int(1.0 * SAMPLE_RATE / CHUNK_SAMPLES)
    log.info("Warming up for 1s to skip arecord startup transient...")

    try:
        for _ in range(warmup_chunks):
            proc.stdout.read(CHUNK_BYTES)
        log.info("Ready — listening for clicks")

        while True:
            raw = proc.stdout.read(CHUNK_BYTES)
            if not raw or len(raw) < CHUNK_BYTES:
                log.error("arecord stream ended unexpectedly")
                break

            samples = np.frombuffer(raw, dtype=np.int16)
            peak = int(np.max(np.abs(samples)))
            rms = int(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
            now = time.monotonic()

            # Always keep the rolling pre-roll buffer current
            pre_roll.append(raw)

            if post_roll_remaining > 0:
                post_roll_buffer.append(raw)
                post_roll_remaining -= 1
                if post_roll_remaining == 0:
                    all_chunks = detection_snapshot + post_roll_buffer
                    clip_path = save_clip(all_chunks, detection_ts)
                    log.info("  clip saved: %s", os.path.basename(clip_path))
                    post_roll_buffer = []
            elif peak > threshold and (now - last_detection) > DEBOUNCE_SECS:
                last_detection = now
                detection_ts = datetime.now()
                log.info(
                    "CLICK | peak=%d  rms=%d",
                    peak, rms,
                )
                detection_snapshot = list(pre_roll)
                post_roll_remaining = post_roll_n
                post_roll_buffer = []

    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        proc.terminate()
        log.info("=== Dosatron listener stopped ===")


def main():
    parser = argparse.ArgumentParser(description="Detect Dosatron clicks via USB mic")
    parser.add_argument("--calibrate", action="store_true",
                        help="Run 10s calibration to observe ambient noise levels")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help=f"Peak amplitude threshold 0-32767 (default {DEFAULT_THRESHOLD})")
    args = parser.parse_args()

    if args.calibrate:
        calibrate()
    else:
        setup_logging()
        listen(args.threshold)


if __name__ == "__main__":
    main()
