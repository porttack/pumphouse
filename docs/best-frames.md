# Best Frames — Automated Sunset Image Picker

Scores every frame of a day's timelapse and selects the best 4 from each of two independent algorithms: OpenCV heuristics (fast) and CLIP (semantic). Available from the timelapse viewer page at direct (non-Cloudflare) access only.

---

## Why Two Algorithms?

A single test against the April 4 2026 timelapse with a human-labeled contact sheet revealed that neither algorithm dominates:

| Source | Picks | Notes |
|--------|-------|-------|
| Human | 6, 26, 45, 57 | Ground truth |
| OpenCV | 13, 33, 53, 73 | Good at mid-sunset saturation; missed the blazing-sun frame; picked an IR/night frame before cutoff fix |
| CLIP | 5, 22, 42, 58 | Found the blazing-sun frame (frame 5 ≈ human's frame 6); independently rejected IR frames as negative |

OpenCV is reliable for the obvious colorful phase. CLIP catches semantically unusual but visually striking frames (e.g. blazing sun against deep blue sky) that OpenCV scores low due to low saturation and high brightness. Showing both sets side-by-side lets you compare quickly and pick a snapshot from either.

---

## Routes

| URL | Description |
|-----|-------------|
| `POST /timelapse/YYYY-MM-DD/pick-best` | Start background scoring job. `?rerun=1` to force re-score. Direct access only. |
| `GET /timelapse/YYYY-MM-DD/best-frames` | JSON status: `none`, `running`, `error`, or `done` with frame manifest. |
| `GET /timelapse/best/YYYY-MM-DD/cv_NNN.jpg` | Serve saved OpenCV pick. |
| `GET /timelapse/best/YYYY-MM-DD/cl_NNN.jpg` | Serve saved CLIP pick. |
| `GET /timelapse/best/YYYY-MM-DD/cv_NNN.jpg/view` | Full-page viewer with Set key snapshot button. |
| `GET /timelapse/best/YYYY-MM-DD/cl_NNN.jpg/view` | Full-page viewer with Set key snapshot button. |
| `POST /timelapse/YYYY-MM-DD/set-snapshot-file` | Set day's snapshot from a saved best-frame file (no browser round-trip). Body: `{"filename": "cv_001.jpg"}`. |

---

## Output Files

```
timelapses/best/
  YYYY-MM-DD/
    cv_001.jpg … cv_004.jpg   # OpenCV picks (chronological order)
    cl_001.jpg … cl_004.jpg   # CLIP picks (chronological order)
    manifest.json             # Scores, frame numbers, metadata
    .running                  # Lock file while job is in progress
    error.txt                 # Written only on failure
```

### manifest.json schema

```json
{
  "date": "2026-04-04",
  "n_scored": 63,
  "n_dropped": 12,
  "clip_available": true,
  "opencv_frames": [
    {"file": "cv_001.jpg", "score": 0.3551, "time_s": 2},
    ...
  ],
  "clip_frames": [
    {"file": "cl_001.jpg", "score": 0.3141, "time_s": 4},
    ...
  ]
}
```

`n_dropped` is the number of IR/night-mode frames removed before scoring (see Night-Mode Cutoff below). CLIP `score` values are normalized to 0–1 across the full scored set before saving.

---

## OpenCV Heuristics

Implemented in `monitor/pick_best.py :: _score_frame_cv()`.

| Component | Weight | Description |
|-----------|--------|-------------|
| Saturation | 28% | Mean HSV saturation across the whole frame — vibrancy |
| Sunset palette | 35% | Fraction of pixels with saturation > 50 and hue in orange/gold (0–35°) or pink/purple (130–180°) |
| Sharpness | 14% | Laplacian variance, capped at 300 — rewards focused frames |
| Exposure | 10% | Triangular penalty peaking at 52.5% brightness; penalises blown-out and near-black frames |
| Silhouette | 13% | Canny edge density in the bottom 40% of the frame — rewards defined tree silhouettes |

**Scores are raw 0–1 values** displayed as percentages (e.g. 0.57 → 57%).

**Strength:** fast (~20 s), reliable for the obvious colorful mid-sunset phase.  
**Weakness:** misses frames where the main interest is structural/compositional (blazing sun, dramatic cloud formation) rather than overall color saturation.

---

## CLIP (ViT-B/32)

Implemented in `monitor/pick_best.py :: _score_frames_clip()`.

Model: OpenAI `ViT-B/32` via the `clip` package. Loaded fresh per job (not kept in memory between runs — Pi RAM is tight).

**Scoring:** each frame is embedded and compared against a set of positive and negative text prompts. The score is `mean(positive similarities) − mean(negative similarities)`.

### Positive prompts

```python
'a stunning coastal sunset photograph',
'vivid orange and gold sunset sky',
'dramatic silhouette of trees against a glowing sky',
'beautiful Pacific Northwest sunset',
'dramatic clouds illuminated in orange and pink at sunset',
'moody coastal sky with scattered clouds at golden hour',
```

The last two prompts were added after a calibration session (April 2026) where human review showed that a few well-lit clouds significantly improve a sunset image over a featureless clear sky with the same color palette.

### Negative prompts

```python
'a dark night sky with no color',
'a washed out overexposed photo',
'an infrared black and white photograph',
'a perfectly clear blue sky with no clouds and no interest',
```

The last negative prompt discourages cloudless clear-sky frames, reinforcing the cloud preference.

**Raw scores** are cosine-similarity differences (typically −0.1 to +0.1). They are **normalized to 0–1 across all scored frames** before saving to the manifest so they display as a comparable percentage alongside the OpenCV scores.

**Strength:** understands semantics — "blazing sun", "dramatic backlight", "interesting cloud formation". Independently rejects IR frames (negative score on `'an infrared black and white photograph'`) without needing the explicit cutoff rule.  
**Weakness:** ~80 s on Pi 4 CPU; scores clusters of similar frames similarly, so diversity filter is important.

---

## Night-Mode Cutoff

Coastal twilight ends with the camera switching to IR/night mode — saturation collapses sharply (a step-change, not gradual). Both scorers share the same cutoff:

1. Compute mean HSV saturation for every frame.
2. Find the peak saturation across all frames.
3. Drop any frame below **20% of peak** before scoring.

This removes the IR tail reliably. The number of dropped frames is recorded in `n_dropped` in the manifest.

---

## Diversity Filter

After scoring, frames are selected greedily: pick the highest scorer, then find the next highest that is ≥ **15 seconds** away from any already-selected frame, and so on up to 4 picks. This prevents selecting 4 consecutive frames from the same golden moment.

---

## Automatic Snapshot Selection

`sunset_timelapse.py` calls `monitor.pick_best.pick_best_snapshot()` immediately after MP4 assembly, before the frame temp directory is deleted. This replaces the previous fixed-offset selection (25 minutes before sunset).

`pick_best_snapshot()`:
1. Applies the night-mode cutoff to all capture frames.
2. Tries CLIP first; falls back to OpenCV if CLIP is unavailable.
3. Returns the single highest-scoring frame path (no diversity filter — snapshot is one image).
4. On any exception, `sunset_timelapse.py` logs a warning and falls back to the fixed offset.

The snapshot is still overrideable via **Set key snapshot** on any best-frame viewer page or the video snapshot button.

---

## Performance (Pi 4, 4 GB RAM)

| Step | Time |
|------|------|
| ffmpeg frame extraction (1 fps) | ~3 s |
| Night-mode cutoff | ~1 s |
| OpenCV scoring (75 frames) | ~15 s |
| CLIP model load | ~10 s |
| CLIP inference (75 frames) | ~70 s |
| **Total (both scorers)** | **~100 s** |

CLIP loads the model fresh each run. Keeping it resident would save ~10 s but would consume ~500 MB RAM continuously, which is too much on a 3.7 GB Pi running the full monitoring stack.

---

## Dependencies

```
torch          # CPU-only build (pytorch.org/whl/cpu)
clip           # pip install git+https://github.com/openai/CLIP.git
opencv-python-headless  # already in requirements.txt
numpy          # already in requirements.txt
```

### Python 3.13 / torchvision compatibility note

`torchvision` has a broken operator registration on Python 3.13 (`torchvision::nms` does not exist error). `pick_best.py` patches `torch.library.Library._register_fake` to swallow the `RuntimeError` before importing `clip`. This is an upstream bug; remove the patch when a fixed torchvision is released.

---

## Calibration Notes (April 2026)

Contact-sheet review of the April 4 2026 timelapse (75 frames, 12 IR dropped):

- **CLIP found frame 5** (blazing sun against deep blue sky, strong silhouette) — OpenCV completely missed it, scoring it low due to low mean saturation and high brightness.
- **OpenCV found frames 13/33/53** reliably — the three obvious colorful mid-sunset phases. CLIP landed nearby (22/42/58) but slightly off within each phase.
- **Frame 73** (IR mode) was picked by early OpenCV before the night-mode cutoff was added. CLIP independently scored it as the lowest-scored frame (negative raw score).
- **Cloud preference** added to CLIP prompts after human reviewer noted that scattered clouds lit by sunset light are more visually interesting than a featureless clear sky with identical color.
