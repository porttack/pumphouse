"""
Dosatron click detector — Flask blueprint.
Mounted at /dosatron and /api/dosatron/*.
"""

import json
import os
import signal
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request, abort, render_template, Response

try:
    from monitor.dosatron import GALLONS_PER_CLICK, DEFAULT_FLOW_THRESHOLD
except Exception:
    GALLONS_PER_CLICK = 0.07
    DEFAULT_FLOW_THRESHOLD = 400

dosatron_bp = Blueprint("dosatron", __name__)

DATA_DIR          = os.path.expanduser("~/.local/share/pumphouse/dosatron")
CLIPS_DIR         = os.path.join(DATA_DIR, "clips")
CYCLES_DIR        = os.path.join(DATA_DIR, "cycles")
FLOW_CYCLES_DIR   = os.path.join(DATA_DIR, "flow_cycles")
JSONL_FILE        = os.path.join(DATA_DIR, "detections.jsonl")
CYCLES_JSONL      = os.path.join(DATA_DIR, "cycles.jsonl")
FLOW_CYCLES_JSONL = os.path.join(DATA_DIR, "flow_cycles.jsonl")
CYCLE_LABELS      = os.path.join(DATA_DIR, "cycle_labels.json")
FLOW_CYCLE_LABELS = os.path.join(DATA_DIR, "flow_cycle_labels.json")
LABELS_FILE       = os.path.join(DATA_DIR, "labels.json")
CONFIG_FILE       = os.path.join(DATA_DIR, "config.json")
PID_FILE          = os.path.join(DATA_DIR, "listener.pid")
PREDICTION_FILE   = os.path.join(DATA_DIR, "prediction.json")
LIVE_PIDS_FILE    = os.path.join(DATA_DIR, "live_pids.json")


# ── helpers ───────────────────────────────────────────────────────────────────

def _listener_running() -> bool:
    try:
        pid = int(Path(PID_FILE).read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _read_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {"threshold": 2000}


def _write_config(cfg: dict):
    _atomic_write(CONFIG_FILE, cfg)


def _read_labels(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_labels(path: str, labels: dict):
    _atomic_write(path, labels)


def _serve_audio(path: str) -> Response:
    """Serve a WAV file with proper byte-range support for browser audio players.

    HTML5 <audio> elements require Range request support to seek within a file
    and to progressively buffer.  Flask's send_file may not handle all Range
    edge cases with gunicorn, so we implement it explicitly here.
    """
    if not os.path.exists(path):
        abort(404)
    file_size = os.path.getsize(path)
    range_header = request.headers.get("Range")

    if range_header:
        # Parse "bytes=start-end" (end is optional)
        try:
            byte_spec = range_header.strip().replace("bytes=", "")
            raw_start, raw_end = byte_spec.split("-")
            start = int(raw_start) if raw_start else 0
            end   = int(raw_end)   if raw_end   else file_size - 1
        except Exception:
            abort(416)
        start = max(0, start)
        end   = min(end, file_size - 1)
        if start > end:
            abort(416)
        length = end - start + 1

        def _stream():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        resp = Response(_stream(), status=206, mimetype="audio/wav")
        resp.headers["Content-Range"]  = f"bytes {start}-{end}/{file_size}"
        resp.headers["Content-Length"] = str(length)
    else:
        def _stream_full():
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk

        resp = Response(_stream_full(), status=200, mimetype="audio/wav")
        resp.headers["Content-Length"] = str(file_size)

    resp.headers["Accept-Ranges"] = "bytes"
    return resp


def _atomic_write(path: str, data: dict):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _read_detections(hours: int = 24) -> list:
    cutoff = datetime.now() - timedelta(hours=hours)
    rows = []
    try:
        with open(JSONL_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["ts"])
                    if ts.tzinfo is not None:
                        ts = ts.replace(tzinfo=None)
                    if ts >= cutoff:
                        rows.append(rec)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return rows


def _read_cycles(hours: int = 24) -> list:
    cutoff = datetime.now() - timedelta(hours=hours)
    rows = []
    try:
        with open(CYCLES_JSONL) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["high_ts"])
                    if ts.tzinfo is not None:
                        ts = ts.replace(tzinfo=None)
                    if ts >= cutoff:
                        rows.append(rec)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return rows


def _read_flow_cycles(hours: int = 24) -> list:
    cutoff = datetime.now() - timedelta(hours=hours)
    rows = []
    try:
        with open(FLOW_CYCLES_JSONL) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["start_ts"])
                    if ts.tzinfo is not None:
                        ts = ts.replace(tzinfo=None)
                    if ts >= cutoff:
                        rows.append(rec)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return rows


