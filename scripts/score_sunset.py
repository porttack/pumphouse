#!/usr/bin/env python3
"""
Score sunset timelapse videos using a hybrid color+CLIP algorithm.

Design philosophy:
  - Direct color/saturation metrics are the primary signal (~60%).
    They precisely measure what we care about: orange, pink, purple, drama.
  - CLIP is a secondary signal (~40%), useful for detecting complex cloud
    texture and dramatic scenes that color alone might miss.
  - Frame selection focuses on the lit sunset window only — dark (night)
    frames are dropped so they don't drag scores down.
  - Peak-weighted aggregation: top ~15% of frames dominate (rewarding brief
    brilliant moments), but the median still contributes so a video that is
    almost entirely fog can't be rescued by a single lucky frame.

Score formula per frame:
  color_score  = 0.45 * colorfulness + 0.35 * warmth + 0.20 * cloud_texture
  hybrid_score = 0.60 * color_score + 0.40 * clip_score

Aggregation:
  final = 0.55 * top_15%_mean + 0.30 * top_50%_mean + 0.15 * median

Usage:
  python score_sunset.py                        # last 8 dates
  python score_sunset.py 2026-02-26 2026-03-21
  python score_sunset.py --all                  # every date with an mp4
  python score_sunset.py --write                # save to clip_scores.json
  python score_sunset.py --no-clip              # color-only (faster, skip CLIP)
  python score_sunset.py --debug 2026-04-11     # print per-frame breakdown
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

TIMELAPSE_DIR    = '/home/pi/timelapses'
SCORES_FILE      = os.path.join(TIMELAPSE_DIR, 'clip_scores.json')
CLIP_MODEL       = os.path.expanduser('~/.cache/clip/ViT-B-32.pt')
FRAMES_EXTRACT   = 48   # extract this many before darkness filtering (reduced to avoid OOM)
DARK_LUM_CUTOFF  = 0.15 # frames with mean luminance below this are "night" — drop them
MIN_LUM_CUTOFF   = 0.05 # also drop extremely dark frames (totally black)

# CLIP text prompts
GOOD_PROMPTS = [
    'dramatic clouds lit from below with vivid orange and pink light at sunset',
    'fast moving storm clouds with colorful glowing edges at golden hour',
    'a breathtaking sunset with brilliant orange pink and purple sky over ocean',
    'multiple cloud layers moving in different directions at sunset with color',
    'rays of golden light breaking through textured clouds over the ocean at dusk',
    'a fiery red and orange sky with dark dramatic clouds',
    'vivid sunset colors on ocean waves with dramatic sky',
]
BAD_PROMPTS = [
    'a flat featureless uniform fog bank with no color or texture',
    'a completely gray overcast sky with no light or color',
    'dense white fog obscuring everything with no cloud definition',
    'a dull gray rainy day sky with no visual interest',
]

# CLIP ViT-B/32 preprocessing (replicates torchvision transforms without importing it)
_CLIP_MEAN = np.array([0.48145466, 0.4578275,  0.40821073], dtype=np.float32)
_CLIP_STD  = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


# ---------------------------------------------------------------------------
# Image preprocessing & color metrics (pure PIL + numpy, no cv2 needed)
# ---------------------------------------------------------------------------

def _clip_preprocess(img: Image.Image) -> np.ndarray:
    """PIL → (1,3,224,224) float32, CLIP-normalized."""
    w, h = img.size
    scale = 224 / min(w, h)
    img = img.resize((round(w * scale), round(h * scale)), Image.BICUBIC)
    nw, nh = img.size
    img = img.crop(((nw - 224) // 2, (nh - 224) // 2,
                    (nw - 224) // 2 + 224, (nh - 224) // 2 + 224))
    arr = np.asarray(img.convert('RGB'), dtype=np.float32) / 255.0
    arr = (arr - _CLIP_MEAN) / _CLIP_STD
    return arr.transpose(2, 0, 1)[np.newaxis]


def _rgb_to_hsv(arr):
    """Fast vectorized RGB→HSV. arr is (H,W,3) float32 0-1. Returns h(0-1),s(0-1),v(0-1)."""
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    diff = maxc - minc
    v = maxc
    with np.errstate(invalid='ignore', divide='ignore'):
        s = np.where(maxc > 1e-6, diff / maxc, 0.0)
    safe = np.where(diff > 1e-6, diff, 1.0)  # avoid divide-by-zero; masked out below
    h = np.zeros_like(r)
    rm = (maxc == r) & (diff > 1e-6)
    gm = (maxc == g) & (diff > 1e-6)
    bm = (maxc == b) & (diff > 1e-6)
    with np.errstate(invalid='ignore', divide='ignore'):
        h[rm] = ((g[rm] - b[rm]) / safe[rm]) % 6
        h[gm] = (b[gm] - r[gm]) / safe[gm] + 2
        h[bm] = (r[bm] - g[bm]) / safe[bm] + 4
    return h / 6.0, s, v  # hue 0-1 = 0-360°


# Fixed absolute caps derived from empirical analysis of known-good vs known-bad sunsets.
# These are what we expect a "great" sunset to approach (Mar 21 style).
# Scores above cap are clamped to 1.0; this ensures absolute comparisons across videos.
_SUNSET_HUE_CAP   = 0.45   # fraction of sky pixels in sunset hue range (good sunset: 0.3-0.5)
_WARMTH_CAP       = 0.12   # orange/red warmth score (fog: 0.013, stunning: 0.091-0.120)
_COLORFULNESS_CAP = 0.25   # Hasler colorfulness (fog: 0.054, good: 0.18-0.28)
_DYNAMISM_CAP     = 0.025  # mean abs frame-to-frame luminance diff (fog: ~0.005, motion: ~0.025)


def _color_score(img: Image.Image) -> dict:
    """
    Compute per-frame color metrics from a PIL image.

    Primary metric: sunset_hue_score — fraction of (saturated, non-dark) pixels
    whose hue falls in the "sunset palette": orange (5-65°), red (0-5° + 345-360°),
    pink/magenta (295-345°), and purple (250-295°).  Weighted by per-pixel saturation
    so a vivid orange pixel counts more than a pale peach one.

    Secondary: warmth (orange/red specifically), colorfulness (Hasler).
    Composite uses fixed absolute caps so scores are comparable across videos.
    """
    arr = np.asarray(img.convert('RGB')).astype(np.float32) / 255.0
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # Luminance
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    avg_lum = float(np.mean(lum))

    # --- HSV-based sunset hue score ---
    h, s, v = _rgb_to_hsv(arr)
    h_deg = h * 360.0

    # Pixels must be somewhat saturated and not too dark to count
    active = (s > 0.25) & (v > 0.15)

    # Sunset hue ranges: orange/yellow, red (wraps around 0°), pink/magenta, purple
    sunset_hue = (
        ((h_deg >= 5)   & (h_deg <= 65))   |   # orange + yellow-orange
        ((h_deg >= 345) | (h_deg <= 5))    |   # red (wraps 360→0)
        ((h_deg >= 295) & (h_deg <= 345))  |   # pink / magenta
        ((h_deg >= 250) & (h_deg <= 295))       # purple / violet
    )
    sunset_pixels = active & sunset_hue

    # Weight by saturation strength so vivid pixels count more
    if sunset_pixels.any():
        sunset_hue_score = float(np.sum(s[sunset_pixels])) / (arr.shape[0] * arr.shape[1])
    else:
        sunset_hue_score = 0.0

    # --- Warmth: orange/red specifically (independent cross-check) ---
    warmth = float(np.mean(np.clip(r - 0.55 * g - 0.45 * b, 0, 1)))

    # --- Colorfulness (Hasler & Suestrunk) ---
    rg = r - g
    yb = 0.5 * (r + g) - b
    colorfulness = float(
        np.sqrt(rg.std() ** 2 + yb.std() ** 2)
        + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    )

    # --- Composite using FIXED absolute caps ---
    # Weights: sunset hue is the primary signal (what we actually care about),
    # warmth is a strong secondary (orange/red), colorfulness catches purple/variety.
    composite = (
        0.55 * min(1.0, sunset_hue_score / _SUNSET_HUE_CAP)
        + 0.30 * min(1.0, warmth / _WARMTH_CAP)
        + 0.15 * min(1.0, colorfulness / _COLORFULNESS_CAP)
    )

    return {
        'sunset_hue':   sunset_hue_score,
        'warmth':       warmth,
        'colorfulness': colorfulness,
        'luminance':    avg_lum,
        'color_composite': composite,
    }


def _temporal_dynamism(frames) -> float:
    """
    Measure frame-to-frame luminance change as a proxy for motion/dynamism.

    Returns the mean absolute per-pixel luminance difference across adjacent
    frame pairs, computed at 64×64 for speed.

    Static fog: ~0.005. Active clouds / fast motion: ~0.025+.
    This is a video-level signal (not per-frame).
    """
    if len(frames) < 2:
        return 0.0

    diffs = []
    prev_lum = None
    for f in frames:
        arr = np.asarray(f.convert('RGB')).astype(np.float32) / 255.0
        lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
        # Downsample to 64×64 for speed and noise reduction
        step_r = max(1, lum.shape[0] // 64)
        step_c = max(1, lum.shape[1] // 64)
        lum_small = lum[::step_r, ::step_c]
        if prev_lum is not None:
            diffs.append(float(np.mean(np.abs(lum_small - prev_lum))))
        prev_lum = lum_small

    return float(np.mean(diffs)) if diffs else 0.0


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def _load_clip():
    """
    Load CLIP ViT-B/32 as an nn.Module on CPU.
    Imports clip/model.py directly to bypass the broken torchvision import.
    """
    import torch
    import importlib.util
    import types

    _clip_dir = '/home/pi/src/pumphouse/venv/lib/python3.13/site-packages/clip'

    spec = importlib.util.spec_from_file_location('clip_model', f'{_clip_dir}/model.py')
    clip_model_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(clip_model_mod)

    state_dict = torch.jit.load(CLIP_MODEL, map_location='cpu').state_dict()
    model = clip_model_mod.build_model(state_dict).float().eval()
    return model


def _load_tokenizer():
    import importlib.util
    _clip_dir = '/home/pi/src/pumphouse/venv/lib/python3.13/site-packages/clip'
    spec = importlib.util.spec_from_file_location('clip_tok', f'{_clip_dir}/simple_tokenizer.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SimpleTokenizer()


def _tokenize(tokenizer, texts):
    import torch
    sot = tokenizer.encoder['<|startoftext|>']
    eot = tokenizer.encoder['<|endoftext|>']
    rows = []
    for t in texts:
        ids = [sot] + tokenizer.encode(t)[:75] + [eot]
        ids += [0] * (77 - len(ids))
        rows.append(ids[:77])
    return torch.tensor(rows, dtype=torch.long)


def _encode_text(model, tokenizer, texts):
    import torch
    tokens = _tokenize(tokenizer, texts)
    with torch.no_grad():
        feats = model.encode_text(tokens).float()
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats


def _encode_images_clip(model, frames, batch_size=8):
    """Encode frames in small batches to avoid OOM on the Pi."""
    import torch
    all_feats = []
    for i in range(0, len(frames), batch_size):
        chunk = frames[i:i + batch_size]
        batch = np.concatenate([_clip_preprocess(f) for f in chunk], axis=0)
        tensor = torch.from_numpy(batch)
        with torch.no_grad():
            feats = model.encode_image(tensor).float()
            feats = feats / feats.norm(dim=-1, keepdim=True)
        all_feats.append(feats)
        del tensor, batch  # free immediately
    import torch
    return torch.cat(all_feats, dim=0)


# ---------------------------------------------------------------------------
# Video I/O
# ---------------------------------------------------------------------------

def _mp4_for_date(date_str):
    candidates = sorted(glob.glob(os.path.join(TIMELAPSE_DIR, f'{date_str}_*.mp4')))
    non_zoom = [p for p in candidates if '_zoom' not in p and 'kenburns' not in p]
    return (non_zoom or candidates or [None])[0]


def _extract_frames(mp4_path, n=FRAMES_EXTRACT):
    """Extract n evenly-spaced frames from an mp4."""
    r = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', mp4_path],
        capture_output=True, text=True
    )
    try:
        duration = float(r.stdout.strip())
    except ValueError:
        duration = 60.0

    frames = []
    with tempfile.TemporaryDirectory() as td:
        step = max(1, int(duration * 25 / n))
        vf = f'select=not(mod(n\\,{step}))'
        subprocess.run(
            ['ffmpeg', '-i', mp4_path, '-vf', vf,
             '-vsync', 'vfr', '-q:v', '2', '-frames:v', str(n),
             os.path.join(td, 'frame_%04d.jpg')],
            capture_output=True
        )
        for path in sorted(glob.glob(os.path.join(td, '*.jpg')))[:n]:
            try:
                frames.append(Image.open(path).copy())
            except Exception:
                pass
    return frames


def _trim_dark_frames(frames):
    """
    Drop dark (night) frames from the end of the sequence, and any
    individually too-dark frames (camera pointed at black sky).

    Strategy:
      1. Walk backward from the end; stop as soon as we hit a frame
         that's bright enough — keep everything up to that point.
      2. Then drop any remaining outlier-dark frames in the middle.
    """
    if not frames:
        return frames

    lums = []
    for f in frames:
        arr = np.asarray(f.convert('RGB')).astype(np.float32) / 255.0
        lums.append(float(np.mean(0.299*arr[:,:,0] + 0.587*arr[:,:,1] + 0.114*arr[:,:,2])))

    # Find last frame that's bright enough
    last_lit = len(frames) - 1
    for i in range(len(frames) - 1, -1, -1):
        if lums[i] >= DARK_LUM_CUTOFF:
            last_lit = i
            break

    # Keep everything up to last_lit, then filter out any stray very-dark frames
    kept = [(f, l) for f, l in zip(frames[:last_lit + 1], lums[:last_lit + 1])
            if l >= MIN_LUM_CUTOFF]
    if not kept:
        return frames  # fallback: keep all if we filtered everything
    frames_out, lums_out = zip(*kept)
    return list(frames_out), list(lums_out)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _aggregate(scores_np):
    """
    Aggregation that rewards brief brilliant moments while heavily penalizing
    videos that are mostly dull (fog, flat overcast).

    35% top-10%  — rewards the peak moments (brief orange flash still counts)
    35% top-50%  — rewards sustained color through the golden hour
    30% median   — hard floor: a video that is 50%+ boring gets punished here

    Because scores_np values use FIXED absolute caps (not per-video normalization),
    a fog video with composite ~0.05 will have a very low median even if a few
    frames hit 0.40, giving a low final score. A sustained great sunset has
    high values throughout, boosting all three components.
    """
    n = len(scores_np)
    sorted_s = np.sort(scores_np)
    top10 = sorted_s[max(0, int(n * 0.90)):].mean()
    top50 = sorted_s[max(0, int(n * 0.50)):].mean()
    med   = float(np.median(sorted_s))
    return 0.25 * top10 + 0.30 * top50 + 0.45 * med


def score_date(model, good_feats, bad_feats, tokenizer, date_str,
               use_clip=True, debug=False):
    mp4 = _mp4_for_date(date_str)
    if not mp4:
        print(f'  {date_str}: no mp4 found', file=sys.stderr)
        return None

    print(f'  {date_str}: extracting frames …', end=' ', flush=True)
    raw_frames = _extract_frames(mp4)
    if not raw_frames:
        print('no frames', file=sys.stderr)
        return None

    result = _trim_dark_frames(raw_frames)
    if isinstance(result, tuple):
        frames, lums = result
    else:
        frames = result
        lums = None

    print(f'{len(raw_frames)} raw → {len(frames)} lit frames', flush=True)

    # --- Color metrics ---
    color_data = [_color_score(f) for f in frames]
    color_composites = np.array([d['color_composite'] for d in color_data])
    # color_composite already uses fixed absolute caps (0-1 range) — do NOT
    # re-normalize per-video, as that would destroy cross-video comparability.
    color_norm = color_composites

    # --- Temporal dynamism (video-level) ---
    dynamism = _temporal_dynamism(frames)
    dynamism_score = min(1.0, dynamism / _DYNAMISM_CAP)

    # --- CLIP metrics (optional) ---
    if use_clip and model is not None:
        import torch
        img_feats = _encode_images_clip(model, frames)
        good_sim  = (img_feats @ good_feats.T).mean(dim=1)
        bad_sim   = (img_feats @ bad_feats.T).mean(dim=1)
        clip_p    = torch.sigmoid((good_sim - bad_sim) * 10).numpy()
        color_clip = 0.60 * color_norm + 0.40 * clip_p
        color_agg = _aggregate(color_clip)
    else:
        clip_p = None
        color_agg = _aggregate(color_norm)

    # --- Blend color aggregate + temporal dynamism ---
    # 48% color (sustained sunset quality) + 52% dynamism (motion, fast clouds)
    # High dynamism weight ensures static/fog videos score low even with a brief color flash.
    # Mathematically: this is the minimum dynamism weight needed for Apr 9 (motion, hazy sky)
    # to beat Apr 11 (static fog with brief orange flare) given their color/dynamism spreads.
    agg = 0.48 * color_agg + 0.52 * dynamism_score
    clip_score = round(agg * 1000)

    if debug:
        print(f'\n  {date_str} per-frame breakdown (sorted by color score):')
        idxs = np.argsort(color_norm)[::-1]
        for rank, i in enumerate(idxs[:15]):
            cd = color_data[i]
            cp_str = f'clip={clip_p[i]:.3f}' if clip_p is not None else ''
            print(f'    #{rank+1:2d} frame {i:3d}: color={color_norm[i]:.3f} '
                  f'{cp_str}  '
                  f'(hue={cd["sunset_hue"]:.3f} warm={cd["warmth"]:.3f} '
                  f'colorful={cd["colorfulness"]:.3f} lum={cd["luminance"]:.3f})')
        print()

    print(f'  {date_str}: color_agg={color_agg:.3f} dynamism={dynamism:.4f}({dynamism_score:.3f}) '
          + (f'clip_agg={round(float(_aggregate(clip_p)), 4)} ' if clip_p is not None else '')
          + f'→ {clip_score}/1000')

    return {
        'date': date_str,
        'score': clip_score,
        'frames_lit': len(frames),
        'frames_total': len(raw_frames),
        'mp4': os.path.basename(mp4),
        'color_agg': round(color_agg, 4),
        'dynamism': round(dynamism, 5),
        'clip_agg': round(float(_aggregate(clip_p)), 4) if clip_p is not None else None,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('dates', nargs='*')
    parser.add_argument('--all',     action='store_true', help='Score all dates')
    parser.add_argument('--write',   action='store_true', help='Save to clip_scores.json')
    parser.add_argument('--no-clip', action='store_true', help='Skip CLIP (color only, fast)')
    parser.add_argument('--debug',   action='store_true', help='Print per-frame breakdown')
    args = parser.parse_args()

    if args.all or not args.dates:
        mp4s  = sorted(glob.glob(os.path.join(TIMELAPSE_DIR, '????-??-??_*.mp4')))
        dates = sorted({re.match(r'(\d{4}-\d{2}-\d{2})', os.path.basename(p)).group(1)
                        for p in mp4s
                        if '_zoom' not in p and 'kenburns' not in p and 'test' not in p})
        if not args.all:
            dates = dates[-8:]
    else:
        dates = args.dates

    use_clip = not args.no_clip
    model = good_feats = bad_feats = tokenizer = None

    if use_clip:
        print(f'Loading CLIP from {CLIP_MODEL} …', flush=True)
        model     = _load_clip()
        tokenizer = _load_tokenizer()
        print('Encoding text prompts …', flush=True)
        good_feats = _encode_text(model, tokenizer, GOOD_PROMPTS)
        bad_feats  = _encode_text(model, tokenizer, BAD_PROMPTS)
    else:
        print('CLIP disabled — color-only scoring', flush=True)

    results = []
    for d in dates:
        r = score_date(model, good_feats, bad_feats, tokenizer, d,
                       use_clip=use_clip, debug=args.debug)
        if r:
            results.append(r)

    print()
    print('=' * 50)
    for r in sorted(results, key=lambda x: -x['score']):
        lit_pct = round(100 * r['frames_lit'] / max(r['frames_total'], 1))
        print(f"  {r['date']}  {r['score']:>4}/1000"
              f"  color={r['color_agg']:.3f}"
              f"  dyn={r['dynamism']:.4f}"
              + (f"  clip={r['clip_agg']:.3f}" if r['clip_agg'] is not None else '')
              + f"  (lit {lit_pct}%)")
    print('=' * 50)

    if args.write:
        existing = {}
        if os.path.exists(SCORES_FILE):
            try:
                existing = json.loads(open(SCORES_FILE).read())
            except Exception:
                pass
        for r in results:
            existing[r['date']] = {
                'score':     r['score'],
                'frames':    r['frames_lit'],
                'color_agg': r['color_agg'],
                'dynamism':  r['dynamism'],
                'clip_agg':  r['clip_agg'],
            }
        with open(SCORES_FILE, 'w') as f:
            json.dump(existing, f, indent=2, sort_keys=True)
        print(f'Scores written to {SCORES_FILE}')


if __name__ == '__main__':
    main()
