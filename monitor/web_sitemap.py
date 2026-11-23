"""
/sitemap — auto-generated table of every route, built from Flask's live URL
map so it can't drift. Itself login-protected (it lists internal endpoints).

Access is read from a ``_auth`` attribute set by ``monitor.passkeys``:
  requires_auth  -> 'login'   (passkey session or Basic auth)
  inline_auth    -> 'login'   (route checks is_authed() itself)
Anything else is 'public', except a few token-in-URL routes listed below.
"""
import html
import re

from flask import Blueprint, Response, current_app

from monitor.passkeys import direct_exempt, requires_auth

sitemap_bp = Blueprint('sitemap', __name__)

TOKEN_ROUTES = {'control'}                 # endpoint names guarded by a secret URL token
MEDIA_EXT = ('.jpg', '.png', '.bmp', '.mp4', '.wav')
MEDIA_HINTS = ('serve', 'clips', 'hero-photos', 'best-frame', '/frame', '/snapshot',
               'ring-snapshot', 'snapshot')
# endpoint -> kind overrides where the path heuristic is wrong
KIND_OVERRIDE = {
    'ping': 'Utility', 'uptime_redirect': 'Utility', 'passkeys.logout': 'Utility',
    'passkeys.login_basic': 'Utility', 'passkeys.login_page': 'Page',
    'passkeys.manage_page': 'Page', 'control': 'Action',
    'watch_bypass_flow': 'Page', 'watch_pressure_low': 'Page',
    'timelapse.timelapse_best_frame_view': 'Page',
    'water_status': 'Page',
}


# Hand-reviewed risk ratings, keyed by endpoint: (level, reason). level is
# 'high' | 'med' | 'low'. Public routes that change state and have no entry here
# are auto-rated 'med' security. Review when handlers change — these are
# judgements from reading the code (2026-11-23), not scanner output.
SEC = {
    'control': ('high', 'Changes relays via GET with the secret in the URL (logs, history, Referer); also needs login or maintenance mode'),
    'ring.delete_archive': ('high', 'Public delete of archived Ring photos'),
    'timelapse.timelapse_rate': ('low', 'Public by design; guest ratings (normally handled by the Cloudflare Worker, direct access needs login)'),
    'timelapse.timelapse_frame_view': ('low', 'Public POST that echoes an uploaded image back into a page'),
    'water_status': ('med', 'While maintenance sharing is ON, logged-out visitors get the full view incl. relay controls'),
    'ring_snapshot': ('low', 'Live Ring image; login or maintenance mode required'),
    'ring_snapshot_api': ('med', 'Live Ring image for anyone with maintenance mode on, or a signed 24 h link'),
    'ring.archive_data': ('med', 'Public index of archived Ring photos'),
    'ring.serve_archive': ('med', 'Public Ring photos (people, vehicles)'),
    'ring.ring_review': ('med', 'Public Ring photo review page'),
    'passkeys.login_basic': ('med', 'Basic auth has no lockout or rate limit; password is a known default'),
    'passkeys.login_options': ('low', 'Public by design; passkey login'),
    'passkeys.login_verify': ('low', 'Public by design; passkey login'),
}
DOS = {
    'dosatron.api_live': ('high', 'Spawns arecord + encoder per request and streams; only 16 worker threads total'),
    'timelapse.timelapse_pick_best': ('low', 'Starts a frame-scoring job; login required'),
    'chart_data': ('med', '?hours unbounded; re-reads snapshots.csv every call'),
    'timelapse.snapshot': ('med', 'Runs ffmpeg (up to 15 s) per uncached request; Cloudflare caches, :6443 does not'),
    'epaper_bmp': ('med', '8-min cache, but query overrides (hours/occupied/threshold) skip it and re-render'),
    'epaper_jpg': ('med', 'Renders a 4x image; varying query params gives distinct cache keys'),
    'sunset': ('med', 'Matplotlib render per request; no cache'),
    'dashboard': ('med', 'Heavy page: tank API call + CSV reads'),
    'water2.api_flow_status': ('med', 'Reads CSV/JSONL history on each call'),
    'dosatron.api_training_clips': ('med', 'Scans the full detections JSONL per call'),
    'dosatron.api_cycles': ('med', 'Loads and sorts all cycle records per call'),
    'dosatron.api_prediction': ('med', 'Loads cycle history per call'),
    'timelapse.timelapse_view': ('med', 'Very large handler; serves big MP4s, holding a thread'),
    'timelapse.timelapse_latest_mp4': ('med', 'Serves a large MP4, holding a thread'),
    'timelapse.timelapse_test_mp4': ('med', 'Serves a large MP4, holding a thread'),
    'timelapse.timelapse_frame_view': ('low', 'Public POST; body capped at 8 MB'),
    'ring_snapshot': ('low', 'Cached; Ring API is called at most once per cache window'),
    'ring.archive_data': ('low', 'Directory scan per call'),
    'ring.serve_archive': ('low', 'Reads the whole file into memory'),
    'passkeys.login_options': ('low', 'Unauthenticated, no rate limit'),
    'passkeys.login_verify': ('low', 'Unauthenticated, no rate limit; signature check per call'),
}


