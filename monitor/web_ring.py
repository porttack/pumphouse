"""
Ring camera archive review blueprint.

Routes:
    GET  /ring-review                         Review page (HTML)
    GET  /api/ring-archive-data               JSON: days + reference status
    GET  /api/ring-archive/<date>/<filename>  Serve archived JPEG
    POST /api/ring-approve                    Copy archive image → reference slot
    DELETE /api/ring-archive/<date>/<file>    Delete from archive
"""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

ring_bp = Blueprint('ring', __name__)

_ARCHIVE_DIR = Path.home() / '.config' / 'pumphouse' / 'ring_archive'
_REF_DIR     = Path.home() / '.config' / 'pumphouse' / 'ring_reference'
_DATE_RE     = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_FILE_RE     = re.compile(r'^\d{4}\.jpg$')


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ring Archive Review</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1a1a1a;color:#e0e0e0;font-family:'Consolas','Monaco','Courier New',monospace;padding:16px}
a{color:#4CAF50;text-decoration:none}
h1{color:#4CAF50;font-size:1.3em;margin-bottom:4px}
.subtitle{color:#888;font-size:0.8em;margin-bottom:20px}

/* reference coverage grid */
.ref-section{background:#252525;border-radius:6px;padding:12px;margin-bottom:24px}
.ref-title{color:#888;font-size:0.8em;margin-bottom:10px}
.ref-grid{display:flex;flex-wrap:wrap;gap:6px}
.ref-slot{width:48px;text-align:center;padding:4px 2px;border-radius:4px;font-size:0.72em;cursor:default}
.ref-slot.has{background:#1e3b1e;color:#4CAF50;border:1px solid #4CAF50}
.ref-slot.none{background:#2a2a2a;color:#555;border:1px solid #383838}
.ref-slot .hr{font-weight:bold;font-size:1em}
.ref-slot .age{font-size:0.85em;margin-top:2px;color:#777}

/* day groups */
.day-group{margin-bottom:28px}
.day-hdr{color:#4CAF50;font-size:0.95em;margin-bottom:10px;padding-bottom:5px;border-bottom:1px solid #2e2e2e}
.thumb-grid{display:flex;flex-wrap:wrap;gap:10px}

/* thumbnail cards */
.card{background:#252525;border-radius:6px;overflow:hidden;width:168px;border:2px solid transparent;transition:border-color .15s}
.card:hover{border-color:#555}
.card.is-ref{border-color:#4CAF50}
.card img{width:168px;height:95px;object-fit:cover;display:block;cursor:pointer}
.card-label{padding:4px 7px 2px;font-size:0.78em;display:flex;justify-content:space-between;align-items:center}
.card-label .t{color:#4CAF50}
.ref-badge{font-size:0.75em;background:#1e3b1e;color:#4CAF50;padding:1px 5px;border-radius:3px}
.card-btns{display:flex;gap:4px;padding:4px 7px 7px}
.btn-ref{flex:1;background:#1e3b1e;color:#4CAF50;border:1px solid #4CAF50;border-radius:3px;padding:3px 0;font-size:0.73em;cursor:pointer;font-family:inherit}
.btn-ref:hover{background:#2a5a2a}
.btn-del{background:#3b1e1e;color:#f44336;border:1px solid #f44336;border-radius:3px;padding:3px 7px;font-size:0.73em;cursor:pointer;font-family:inherit}
.btn-del:hover{background:#5a2a2a}

/* modal */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.88);z-index:100;align-items:center;justify-content:center;flex-direction:column;gap:12px}
.modal.open{display:flex}
.modal img{max-width:95vw;max-height:78vh;border-radius:4px;display:block}
.modal-meta{color:#aaa;font-size:0.85em}
.modal-btns{display:flex;gap:10px}
.modal-btn-ref{background:#1e3b1e;color:#4CAF50;border:1px solid #4CAF50;border-radius:4px;padding:5px 18px;font-size:0.85em;cursor:pointer;font-family:inherit}
.modal-btn-ref:hover{background:#2a5a2a}
.modal-btn-del{background:#3b1e1e;color:#f44336;border:1px solid #f44336;border-radius:4px;padding:5px 18px;font-size:0.85em;cursor:pointer;font-family:inherit}
.modal-btn-del:hover{background:#5a2a2a}
.modal-close{position:absolute;top:14px;right:18px;font-size:2em;color:#888;cursor:pointer;line-height:1}
.modal-close:hover{color:#fff}

/* toast */
.toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:#252525;color:#4CAF50;border:1px solid #4CAF50;padding:7px 22px;border-radius:20px;font-size:0.83em;opacity:0;transition:opacity .25s;z-index:200;pointer-events:none}
.toast.show{opacity:1}

.empty{color:#555;margin-top:16px;font-size:0.9em}
</style>
</head>
<body>
<h1><a href="/">&#8592;</a> Ring Archive Review</h1>
<div class="subtitle">Manually approve empty-driveway images as reference baselines for vehicle detection</div>

<div class="ref-section">
  <div class="ref-title">Reference coverage &mdash; one baseline image per hour slot (approve images below to fill gaps)</div>
  <div class="ref-grid" id="ref-grid"></div>
</div>

<div id="archive"></div>

<div class="modal" id="modal">
  <span class="modal-close" onclick="closeModal()">&#215;</span>
  <img id="modal-img" src="" alt="">
  <div class="modal-meta" id="modal-meta"></div>
  <div class="modal-btns">
    <button class="modal-btn-ref" onclick="approveModal()">Set as reference</button>
    <button class="modal-btn-del" onclick="deleteModal()">Delete</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let data = null;
let mDate = null, mFile = null;

async function load() {
  const r = await fetch('/api/ring-archive-data');
  data = await r.json();
  renderRefs();
  renderArchive();
}

function renderRefs() {
  const g = document.getElementById('ref-grid');
  g.innerHTML = '';
  for (let h = 0; h < 24; h++) {
    const ref = data.references[h];
    const el = document.createElement('div');
    el.className = 'ref-slot ' + (ref ? 'has' : 'none');
    const lbl = h === 0 ? '12a' : h < 12 ? h+'a' : h === 12 ? '12p' : (h-12)+'p';
    el.innerHTML = '<div class="hr">'+lbl+'</div><div class="age">'+(ref ? ref.age : '&mdash;')+'</div>';
    if (ref) el.title = 'Set: ' + ref.mtime + (ref.from ? '\\nFrom: ' + ref.from : '');
    g.appendChild(el);
  }
}

function renderArchive() {
  const c = document.getElementById('archive');
  c.innerHTML = '';
  if (!data.days.length) {
    c.innerHTML = '<p class="empty">No archived images yet — images will appear here every 15 minutes during daylight.</p>';
    return;
  }
  for (const day of data.days) {
    const grp = document.createElement('div');
    grp.className = 'day-group';
    const hdr = document.createElement('div');
    hdr.className = 'day-hdr';
    hdr.textContent = day.date + ' — ' + day.images.length + ' image' + (day.images.length !== 1 ? 's' : '');
    grp.appendChild(hdr);
    const grid = document.createElement('div');
    grid.className = 'thumb-grid';
    for (const img of day.images) {
      const ref = data.references[img.hour];
      const isRef = ref && ref.from === day.date + '/' + img.filename;

      const card = document.createElement('div');
      card.className = 'card' + (isRef ? ' is-ref' : '');

      const imgEl = document.createElement('img');
      imgEl.src = '/api/ring-archive/' + day.date + '/' + img.filename;
      imgEl.loading = 'lazy';
      imgEl.alt = img.time_str;
      imgEl.addEventListener('click', (function(d,f,t){return function(){openModal(d,f,t);};})(day.date,img.filename,img.time_str));

      const lbl = document.createElement('div');
      lbl.className = 'card-label';
      const tspan = document.createElement('span');
      tspan.className = 't';
      tspan.textContent = img.time_str;
      lbl.appendChild(tspan);
      if (isRef) {
        const badge = document.createElement('span');
        badge.className = 'ref-badge';
        badge.textContent = 'ref';
        lbl.appendChild(badge);
      }

      const btns = document.createElement('div');
      btns.className = 'card-btns';
      const btnRef = document.createElement('button');
      btnRef.className = 'btn-ref';
      btnRef.textContent = 'Set as ref';
      btnRef.addEventListener('click', (function(d,f){return function(e){e.stopPropagation();approve(d,f);};})(day.date,img.filename));
      const btnDel = document.createElement('button');
      btnDel.className = 'btn-del';
      btnDel.textContent = '×';
      btnDel.addEventListener('click', (function(d,f){return function(e){e.stopPropagation();del(d,f);};})(day.date,img.filename));
      btns.appendChild(btnRef);
      btns.appendChild(btnDel);

      card.appendChild(imgEl);
      card.appendChild(lbl);
      card.appendChild(btns);
      grid.appendChild(card);
    }
    grp.appendChild(grid);
    c.appendChild(grp);
  }
}

function openModal(date, file, timeStr) {
  mDate = date; mFile = file;
  document.getElementById('modal-img').src = '/api/ring-archive/' + date + '/' + file;
  document.getElementById('modal-meta').textContent = date + '  ' + timeStr;
  document.getElementById('modal').classList.add('open');
}
function closeModal() { document.getElementById('modal').classList.remove('open'); }

async function approve(date, file) {
  const r = await fetch('/api/ring-approve', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({date, filename: file})
  });
  if (r.ok) { toast('Reference set ✓'); await load(); }
  else toast('Error: ' + await r.text());
}

async function del(date, file) {
  if (!confirm('Delete ' + date + ' ' + file + '?')) return;
  const r = await fetch('/api/ring-archive/' + date + '/' + file, {method: 'DELETE'});
  if (r.ok) { toast('Deleted'); await load(); }
  else toast('Error');
}

async function approveModal() { await approve(mDate, mFile); closeModal(); }
async function deleteModal()  { await del(mDate, mFile);    closeModal(); }

document.getElementById('modal').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}

load();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@ring_bp.route('/ring-review')
def ring_review():
    return Response(_PAGE, mimetype='text/html')


@ring_bp.route('/api/ring-archive-data')
def archive_data():
    days = []
    if _ARCHIVE_DIR.exists():
        for day_dir in sorted(_ARCHIVE_DIR.iterdir(), reverse=True):
            if not day_dir.is_dir() or not _DATE_RE.match(day_dir.name):
                continue
            images = []
            for f in sorted(day_dir.glob('????.jpg')):
                if not _FILE_RE.match(f.name):
                    continue
                hour   = int(f.stem[:2])
                minute = int(f.stem[2:])
                dt = datetime.strptime(f'{day_dir.name} {f.stem}', '%Y-%m-%d %H%M')
                images.append({
                    'filename': f.name,
                    'hour':     hour,
                    'time_str': dt.strftime('%-I:%M %p'),
                })
            if images:
                days.append({'date': day_dir.name, 'images': images})

    references = {}
    if _REF_DIR.exists():
        for h in range(24):
            ref_jpg  = _REF_DIR / f'{h:02d}.jpg'
            ref_json = _REF_DIR / f'{h:02d}.json'
            if not ref_jpg.exists():
                continue
            mtime    = datetime.fromtimestamp(ref_jpg.stat().st_mtime)
            age_days = (datetime.now() - mtime).days
            source   = None
            if ref_json.exists():
                try:
                    source = json.loads(ref_json.read_text()).get('from')
                except Exception:
                    pass
            references[h] = {
                'mtime': mtime.strftime('%b %-d %-I:%M %p'),
                'age':   'today' if age_days == 0 else f'{age_days}d',
                'from':  source,
            }

    return jsonify({'days': days, 'references': references})


@ring_bp.route('/api/ring-archive/<date_str>/<filename>')
def serve_archive(date_str, filename):
    if not _DATE_RE.match(date_str) or not _FILE_RE.match(filename):
        return Response('Invalid path', status=400)
    path = _ARCHIVE_DIR / date_str / filename
    if not path.exists():
        return Response('Not found', status=404)
    return Response(path.read_bytes(), mimetype='image/jpeg',
                    headers={'Cache-Control': 'public, max-age=86400'})


@ring_bp.route('/api/ring-approve', methods=['POST'])
def approve():
    body      = request.get_json(force=True) or {}
    date_str  = body.get('date', '')
    filename  = body.get('filename', '')
    if not _DATE_RE.match(date_str) or not _FILE_RE.match(filename):
        return Response('Invalid', status=400)
    src = _ARCHIVE_DIR / date_str / filename
    if not src.exists():
        return Response('Not found', status=404)

    hour = int(filename[:2])
    _REF_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, _REF_DIR / f'{hour:02d}.jpg')
    (_REF_DIR / f'{hour:02d}.json').write_text(
        json.dumps({'from': f'{date_str}/{filename}'})
    )
    return Response('ok')


@ring_bp.route('/api/ring-archive/<date_str>/<filename>', methods=['DELETE'])
def delete_archive(date_str, filename):
    if not _DATE_RE.match(date_str) or not _FILE_RE.match(filename):
        return Response('Invalid path', status=400)
    path = _ARCHIVE_DIR / date_str / filename
    if not path.exists():
        return Response('Not found', status=404)
    path.unlink()
    try:
        if not any(path.parent.iterdir()):
            path.parent.rmdir()
    except Exception:
        pass
    return Response('ok')
