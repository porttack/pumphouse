# AP CSP Lesson Unit — Part 3 of 3: Computer Vision, AI Image Scoring, and Human-in-the-Loop Verification
### A Real-World Project-Based Unit on Computer Science Principles

**Course:** AP Computer Science Principles (CS50-based)  
**Total Time:** 4 sessions × 30 minutes  
**Continues from [Part 2](ap-csp-timelapse-part2.md)**  
**Central Project:** The same Raspberry Pi sunset timelapse system, now extended with
automated image quality scoring using two different AI approaches.  
**Driving Question:** *How do you teach a computer to recognize a beautiful sunset — and how
do you know if it's working?*

**AP CSP Big Ideas Covered:** DAT · AAP · IOC (AI ethics and transparency)

---

## Unit Overview

This part is about one specific engineering decision repeated many times: *how do we know
which frame of a sunset timelapse would make the best photograph?*

The answer required three different technical approaches, human judgment to evaluate them,
a cloud AI to help verify at scale, and AI-assisted coding to build the whole thing. That
combination — algorithms + human verification + cloud AI + AI-generated code — is what
modern applied AI work actually looks like.

**Project arc (this part):**

16. What is a pixel? What is a frame? How do images become data a program can reason about?
17. Heuristic scoring: writing rules by hand (saturation, palette, sharpness, exposure, silhouette)
18. What is CLIP? How is it different from an LLM? How does it score an image?
19. Prompt engineering for images — tuning CLIP with positive and negative text descriptions
20. Human-in-the-loop verification: the contact sheet technique and uploading to a cloud AI
21. AI-assisted coding: how Claude wrote most of this code, what that changes, and what it doesn't

---

## SIDEBAR: Is CLIP an LLM?

**No. CLIP is a Vision-Language Model (VLM) — a different animal.**

Understanding the difference matters.

### What an LLM does

A Large Language Model (GPT-4, Claude, Llama) is trained to predict the next token in a
sequence. Given a prompt, it generates text. It has learned an enormous amount about the
world through patterns in language, but its primary operation is *sequence continuation*.

### What CLIP does

CLIP (Contrastive Language–Image Pre-training, OpenAI 2021) does something structurally
different. It was trained on ~400 million image-caption pairs scraped from the internet.
During training it learned to map *both* images and text into the same vector space, such
that an image of an orange sunset and the text "vivid orange sunset sky" end up close to
each other in that space, while "an infrared black and white photograph" ends up far away.

```
Image of sunset ──────────────────→ [0.23, -0.11, 0.87, ...]
                  CLIP encoder                    ↑
                                               same
                                            neighborhood
Text "vivid orange sunset sky" ───→ [0.21, -0.09, 0.85, ...]
```

It does **not** generate text. It does **not** answer questions. It just embeds things into a
shared space and lets you measure similarity with a dot product.

### The practical difference

| | LLM | CLIP |
|---|---|---|
| Input | Text (or image+text for multimodal) | Image OR text |
| Output | Generated text tokens | A fixed-length vector (embedding) |
| Use case | Answering questions, writing, summarizing | Similarity search, zero-shot classification |
| Model size (ViT-B/32) | GPT-4: ~1 trillion params | ~150 million params |
| Pi 4 inference time | Impractical | ~1 second/frame |

CLIP is a *retrieval and similarity* tool; LLMs are *generation* tools. In this project CLIP
scores images; Claude (an LLM) was used to *write the code that runs CLIP*.

### Zero-shot classification

The clever trick CLIP enables is **zero-shot classification** — you never have to label
training images. You just describe what you want in plain English, and CLIP tells you how
close each image is to that description. That's how prompt engineering works for CLIP: the
"prompts" are the text descriptions you write, and the scores are cosine similarities.

---

## Session 1 — Images as Data (30 min)

### The core idea: a pixel is just a number

A JPEG from the timelapse camera is 1920 × 960 pixels. Each pixel is three numbers (R, G, B)
between 0 and 255. The entire image is therefore a 2D array of shape (960, 1920, 3) —
roughly 5.5 million numbers.

A program can't "see" a sunset. But it can compute statistics over those 5.5 million numbers.

### The HSV color space

RGB is how screens display color. HSV (Hue, Saturation, Value) is how humans *describe* color.
OpenCV converts between them with one function call.

```python
import cv2
bgr = cv2.imread('frame_0042.jpg')   # shape (960, 1920, 3), dtype uint8
hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
h = hsv[:, :, 0]   # hue:        0–180  (OpenCV uses half-degrees)
s = hsv[:, :, 1]   # saturation: 0–255  (0 = grey, 255 = fully vivid)
v = hsv[:, :, 2]   # value:      0–255  (0 = black, 255 = white)
```

Why HSV for sunsets? Because "orange" is a specific hue range, not a mixture of R/G/B values.
Checking `h <= 35` is simpler and more robust than checking the equivalent RGB condition.

**Discussion question:** Why does OpenCV use BGR order instead of RGB? *(Historical: early
Intel image processing libraries used BGR; OpenCV inherited it and kept it for compatibility.)*

### Activity

Given this frame (frame_0042 from April 4 2026):
- What would the mean saturation tell you about whether it was a colorful sunset or a grey sky?
- If mean_v = 0.9, what does that suggest about this frame?
- Write a one-sentence "algorithm" in plain English for picking the best sunset photo.

---

## Session 2 — Heuristic Scoring (30 min)

### What is a heuristic?

A heuristic is a rule of thumb — a procedure that works well in practice but isn't guaranteed
to be optimal. Most practical computer vision before deep learning was heuristics.

The OpenCV scorer in `monitor/pick_best.py` uses five heuristics:

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| Saturation | 28% | Mean HSV saturation — is the image vivid or grey? |
| Sunset palette | 35% | What fraction of pixels have sunset-range hues (orange/gold/pink/purple) and are vivid? |
| Sharpness | 14% | Laplacian variance — are edges crisp or blurry? |
| Exposure | 10% | Is the brightness in the sweet spot (not blown out, not near-black)? |
| Silhouette | 13% | Edge density in the bottom 40% — do the trees make a clean silhouette? |

### The weights were chosen empirically

There was no mathematical derivation. The weights were set, the results were inspected
against human judgment, and then adjusted. This is how most heuristic systems are built.

### Where heuristics fail

Here is the problem we ran into: the algorithm completely missed **frame 6** of the April 4
timelapse. Frame 6 shows the sun blazing white-hot against a deep blue sky with a perfect
tree silhouette. A human reviewer called it one of the best frames of the whole set.

Why did the algorithm miss it?
- Mean saturation: **low** — the white sun disc and blue sky have low saturation
- Sunset palette: **low** — white and blue are not in the orange/pink hue range
- Exposure: **penalized** — mean brightness is high (the sun is very bright)

The algorithm literally could not see the sun. It was optimizing for "colorful" and frame 6
was "stark and dramatic" — a completely different kind of beautiful.

**Discussion question:** Can you think of another scene this algorithm would miss?
*(Fog rolling in at dusk; a lightning bolt; dramatic silhouette with no color at all.)*

---

## Session 3 — CLIP and Prompt Engineering (30 min)

### How CLIP scores an image

```python
import clip, torch
from PIL import Image

model, preprocess = clip.load('ViT-B/32', device='cpu')

positive_prompts = [
    'a stunning coastal sunset photograph',
    'vivid orange and gold sunset sky',
    'dramatic clouds illuminated in orange and pink at sunset',
]
negative_prompts = [
    'an infrared black and white photograph',
    'a perfectly clear blue sky with no clouds and no interest',
]

# Embed the text prompts once
pos_feats = model.encode_text(clip.tokenize(positive_prompts))
neg_feats = model.encode_text(clip.tokenize(negative_prompts))
pos_feats /= pos_feats.norm(dim=-1, keepdim=True)   # normalize to unit vectors
neg_feats /= neg_feats.norm(dim=-1, keepdim=True)

# Score one frame
img  = preprocess(Image.open('frame_0006.jpg')).unsqueeze(0)
feat = model.encode_image(img)
feat /= feat.norm(dim=-1, keepdim=True)

score = (feat @ pos_feats.T).mean() - (feat @ neg_feats.T).mean()
# A positive score means the image is more like the positive prompts than the negative ones
```

The dot product `feat @ pos_feats.T` is the **cosine similarity** between the image embedding
and each text embedding. Averaging across all positive prompts gives one number. Subtracting
the negative average penalizes things you don't want.

### CLIP found frame 6

When we ran CLIP on the April 4 timelapse, it selected frame 5 (one second earlier than the
human's frame 6 pick — essentially the same moment). OpenCV had ranked that frame near the
bottom of the list. CLIP understood "dramatic backlit sun" as aesthetically striking; the
heuristic only understood "saturated colors."

### Prompt engineering

CLIP's scores depend entirely on the text prompts. Writing good prompts is called
**prompt engineering**. After the first test, we added two new positive prompts:

```python
'dramatic clouds illuminated in orange and pink at sunset',
'moody coastal sky with scattered clouds at golden hour',
```

And one new negative prompt:

```python
'a perfectly clear blue sky with no clouds and no interest',
```

**Why?** Human review showed that a few well-lit clouds dramatically improve a sunset image
over a featureless clear sky, even if the clear sky has slightly stronger color. The original
prompts said nothing about clouds, so CLIP had no reason to prefer them.

This is the same kind of prompt engineering used with LLMs — you describe what you want more
precisely, and the model gets better at finding it.

### Normalizing scores for comparison

CLIP's raw scores are cosine-similarity differences, typically between −0.1 and +0.1.
OpenCV scores are 0–1. To compare them in the UI, the CLIP scores are normalized:

```python
cl_min   = min(f['score'] for f in cl_scored)
cl_max   = max(f['score'] for f in cl_scored)
cl_range = (cl_max - cl_min) or 1.0
score_norm = (raw_score - cl_min) / cl_range   # now 0–1
```

This is **min-max normalization** — a standard data preprocessing technique.

**Discussion question:** What's the risk of normalizing this way?
*(If all frames score very similarly — e.g. on a grey overcast day — the normalized scores
spread across 0–1 regardless, making even the "best" pick look better than it is.)*

---

## Session 4 — Verification, Human-in-the-Loop, and AI-Assisted Coding (30 min)

### The contact sheet technique

After building a scorer, how do you know if it's working? You need ground truth — examples
where you know the right answer.

We generated a **contact sheet**: a single image with all 75 frames tiled in a 10×8 grid,
each labeled with its frame number. This was uploaded to Claude.ai with the prompt:

> *These are 75 consecutive 1-second frames from a coastal Oregon sunset timelapse, labeled
> 1–75. Which 4 frames would make the best standalone photographs — most vivid colors, most
> dramatic sky, best composition? List the frame numbers and briefly say why for each.*

Claude returned: frames 6, 26, 45, 57 (plus honorable mention: 33).

We then compared:

| Source | Picks |
|--------|-------|
| Human (Claude.ai) | 6, 26, 45, 57 |
| OpenCV heuristic | 13, 33, 53, 73 |
| CLIP (first pass) | 5, 22, 42, 58 |
| CLIP (after prompt tuning) | 5, 22, 41, 57 |

Observations:
- **OpenCV** reliably found the colorful mid-sunset phases but missed the blazing-sun frame (6) and picked an IR/night frame (73) before we added the night-mode cutoff.
- **CLIP** found frame 5 (one frame away from the human's frame 6) — the semantically striking shot that OpenCV completely missed. After prompt tuning, it landed on 57, matching one of the human's top picks exactly.
- **Neither algorithm perfectly replicated human judgment.** That's expected. Both are useful; neither is a replacement for a human eye.

### Why upload to a cloud AI instead of just asking yourself?

A human reviewer looking at 75 tiny thumbnails in a grid can be inconsistent — tired, rushed,
or unconsciously biased toward frames they already saw highlighted by the algorithm. A cloud
AI applies the same criteria uniformly across all frames in one pass. It's also faster to
iterate: change the contact sheet, re-upload, get new picks in seconds.

This is **human-in-the-loop** AI use: the AI does the tedious comparison work; the human
decides whether the AI's criteria match what matters.

### AI-assisted coding

Almost all the code in this feature — `monitor/pick_best.py`, the new routes in
`monitor/web_timelapse.py`, the changes to `sunset_timelapse.py`, and this documentation —
was written by Claude (the same AI used for verification) through a conversation in the
Claude Code CLI tool.

The engineering decisions were made by the human:
- "Show both OpenCV and CLIP picks side by side so I can compare"
- "Prefer frames with a few clouds over a featureless clear sky"
- "Clicking a thumbnail should open a full viewer, not just zoom in"
- "Re-extracting frames from the MP4 is wasteful — subsample the originals"

The implementation decisions were made by Claude:
- Uniform subsampling to 100 frames instead of re-extracting from MP4
- Min-max normalization to make CLIP and OpenCV scores comparable
- Server-side file copy for set-snapshot to avoid the browser 16KB form limit
- The torchvision/Python 3.13 operator registration patch

**What does this mean for CS?** It means the boundary between "knowing how to code" and
"knowing what to build" has shifted. You still need to understand what a cosine similarity
is, why min-max normalization works, and what the 16KB form limit means — otherwise you
can't evaluate whether the AI's solution is correct. But you don't need to remember the
exact OpenCV function call for Canny edge detection.

### What the AI got wrong

Not everything worked on the first try:
- **The torchvision bug** (Python 3.13 incompatibility) was diagnosed by Claude but required
  several attempts before the correct monkey-patch was found.
- **The `grid` reference bug** — when the UI was refactored to show two scorers, a stale
  reference to `document.getElementById('best-frames-grid')` caused the Best Frames button
  to silently fail on pages where it hadn't been used before. Claude introduced the bug and
  Claude found it.
- **The form size limit** — the first implementation POSTed the full JPEG through the browser,
  hitting Flask's 16KB default form limit. The fix (server-side file copy) only emerged after
  the error actually occurred in testing.

**Pattern:** AI-assisted coding still requires testing, debugging, and iteration. The AI
compresses the time to a working first draft but does not eliminate the need to understand
what is happening.

---

## Summary: What Did We Actually Build?

A pipeline that:
1. Captures ~1,440 JPEG frames from an RTSP camera during the 2-hour sunset window
2. Assembles them into an MP4 timelapse (H.264, CRF 33, 24 fps)
3. After assembly, uniformly subsamples 100 frames from the originals (no re-extraction)
4. Applies a night-mode IR cutoff (drops frames below 20% of peak saturation)
5. Scores remaining frames with CLIP (ViT-B/32, ~80s on Pi 4 CPU) using tuned prompts
6. Falls back to OpenCV heuristics if CLIP fails
7. Saves the single best frame as the day's snapshot thumbnail
8. Separately (on demand, via the web UI) runs both algorithms and saves 4 picks each
9. Displays both sets of picks side-by-side for human review and manual snapshot selection

The snapshot displayed in the "All Timelapses" list and used in the timelapse completion
email is now the CLIP-selected best frame rather than a fixed time offset.

---

## AP CSP Connections

| Concept | Where it appears |
|---------|-----------------|
| **Data representation** | Images as 3D arrays; HSV vs RGB color spaces |
| **Algorithms** | Heuristic scoring; greedy diversity selection; uniform subsampling |
| **Abstraction** | `pick_best_snapshot()` hides CLIP/OpenCV choice from the caller |
| **Collaboration** | Human + algorithm + cloud AI working together on the same problem |
| **Beneficial/harmful effects of computing** | AI-assisted coding: productivity gain vs. need to understand output |
| **Undecidable problems** | "Best photo" has no ground truth; human judgment is the oracle |
| **Bias in algorithms** | OpenCV rewards saturated colors; misses stark/dramatic scenes |
| **Testing and debugging** | Contact sheet verification; real bugs introduced and fixed |

---

## Extension Activities

1. **Change the prompts.** Add or remove one CLIP prompt and re-run on the April 4 timelapse.
   Does the selection change? Why or why not?

2. **Write a new heuristic.** Add a "golden ratio thirds" term: does the horizon line fall
   in the top or bottom third of the frame? Weight it and see if it helps.

3. **Compare algorithms across multiple days.** Run both scorers on 5 different dates. For
   each, upload the contact sheet to a cloud AI and collect picks. Build a small table: how
   often does OpenCV match the cloud AI? How often does CLIP? Is one consistently better?

4. **The normalization problem.** On an overcast day with no vivid colors, CLIP scores might
   cluster very tightly (e.g. 0.010, 0.011, 0.012). After normalization, the "best" frame
   shows 100% even though all frames are essentially equal. How would you detect this and
   communicate it to the user?

5. **Model size tradeoff.** ViT-B/32 takes ~80s on the Pi. ViT-L/14 is more accurate but
   ~4× larger and slower. How would you decide which to use without just trying both?