def _risks(endpoint, access, mutates):
    sec = SEC.get(endpoint)
    if not sec and mutates and access in ('public', 'tunnel'):
        sec = ('med', 'Public state change, no login')
    return sec, DOS.get(endpoint)


def _kind(rule, endpoint, methods):
    if endpoint in KIND_OVERRIDE:
        return KIND_OVERRIDE[endpoint]
    p = rule.rule
    if p.endswith(MEDIA_EXT) or any(h in p for h in MEDIA_HINTS):
        return 'Media'
    if p.startswith(('/api/', '/passkey/')):
        return 'API'
    if methods - {'GET'}:
        return 'API'            # form posts / mutations without an /api prefix
    return 'Page'


def _access(fn, endpoint, path):
    if getattr(fn, '_auth', None):
        return fn._auth if fn._auth in ('maint', 'mixed') else 'login'
    bp = current_app.blueprints.get(endpoint.split('.')[0]) if '.' in endpoint else None
    if bp is not None and getattr(bp, '_auth', None):
        return 'login'
    if endpoint in TOKEN_ROUTES:
        return 'token'
    # Non-API pages are public only via the Cloudflare tunnel; direct access
    # needs login (see passkeys.require_login_for_direct_access).
    return 'public' if direct_exempt(path) else 'tunnel'


def _desc(fn):
    doc = (fn.__doc__ or '').strip()
    line = next((l.strip() for l in doc.splitlines() if l.strip()), '')
    return line[:140]


EXPOSURE_RANK = {'public': 0, 'tunnel': 1, 'mixed': 2, 'token': 3, 'maint': 4, 'login': 5}
LEVEL_RANK = {'high': 0, 'med': 1, 'low': 2, None: 3}


def collect_routes():
    rows = []
    for rule in current_app.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        fn = current_app.view_functions[rule.endpoint]
        methods = set(rule.methods) - {'HEAD', 'OPTIONS'}
        access = _access(fn, rule.endpoint, rule.rule)
        mutates = bool(methods & {'POST', 'PUT', 'PATCH', 'DELETE'})
        sec, dos = _risks(rule.endpoint, access, mutates)
        rows.append({
            'sec': sec, 'dos': dos,
            'path': rule.rule,
            'methods': sorted(methods),
            'kind': _kind(rule, rule.endpoint, methods),
            'access': access,
            'desc': _desc(fn),
            'mutates': mutates,
            'linkable': 'GET' in methods and '<' not in rule.rule,
        })
    # Default order: least-protected first, then worst security risk, DoS risk, path.
    rows.sort(key=lambda r: (EXPOSURE_RANK[r['access']],
                             LEVEL_RANK[r['sec'][0] if r['sec'] else None],
                             LEVEL_RANK[r['dos'][0] if r['dos'] else None],
                             r['path'], r['methods']))
    return rows