def _fmt_ts(iso: str | None) -> str:
    """Format an ISO timestamp as a human-friendly string for the dashboard."""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        now = datetime.now()
        if dt.date() == now.date():
            return "Today " + dt.strftime("%-I:%M %p")
        elif dt.date() == (now - timedelta(days=1)).date():
            return "Yesterday " + dt.strftime("%-I:%M %p")
        else:
            return dt.strftime("%a %-I:%M %p")
    except Exception:
        return iso


# ── page ──────────────────────────────────────────────────────────────────────

@dosatron_bp.route("/dosatron")
def dosatron_page():
    return render_template("dosatron.html")


# ── status / config ───────────────────────────────────────────────────────────

@dosatron_bp.route("/api/dosatron/status")
def api_status():
    cfg = _read_config()
    return jsonify({
        "running":        _listener_running(),
        "threshold":      cfg.get("threshold", 2000),
        "flow_threshold":       cfg.get("flow_threshold", DEFAULT_FLOW_THRESHOLD),
        "flow_ratio_threshold": cfg.get("flow_ratio_threshold", 22.0),
    })




# ── cycles ────────────────────────────────────────────────────────────────────

@dosatron_bp.route("/api/dosatron/cycles")
def api_cycles():
    hours        = int(request.args.get("hours", 24))
    cycle_labels = _read_labels(CYCLE_LABELS)
    rows         = _read_cycles(hours=max(hours, 48))  # need extra history for duty calc
    result       = []

    # Sort by high_ts ascending so we can compute cycle period from consecutive highs
    sorted_rows = sorted(rows, key=lambda r: r.get("high_ts", ""))

    # Load avg interval from prediction for the last cycle's duty
    avg_interval_s = None
    try:
        with open(PREDICTION_FILE) as f:
            avg_interval_s = json.load(f).get("avg_interval_minutes", None)
        if avg_interval_s is not None:
            avg_interval_s *= 60
    except Exception:
        pass

    for i, rec in enumerate(sorted_rows):
        # Only return cycles within the requested hours window
        try:
            high_dt = datetime.fromisoformat(rec["high_ts"])
            if (datetime.now() - high_dt).total_seconds() > hours * 3600:
                continue
        except Exception:
            pass

        fn     = rec.get("audio_file", "")
        exists = bool(fn) and os.path.exists(os.path.join(CYCLES_DIR, fn))
        clicks = rec.get("click_count", 0)

        # High duration = low_ts - high_ts (not counting post-roll)
        high_dur_s = None
        duty_pct   = None
        try:
            h = datetime.fromisoformat(rec["high_ts"])
            l = datetime.fromisoformat(rec["low_ts"])
            high_dur_s = (l - h).total_seconds()
            # Cycle period = next cycle's high_ts - this high_ts
            if i + 1 < len(sorted_rows) and sorted_rows[i + 1].get("high_ts"):
                nxt = datetime.fromisoformat(sorted_rows[i + 1]["high_ts"])
                period_s = (nxt - h).total_seconds()
            elif avg_interval_s:
                period_s = avg_interval_s
            else:
                period_s = None
            if period_s and period_s > 0:
                duty_pct = round(high_dur_s / period_s * 100, 1)
        except Exception:
            pass

        result.append({
            "high_ts":       rec.get("high_ts"),
            "low_ts":        rec.get("low_ts"),
            "duration_s":    rec.get("duration_s"),
            "high_duration_s": round(high_dur_s, 1) if high_dur_s else None,
            "duty_pct":      duty_pct,
            "click_count":   clicks,
            "gallons":       round(clicks * GALLONS_PER_CLICK, 2),
            "audio_file":    fn if exists else None,
            "truncated":     rec.get("truncated", False),
            "manual_count":  cycle_labels.get(fn),
        })

    result.reverse()   # newest first
    return jsonify({"cycles": result})


