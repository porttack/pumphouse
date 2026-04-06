"""
pick_best.py — Score timelapse frames and select the best frames for each day.

Runs two independent scorers and saves 4 picks from each:

  OpenCV heuristics (fast, ~20s):
    - Saturation, sunset palette, sharpness, exposure, silhouette contrast
    - Night-mode cutoff: IR frames dropped before selection

  CLIP ViT-B/32 (semantic, ~60s):
    - Scores each frame against positive/negative sunset prompts
    - Understands "blazing sun", "dramatic backlight", "IR photo = bad"

Output layout:
    /home/pi/timelapses/best/<YYYY-MM-DD>/cv_001.jpg … cv_004.jpg
    /home/pi/timelapses/best/<YYYY-MM-DD>/cl_001.jpg … cl_004.jpg
    /home/pi/timelapses/best/<YYYY-MM-DD>/manifest.json
    /home/pi/timelapses/best/<YYYY-MM-DD>/.running
    /home/pi/timelapses/best/<YYYY-MM-DD>/error.txt
"""

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import cv2
import numpy as np

TIMELAPSE_DIR = '/home/pi/timelapses'
BEST_DIR      = os.path.join(TIMELAPSE_DIR, 'best')
N_PICKS       = 4   # picks per scorer
MIN_GAP_S     = 15  # minimum seconds between picks


# ---------------------------------------------------------------------------
# OpenCV scorer
# ---------------------------------------------------------------------------

def _mean_saturation(bgr: np.ndarray) -> float:
    s = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[:, :, 1]
    return float(np.mean(s)) / 255.0