_CSS = """
:root{--bg:#fff;--fg:#1c1c1e;--mut:#6b6b70;--line:#e2e2e6;--row:#f6f6f8;
--pub:#8a5a00;--pubbg:#fff1d6;--log:#0b6b3a;--logbg:#d9f3e3;--tok:#5b3ab0;--tokbg:#e9e1fb;
--warn:#fde2e2}
@media(prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mut:#9a9aa2;--line:#2c2c30;--row:#1a1a1d;
--pub:#ffcf70;--pubbg:#3a2c0a;--log:#7fe0aa;--logbg:#0f3322;--tok:#c4b0ff;--tokbg:#2a2050;--warn:#3d1a1a}}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:16px}
h1{margin:0 0 4px}p.s{color:var(--mut);margin:0 0 12px}
.f{display:flex;flex-wrap:wrap;gap:8px 16px;margin:12px 0}.f label{cursor:pointer}
input[type=search]{padding:8px;border-radius:6px;border:1px solid var(--line);background:var(--bg);
color:var(--fg);min-width:200px}
.w{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{position:sticky;top:0;background:var(--bg);cursor:pointer;user-select:none}th.s::after{content:' \\25B2'}th.s.d::after{content:' \\25BC'}tr:nth-child(even){background:var(--row)}
tr.warn{background:var(--warn)}
code{font-family:ui-monospace,monospace;font-size:13px}a{color:inherit}
.b{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;white-space:nowrap}
.public{color:var(--pub);background:var(--pubbg)}.login{color:var(--log);background:var(--logbg)}
.token,.maint,.mixed{color:var(--tok);background:var(--tokbg)}.tunnel{color:var(--pub);background:var(--pubbg)}
.m{color:var(--mut);font-size:12px}
.high{color:#fff;background:#c0392b}.med{color:#5a3b00;background:#ffc861}.low{color:var(--mut);background:var(--row);border:1px solid var(--line)}
@media(prefers-color-scheme:dark){.med{color:#ffd98a;background:#4a3508}}
.note{background:var(--row);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:12px 0;font-size:14px}
.note li{margin:3px 0}td.r{min-width:150px}
"""

_JS = """
const q=document.getElementById('q'),rows=[...document.querySelectorAll('tbody tr')],
cks=[...document.querySelectorAll('input[data-k]')];
function go(){const t=q.value.toLowerCase();const on={};cks.forEach(c=>{(on[c.dataset.k]??=new Set());
if(c.checked)on[c.dataset.k].add(c.value)});let n=0;
rows.forEach(r=>{const ok=r.textContent.toLowerCase().includes(t)&&on.kind.has(r.dataset.kind)&&
on.access.has(r.dataset.access)&&(!document.getElementById('wo').checked||r.dataset.risk==='1');
r.hidden=!ok;if(ok)n++});document.getElementById('n').textContent=n+' of '+rows.length}
const tb=document.querySelector('tbody'),ths=[...document.querySelectorAll('th[data-c]')];
const val=(r,c)=>{const t=r.children[c];return t.dataset.v!==undefined?t.dataset.v:t.textContent.trim().toLowerCase()};
ths.forEach(h=>h.onclick=()=>{const c=+h.dataset.c,d=h.classList.contains('s')&&!h.classList.contains('d');
ths.forEach(x=>x.classList.remove('s','d'));h.classList.add('s');if(d)h.classList.add('d');
const num=rows.every(r=>val(r,c)!==''&&!isNaN(val(r,c)));
[...rows].sort((a,b)=>{const x=val(a,c),y=val(b,c);const k=num?x-y:String(x).localeCompare(String(y));return d?-k:k}).forEach(r=>tb.appendChild(r))});
q.oninput=go;cks.forEach(c=>c.onchange=go);document.getElementById('wo').onchange=go;go();
"""