@dosatron_bp.route("/api/dosatron/cycles/<path:filename>")
def api_serve_cycle(filename):
    if "/" in filename or ".." in filename:
        abort(400)
    return _serve_audio(os.path.join(CYCLES_DIR, filename))


@dosatron_bp.route("/api/dosatron/cycles/<path:filename>/manual_count", methods=["POST"])
def api_cycle_manual_count(filename):
    if "/" in filename or ".." in filename:
        abort(400)
    data  = request.get_json(force=True)
    count = data.get("count")
    labels = _read_labels(CYCLE_LABELS)
    if count is None:
        labels.pop(filename, None)
    else:
        labels[filename] = int(count)
    _write_labels(CYCLE_LABELS, labels)
    return jsonify({"ok": True, "filename": filename, "manual_count": count})


# ── individual detections (calibration) ──────────────────────────────────────

@dosatron_bp.route("/api/dosatron/detections")
def api_detections():
    hours  = int(request.args.get("hours", 24))
    labels = _read_labels(LABELS_FILE)
    rows   = _read_detections(hours)
    result = []
    for rec in reversed(rows):
        clip = rec.get("clip", "")
        exists = bool(clip) and os.path.exists(os.path.join(CLIPS_DIR, clip))
        result.append({
            "ts":        rec.get("ts"),
            "peak":      rec.get("peak"),
            "rms":       rec.get("rms"),
            "threshold": rec.get("threshold"),
            "clip":      clip if exists else None,
            "label":     labels.get(clip) if clip else None,
        })
    total   = len(result)
    labeled = sum(1 for r in result if r["label"] is not None)
    clicks  = sum(1 for r in result if r["label"] == "click")
    noise   = sum(1 for r in result if r["label"] == "noise")
    return jsonify({
        "detections": result,
        "summary": {"total": total, "labeled": labeled, "clicks": clicks, "noise": noise},
    })


@dosatron_bp.route("/api/dosatron/clips/<path:filename>")
def api_serve_clip(filename):
    if "/" in filename or ".." in filename:
        abort(400)
    return _serve_audio(os.path.join(CLIPS_DIR, filename))


@dosatron_bp.route("/api/dosatron/label/<path:filename>", methods=["POST"])
def api_label(filename):
    if "/" in filename or ".." in filename:
        abort(400)
    data  = request.get_json(force=True)
    label = data.get("label")
    if label not in ("click", "noise", None):
        abort(400, "label must be 'click', 'noise', or null")
    labels = _read_labels(LABELS_FILE)
    if label is None:
        labels.pop(filename, None)
    else:
        labels[filename] = label
    _write_labels(LABELS_FILE, labels)
    return jsonify({"ok": True, "filename": filename, "label": label})


# ── prediction ────────────────────────────────────────────────────────────────

BYPASS_PREDICTION_FILE = os.path.join(DATA_DIR, "bypass_prediction.json")
RELAY_STATE_FILE       = os.path.join(os.path.expanduser("~/src/pumphouse"), "relay_state.json")


def _bypass_is_on() -> bool:
    try:
        with open(RELAY_STATE_FILE) as f:
            return json.load(f).get("bypass", "OFF").upper() == "ON"
    except Exception:
        return False