def _score_frame_cv(bgr: np.ndarray) -> float:
    """Return a 0–1 aesthetic score using OpenCV heuristics."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    sat_score   = float(np.mean(s)) / 255.0
    sunset_mask = (s > 50) & ((h <= 35) | (h >= 130))
    warm_ratio  = float(sunset_mask.mean())

    gray      = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    lap_var   = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharp_score = min(lap_var / 300.0, 1.0)

    mean_v    = float(np.mean(v)) / 255.0
    exp_score = max(0.0, 1.0 - abs(mean_v - 0.525) / 0.375)

    h_px        = bgr.shape[0]
    bottom      = gray[int(h_px * 0.60):, :]
    edge_score  = min(float(cv2.Canny(bottom, 50, 150).mean()) / 30.0, 1.0)

    return (sat_score   * 0.28
            + warm_ratio  * 0.35
            + sharp_score * 0.14
            + exp_score   * 0.10
            + edge_score  * 0.13)


# ---------------------------------------------------------------------------
# CLIP scorer
# ---------------------------------------------------------------------------

_CLIP_POSITIVE = [
    'a stunning coastal sunset photograph',
    'vivid orange and gold sunset sky',
    'dramatic silhouette of trees against a glowing sky',
    'beautiful Pacific Northwest sunset',
    # Clouds add interest — a few dramatic clouds lit by sunset are better than
    # a featureless clear sky even if the clear sky has strong color.
    'dramatic clouds illuminated in orange and pink at sunset',
    'moody coastal sky with scattered clouds at golden hour',
]
_CLIP_NEGATIVE = [
    'a dark night sky with no color',
    'a washed out overexposed photo',
    'an infrared black and white photograph',
    # Penalise completely clear, cloudless skies — they lack visual texture.
    'a perfectly clear blue sky with no clouds and no interest',
]


def _try_load_clip():
    """
    Return (model, preprocess, tokenize_fn) or None if CLIP is unavailable.
    Applies the torchvision/Python-3.13 operator-registration patch before import.
    """
    try:
        # Patch torchvision's broken operator registration on Python 3.13
        import torch.library as _tl
        _orig = _tl.Library._register_fake
        def _safe(self, op, func, *a, **kw):
            try:
                return _orig(self, op, func, *a, **kw)
            except RuntimeError:
                pass
        _tl.Library._register_fake = _safe

        import torch
        import clip as _clip
        model, preprocess = _clip.load('ViT-B/32', device='cpu')
        model.eval()
        return model, preprocess, _clip.tokenize
    except Exception:
        return None


def _score_frames_clip(frame_paths: list[Path]) -> list[float] | None:
    """
    Score every frame with CLIP.  Returns a list of floats (one per frame)
    or None if CLIP is unavailable.
    """
    clip_pkg = _try_load_clip()
    if clip_pkg is None:
        return None

    model, preprocess, tokenize = clip_pkg
    import torch
    from PIL import Image

    with torch.no_grad():
        pos_feats = model.encode_text(tokenize(_CLIP_POSITIVE))
        neg_feats = model.encode_text(tokenize(_CLIP_NEGATIVE))
        pos_feats /= pos_feats.norm(dim=-1, keepdim=True)
        neg_feats /= neg_feats.norm(dim=-1, keepdim=True)

    scores = []
    for fp in frame_paths:
        img = preprocess(Image.open(fp)).unsqueeze(0)
        with torch.no_grad():
            feat = model.encode_image(img)
            feat /= feat.norm(dim=-1, keepdim=True)
        score = float((feat @ pos_feats.T).mean()) - float((feat @ neg_feats.T).mean())
        scores.append(score)
    return scores


# ---------------------------------------------------------------------------
# Shared selection logic
# ---------------------------------------------------------------------------

def _pick_diverse_top(scored: list[dict], n: int, min_gap_s: int) -> list[dict]:
    """Greedy diversity selection: pick top-n with >= min_gap_s between frames."""
    ranked   = sorted(scored, key=lambda x: x['score'], reverse=True)
    selected = []
    for f in ranked:
        if len(selected) >= n:
            break
        if all(abs(f['time_s'] - s['time_s']) >= min_gap_s for s in selected):
            selected.append(f)
    return sorted(selected, key=lambda x: x['time_s'])


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------

def _run_job(date_str: str, mp4_path: str) -> None:
    out_dir    = Path(BEST_DIR) / date_str
    lock_file  = out_dir / '.running'
    error_file = out_dir / 'error.txt'
    manifest_f = out_dir / 'manifest.json'

    lock_file.touch()
    error_file.unlink(missing_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix='pumphouse_best_') as tmp:
            frame_pattern = os.path.join(tmp, 'f%06d.jpg')
            subprocess.run(
                ['ffmpeg', '-y', '-i', mp4_path,
                 '-vf', 'fps=1', '-q:v', '4', frame_pattern],
                check=True, capture_output=True,
            )

            frame_paths = sorted(Path(tmp).glob('f*.jpg'))
            if not frame_paths:
                raise RuntimeError('ffmpeg produced no frames')

            # --- OpenCV pass ---
            cv_scored = []
            sat_values = []
            for i, fp in enumerate(frame_paths):
                bgr = cv2.imread(str(fp))
                if bgr is None:
                    continue
                sat_values.append(_mean_saturation(bgr))
                cv_scored.append({
                    'path':   str(fp),
                    'time_s': i,
                    'score':  _score_frame_cv(bgr),
                })

            if not cv_scored:
                raise RuntimeError('No frames could be read')

            # Night-mode cutoff (shared — IR frames are bad for both scorers)
            peak_sat  = max(sat_values)
            cutoff    = peak_sat * 0.20
            n_before  = len(cv_scored)
            keep_mask = [sat >= cutoff for sat in sat_values]
            cv_scored = [f for f, keep in zip(cv_scored, keep_mask) if keep]
            n_dropped = n_before - len(cv_scored)

            if not cv_scored:
                raise RuntimeError('All frames dropped by night-mode cutoff')

            cv_top = _pick_diverse_top(cv_scored, n=N_PICKS, min_gap_s=MIN_GAP_S)

            cv_frames_out = []
            for idx, f in enumerate(cv_top, 1):
                dest = out_dir / f'cv_{idx:03d}.jpg'
                bgr  = cv2.imread(f['path'])
                cv2.imwrite(str(dest), bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
                cv_frames_out.append({
                    'file':   f'cv_{idx:03d}.jpg',
                    'score':  round(f['score'], 4),
                    'time_s': f['time_s'],
                })

            # --- CLIP pass ---
            kept_paths = [Path(f['path']) for f in cv_scored]
            kept_times = [f['time_s'] for f in cv_scored]

            clip_frames_out = []
            clip_available  = False
            clip_scores_raw = _score_frames_clip(kept_paths)

            if clip_scores_raw is not None:
                clip_available = True
                cl_scored = [
                    {'path': str(p), 'time_s': t, 'score': s}
                    for p, t, s in zip(kept_paths, kept_times, clip_scores_raw)
                ]
                # Normalize raw cosine-diff scores to 0–1 across all scored frames
                # so they display as a comparable percentage in the UI.
                cl_min = min(f['score'] for f in cl_scored)
                cl_max = max(f['score'] for f in cl_scored)
                cl_range = (cl_max - cl_min) or 1.0
                for f in cl_scored:
                    f['score_norm'] = (f['score'] - cl_min) / cl_range

                cl_top = _pick_diverse_top(cl_scored, n=N_PICKS, min_gap_s=MIN_GAP_S)
                for idx, f in enumerate(cl_top, 1):
                    dest = out_dir / f'cl_{idx:03d}.jpg'
                    bgr  = cv2.imread(f['path'])
                    cv2.imwrite(str(dest), bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
                    clip_frames_out.append({
                        'file':   f'cl_{idx:03d}.jpg',
                        'score':  round(f['score_norm'], 4),
                        'time_s': f['time_s'],
                    })

        manifest_f.write_text(json.dumps({
            'date':           date_str,
            'n_scored':       len(cv_scored),
            'n_dropped':      n_dropped,
            'clip_available': clip_available,
            'opencv_frames':  cv_frames_out,
            'clip_frames':    clip_frames_out,
        }, indent=2))

    except Exception as exc:
        error_file.write_text(str(exc))
    finally:
        lock_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_pick_best(date_str: str, mp4_path: str, n: int = N_PICKS) -> str:
    """
    Start a background scoring job.  n is ignored (kept for API compat).
    Returns 'started', 'already_running', or 'already_done'.
    """
    out_dir = Path(BEST_DIR) / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    if (out_dir / '.running').exists():
        return 'already_running'
    if (out_dir / 'manifest.json').exists():
        return 'already_done'

    t = threading.Thread(
        target=_run_job,
        args=(date_str, mp4_path),
        daemon=True,
        name=f'pick-best-{date_str}',
    )
    t.start()
    return 'started'


def reset_best(date_str: str) -> None:
    """Delete all results so the job can be re-run."""
    out_dir = Path(BEST_DIR) / date_str
    for name in ('manifest.json', 'error.txt', '.running'):
        (out_dir / name).unlink(missing_ok=True)
    for jpg in out_dir.glob('*.jpg'):
        jpg.unlink(missing_ok=True)


def pick_best_snapshot(frame_paths: list[Path]) -> Path | None:
    """
    Given a list of timelapse frame paths (in capture order), return the single
    frame that makes the best snapshot using CLIP, falling back to OpenCV if
    CLIP is unavailable.

    Applies the night-mode IR cutoff before scoring.  Returns None if the list
    is empty or all frames are dropped by the cutoff.

    Intended to be called from sunset_timelapse.py immediately after assembly,
    replacing the fixed-time-offset snapshot selection.
    """
    if not frame_paths:
        return None

    # --- Night-mode cutoff (same logic as _run_job) ---
    bgrs = []
    for fp in frame_paths:
        bgr = cv2.imread(str(fp))
        bgrs.append(bgr)

    sat_values = [_mean_saturation(b) if b is not None else 0.0 for b in bgrs]
    peak_sat   = max(sat_values) if sat_values else 0.0
    cutoff     = peak_sat * 0.20
    kept       = [(fp, bgr) for fp, bgr, sat in zip(frame_paths, bgrs, sat_values)
                  if sat >= cutoff and bgr is not None]
    if not kept:
        return None
    kept_paths, kept_bgrs = zip(*kept)

    # --- Try CLIP first ---
    clip_scores = _score_frames_clip(list(kept_paths))
    if clip_scores is not None:
        best_idx = clip_scores.index(max(clip_scores))
        return kept_paths[best_idx]

    # --- OpenCV fallback ---
    cv_scores = [_score_frame_cv(b) for b in kept_bgrs]
    best_idx  = cv_scores.index(max(cv_scores))
    return kept_paths[best_idx]


def get_status(date_str: str) -> dict:
    out_dir = Path(BEST_DIR) / date_str

    if (out_dir / '.running').exists():
        return {'status': 'running'}

    error_file = out_dir / 'error.txt'
    if error_file.exists():
        return {'status': 'error', 'message': error_file.read_text().strip()}

    manifest_f = out_dir / 'manifest.json'
    if manifest_f.exists():
        try:
            return {'status': 'done', **json.loads(manifest_f.read_text())}
        except Exception as exc:
            return {'status': 'error', 'message': f'Corrupt manifest: {exc}'}

    return {'status': 'none'}