@sitemap_bp.route('/sitemap')
@requires_auth
def sitemap_page():
    """Table of every route: end-user page vs API/media, and public vs login-required."""
    rows = collect_routes()
    body = []
    for r in rows:
        warn = r['mutates'] and r['access'] in ('public', 'tunnel')
        p = html.escape(r['path'])
        path = f'<a href="{p}"><code>{p}</code></a>' if r['linkable'] else f'<code>{p}</code>'
        label = {'public': 'Public', 'login': 'Login required', 'token': 'Secret URL token', 'maint': 'Login or maintenance mode', 'mixed': 'Public view; full view if login/maintenance', 'tunnel': 'Public via Cloudflare; login if direct'}[r['access']]
        note = '<div class=m>&#9888; changes state, no login</div>' if warn else ''
        def cell(r_):
            if not r_:
                return '<td class=r data-v=3><span class=m>&mdash;</span></td>'
            return (f'<td class=r data-v={LEVEL_RANK[r_[0]]}><span class="b {r_[0]}">{r_[0].upper()}</span>'
                    f'<div class=m>{html.escape(r_[1])}</div></td>')
        risky = '1' if (r['sec'] and r['sec'][0] in ('high', 'med')) or (r['dos'] and r['dos'][0] in ('high', 'med')) else '0'
        body.append(
            f'<tr data-risk="{risky}" data-kind="{r["kind"]}" data-access="{r["access"]}"{" class=warn" if warn else ""}>'
            f'<td data-v="{html.escape(r["path"])}">{path}</td><td>{html.escape(" ".join(r["methods"]))}</td><td>{r["kind"]}</td>'
            f'<td data-v={EXPOSURE_RANK[r["access"]]}><span class="b {r["access"]}">{label}</span>{note}</td>'
            f'{cell(r["sec"])}{cell(r["dos"])}<td>{html.escape(r["desc"])}</td></tr>')
    kinds = ['Page', 'API', 'Media', 'Action', 'Utility']
    kcb = ''.join(f'<label><input type=checkbox data-k=kind value="{k}" checked> {k}</label>' for k in kinds)
    acb = ''.join(f'<label><input type=checkbox data-k=access value="{a}" checked> {l}</label>'
                  for a, l in (('public', 'Public'), ('login', 'Login'), ('maint', 'Login/maint'), ('mixed', 'Mixed'), ('tunnel', 'Cloudflare-only'), ('token', 'Token')))
    n_warn = sum(1 for r in rows if r['mutates'] and r['access'] == 'public')
    page = (f'<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
            f'<title>Sitemap</title><style>{_CSS}</style><h1>Sitemap</h1>'
            f'<p class=s>Generated from the live route table. Page = for people; API/Media = data for scripts and pages; '
            f'Action = triggers a relay. Sorted least-protected first; click a column header to re-sort. <span id=n></span> shown.</p>'
            f'<div class=f><input id=q type=search placeholder="Filter…">{kcb}{acb}'
            f'<label><input type=checkbox id=wo> Only HIGH/MED risk</label></div>'
            f'<div class=note><b>Reading the risk columns.</b> Hand-reviewed from the code, not a scanner; see SEC/DOS in web_sitemap.py.'
            f'<ul><li><b>Global DoS exposure:</b> no rate limiting anywhere, and gunicorn has 2 workers &times; 8 threads = 16 concurrent requests, '
            f'so a few slow requests can stall the whole site. Hostnames behind Cloudflare get some protection; '
            f'<code>pumphouse.&hellip;:6443</code> (DNS-only) gets none.</li>'
            f'<li><b>Global:</b> request bodies are capped at 8 MB; Basic auth (accepted on login routes) has no lockout.</li>'
            f'<li><b>Cloudflare vs direct:</b> non-API pages are public only when they arrive through the Cloudflare tunnel (CF-Ray header <i>and</i> a loopback connection, so it can&rsquo;t be faked); direct visits to them &mdash; LAN or the :6443 hostname &mdash; require login. <code>/api/*</code>, <code>/ping</code> and the login pages are exempt for devices and scripts.</li></ul></div>'
            f'<div class=w><table><thead><tr><th data-c=0>Path<th data-c=1>Methods<th data-c=2>Type<th data-c=3>Access<th data-c=4>Security risk<th data-c=5>DoS risk<th data-c=6>What it does</thead>'
            f'<tbody>{"".join(body)}</tbody></table></div><script>{_JS}</script>')
    return Response(page, mimetype='text/html')