@dosatron_bp.route("/api/dosatron/prediction")
def api_prediction():
    bypass = _bypass_is_on()

    if bypass:
        # ── bypass mode: use flow_cycles.jsonl based prediction ───────────────
        try:
            with open(BYPASS_PREDICTION_FILE) as f:
                bp = json.load(f)
            return jsonify({
                "mode":             "bypass",
                "last_low":         _fmt_ts(bp.get("last_end_ts")),
                "next_high":        _fmt_ts(bp.get("predicted_next_start_ts")),
                "next_low":         _fmt_ts(bp.get("predicted_next_end_ts")),
                "next_high_iso":    bp.get("predicted_next_start_ts"),
                "next_low_iso":     bp.get("predicted_next_end_ts"),
                "avg_interval_min": bp.get("avg_interval_minutes"),
                "avg_high_s":       bp.get("avg_duration_s"),
                "based_on_n":       bp.get("based_on_n", 0),
            })
        except FileNotFoundError:
            return jsonify({"mode": "bypass", "last_low": None, "next_high": None,
                            "next_low": None, "based_on_n": 0})
        except Exception:
            return jsonify({"mode": "bypass", "last_low": None, "next_high": None,
                            "next_low": None})

    # ── normal mode: pressure-cycle prediction written by poll.py ─────────────
    try:
        with open(PREDICTION_FILE) as f:
            data = json.load(f)

        next_low_iso = None
        avg_high_s   = None
        try:
            recent = _read_cycles(hours=72)
            durs = []
            for c in recent:
                if c.get("high_ts") and c.get("low_ts"):
                    h = datetime.fromisoformat(c["high_ts"])
                    l = datetime.fromisoformat(c["low_ts"])
                    durs.append((l - h).total_seconds())
            if durs:
                avg_high_s    = sum(durs) / len(durs)
                next_high_iso = data.get("predicted_next_high_ts")
                if next_high_iso:
                    next_low_iso = (datetime.fromisoformat(next_high_iso)
                                    + timedelta(seconds=avg_high_s)).isoformat()
        except Exception:
            pass

        return jsonify({
            "mode":             "pressure",
            "last_low":         _fmt_ts(data.get("last_low_ts")),
            "next_high":        _fmt_ts(data.get("predicted_next_high_ts")),
            "next_low":         _fmt_ts(next_low_iso),
            "next_high_iso":    data.get("predicted_next_high_ts"),
            "next_low_iso":     next_low_iso,
            "avg_interval_min": data.get("avg_interval_minutes"),
            "avg_high_s":       round(avg_high_s, 0) if avg_high_s else None,
            "based_on_n":       data.get("based_on_n"),
        })
    except FileNotFoundError:
        return jsonify({"mode": "pressure", "last_low": None, "next_high": None,
                        "next_low": None})
    except Exception:
        return jsonify({"mode": "pressure", "last_low": None, "next_high": None,
                        "next_low": None})


# ── flow cycles (bypass mode) ─────────────────────────────────────────────────

@dosatron_bp.route("/api/dosatron/flow_cycles")
def api_flow_cycles():
    hours  = int(request.args.get("hours", 24))
    labels = _read_labels(FLOW_CYCLE_LABELS)
    rows   = _read_flow_cycles(hours)
    result = []
    for rec in reversed(rows):
        fn     = rec.get("audio_file", "")
        exists = bool(fn) and os.path.exists(os.path.join(FLOW_CYCLES_DIR, fn))
        result.append({
            "start_ts":   rec.get("start_ts"),
            "end_ts":     rec.get("end_ts"),
            "duration_s": rec.get("duration_s"),
            "audio_file": fn if exists else None,
            "truncated":  rec.get("truncated", False),
            "verified":   labels.get(fn),   # True=real, False=false-positive, None=unlabeled
        })
    return jsonify({"flow_cycles": result})


@dosatron_bp.route("/api/dosatron/flow_cycles/<path:filename>")
def api_serve_flow_cycle(filename):
    if "/" in filename or ".." in filename:
        abort(400)
    return _serve_audio(os.path.join(FLOW_CYCLES_DIR, filename))


@dosatron_bp.route("/api/dosatron/flow_cycles/<path:filename>/verify", methods=["POST"])
def api_flow_verify(filename):
    if "/" in filename or ".." in filename:
        abort(400)
    data     = request.get_json(force=True)
    verified = data.get("verified")   # True, False, or None
    if verified is not None:
        verified = bool(verified)
    labels = _read_labels(FLOW_CYCLE_LABELS)
    if verified is None:
        labels.pop(filename, None)
    else:
        labels[filename] = verified
    _write_labels(FLOW_CYCLE_LABELS, labels)
    return jsonify({"ok": True, "filename": filename, "verified": verified})


@dosatron_bp.route("/api/dosatron/config", methods=["POST"])
def api_set_config():
    # Shadows the earlier api_set_config — Flask will only use the last definition.
    # Both click threshold and flow threshold are handled here.
    data = request.get_json(force=True)
    cfg  = _read_config()
    if "threshold" in data:
        t = int(data["threshold"])
        if not 100 <= t <= 32000:
            abort(400, "threshold must be 100–32000")
        cfg["threshold"] = t
    if "flow_threshold" in data:
        ft = int(data["flow_threshold"])
        if not 50 <= ft <= 10000:
            abort(400, "flow_threshold must be 50–10000")
        cfg["flow_threshold"] = ft
    if "flow_ratio_threshold" in data:
        fr = float(data["flow_ratio_threshold"])
        if not 1.0 <= fr <= 100.0:
            abort(400, "flow_ratio_threshold must be 1–100")
        cfg["flow_ratio_threshold"] = fr
    _write_config(cfg)
    return jsonify({"ok": True, "config": cfg})


# ── live audio stream ─────────────────────────────────────────────────────────

def _kill_live_stream():
    """Kill any orphaned arecord/ffmpeg from a previous live stream session."""
    try:
        with open(LIVE_PIDS_FILE) as f:
            pids = json.load(f)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        os.unlink(LIVE_PIDS_FILE)
    except Exception:
        pass


@dosatron_bp.route("/api/dosatron/live")
def api_live():
    """Stream live mic audio as MP3 via ffmpeg → dsnoop (shares with listener)."""
    _kill_live_stream()

    def generate():
        # arecord captures from the shared dsnoop device; ffmpeg encodes to MP3.
        # Two processes so ffmpeg never tries to open ALSA directly (it would
        # request stereo and dsnoop's mono slave would reject it).
        rec = subprocess.Popen(
            ["arecord", "-D", "dosatron_in", "-r", "44100", "-c", "1", "-f", "S16_LE"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        enc = subprocess.Popen(
            ["ffmpeg", "-y",
             "-f", "s16le", "-ar", "44100", "-ac", "1", "-i", "pipe:0",
             "-acodec", "libmp3lame", "-b:a", "64k",
             "-chunk_size", "1024", "-flush_packets", "1",
             "-f", "mp3", "pipe:1"],
            stdin=rec.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        rec.stdout.close()   # let rec's stdout be owned by enc
        try:
            with open(LIVE_PIDS_FILE, "w") as f:
                json.dump([rec.pid, enc.pid], f)
        except Exception:
            pass
        try:
            while True:
                chunk = enc.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                rec.terminate()
            except Exception:
                pass
            try:
                enc.terminate()
            except Exception:
                pass
            rec.wait()
            enc.wait()
            try:
                os.unlink(LIVE_PIDS_FILE)
            except Exception:
                pass

    resp = Response(generate(), mimetype="audio/mpeg")
    resp.headers["Cache-Control"] = "no-cache, no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp
