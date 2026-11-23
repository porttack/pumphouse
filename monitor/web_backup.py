"""
Backup status page — Flask blueprint, mounted at /backup (auth required).

Reads ~/.local/share/pumphouse/backup_log.csv, written by bin/restic_backup.py
after every nightly restic run to Cloudflare R2. Shows what was uploaded per
day and per month, the current repository size, and a rough R2 storage-cost
estimate so it can be compared with the Cloudflare bill.
"""
import csv
import html
import json
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

from flask import Blueprint, Response, request

backup_bp = Blueprint('backup', __name__)

LOG_CSV = Path.home() / '.local/share/pumphouse/backup_log.csv'
DETAIL_JSONL = Path.home() / '.local/share/pumphouse/backup_detail.jsonl'

NOT_BACKED_UP = [
    ('Python virtualenvs (venv)', 'rebuild from requirements.txt'),
    ('OS and installed packages (/usr/lib, /usr/share, /usr/bin)', 'reinstall from the saved apt package list'),
    ('System logs (/var/log, ~4 GB) and caches', 'regenerated'),
    ('VS Code server and extensions', 'reinstalled on connect'),
    ('Docker data', 'no containers or images exist'),
]

# Cloudflare R2 standard-storage list prices (check dash for changes).
R2_STORAGE_PER_GB_MONTH = 0.015
R2_FREE_GB = 10.0
SAVED = ('ok', 'WARN')   # a snapshot reached R2 (WARN = cleanup step had a problem)
NEXT_RUN_HHMM = (3, 30)  # nightly cron time


def _mb(n):
    return f'{n / 1e6:,.1f} MB' if n < 1e9 else f'{n / 1e9:,.2f} GB'


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _load():
    if not LOG_CSV.exists():
        return []
    with open(LOG_CSV, newline='') as f:
        return list(csv.DictReader(f))


def _load_detail():
    out = {}
    if DETAIL_JSONL.exists():
        for line in DETAIL_JSONL.read_text().splitlines():
            try:
                d = json.loads(line)
                out[d['snapshot']] = d
            except (ValueError, KeyError):
                pass
    return out


def _contents_html(detail):
    if not detail:
        return '<p class="note">Detail appears after the next backup run.</p>'
    d = detail
    rows = ''
    for label, g in sorted(d['groups'].items(), key=lambda kv: -kv[1]['bytes']):
        kids = ''.join(f'<tr><td class="sub">{html.escape(k)}</td><td></td><td class="n">{_mb(v)}</td><td colspan="2"></td></tr>'
                       for k, v in g['children'].items())
        chg = (f'+{g["added"]:,} / ~{g["modified"]:,} / -{g["removed"]:,}'
               if d.get('prev') else 'first run')
        rows += (f'<tr class="grp"><td><details><summary>{html.escape(label)}</summary></details></td>'
                 f'<td class="n">{g["files"]:,}</td><td class="n">{_mb(g["bytes"])}</td>'
                 f'<td class="n">{chg}</td><td class="n">{_mb(g["changed_bytes"]) if d.get("prev") else "—"}</td></tr>'
                 f'<tr class="kids"><td colspan="5"><details><summary class="sm">largest items</summary>'
                 f'<table>{kids}</table></details></td></tr>')
    total_files = sum(g['files'] for g in d['groups'].values())
    total_bytes = sum(g['bytes'] for g in d['groups'].values())
    top = ''.join(f'<tr><td class="path">{html.escape(t["path"])}</td><td>{html.escape(t["mod"])}</td>'
                  f'<td class="n">{_mb(t["bytes"])}</td></tr>' for t in d.get('top_changed', []))
    return (f'<div class="card"><table><tr><th>What</th><th>Files</th><th>Size</th>'
            f'<th>Changed this run (+new ~modified -removed)</th><th>Size of changed</th></tr>{rows}'
            f'<tr><th>Total</th><th class="n">{total_files:,}</th><th class="n">{_mb(total_bytes)}</th><th></th><th></th></tr></table></div>'
            + (f'<h2>Largest new/changed files in the latest run</h2><div class="card"><table>{top}</table></div>' if top else ''))


def _ago(dt, now):
    sec = (now - dt).total_seconds()
    if sec < 90:
        return 'just now'
    if sec < 5400:
        return f'{sec / 60:.0f} min ago'
    if sec < 172800:
        return f'{sec / 3600:.0f} h ago'
    return f'{sec / 86400:.0f} days ago'


def _summary_html(rows, repo_now, detail, est_now):
    now = datetime.now()
    if not rows:
        return '<div class="banner bad">No backup has run yet.</div>'
    last = rows[-1]
    last_saved = next((r for r in reversed(rows) if r['status'] in SAVED), None)
    saved_dt = datetime.fromisoformat(last_saved['ts']) if last_saved else None
    age_h = (now - saved_dt).total_seconds() / 3600 if saved_dt else 9e9

    # overall health
    if last['status'] not in SAVED or age_h > 50:
        level, msg = 'bad', ('Last backup FAILED' if last['status'] not in SAVED else 'No recent backup')
    elif last['status'] == 'WARN' or age_h > 30:
        level, msg = 'warn', ('Backup saved, but cleanup reported a problem' if last['status'] == 'WARN'
                              else 'Backup is overdue')
    else:
        level, msg = 'good', 'Backups healthy'
    sub = (f'Last good backup {_ago(saved_dt, now)} ({saved_dt:%a %b %-d, %-I:%M %p})'
           if saved_dt else 'No successful backup yet')
    banner = f'<div class="banner {level}"><b>{msg}</b><span>{sub}</span></div>'

    # last 30 days reliability
    cutoff = now - timedelta(days=30)
    recent = [r for r in rows if datetime.fromisoformat(r['ts']) >= cutoff]
    ok_n = sum(r['status'] == 'ok' for r in recent)
    warn_n = sum(r['status'] == 'WARN' for r in recent)
    fail_n = len(recent) - ok_n - warn_n
    problems = [r for r in recent if r['status'] != 'ok'][-5:]
    prob_html = ''
    if problems:
        items = ''.join(
            f'<li><b>{html.escape(r["ts"].replace("T", " "))}</b> '
            f'<span class="{"warn" if r["status"] == "WARN" else "bad"}">{html.escape(r["status"])}</span> '
            f'{html.escape(r["error"][:220])}</li>' for r in reversed(problems))
        prob_html = f'<div class="card probs"><b>Recent problems</b><ul>{items}</ul></div>'

    # next scheduled run
    nxt = now.replace(hour=NEXT_RUN_HHMM[0], minute=NEXT_RUN_HHMM[1], second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    nxt_h = (nxt - now).total_seconds() / 3600

    # uploads + growth
    week_ago = now - timedelta(days=7)
    up7 = sum(_int(r['bytes_added']) for r in rows
              if r['status'] in SAVED and datetime.fromisoformat(r['ts']) >= week_ago)
    with_repo = [r for r in rows if r['status'] in SAVED and r['repo_bytes']]
    growth = '—'
    growth_sub = 'needs a few days of data'
    if len(with_repo) >= 2:
        a, b = with_repo[0], with_repo[-1]
        days_span = (datetime.fromisoformat(b['ts']) - datetime.fromisoformat(a['ts'])).total_seconds() / 86400
        if days_span >= 1:
            per_day = (_int(b['repo_bytes']) - _int(a['repo_bytes'])) / days_span
            growth = f'{_mb(max(per_day, 0))}/day'
            room = R2_FREE_GB * 1e9 - repo_now
            if per_day <= 0:
                growth_sub = 'not growing'
            elif room <= 0:
                growth_sub = 'already past the free tier'
            else:
                d = room / per_day
                growth_sub = (f'~{d:.0f} days until free tier is full' if d < 730 else 'free tier lasts years')
    pct = min(100.0, 100.0 * repo_now / (R2_FREE_GB * 1e9))
    nfiles = sum(g['files'] for g in detail['groups'].values()) if detail else 0
    src = _int(last_saved['bytes_processed']) if last_saved else 0
    snaps_n = _int(with_repo[-1]['snapshots']) if with_repo and with_repo[-1]['snapshots'] else 0

    def tile(big, small, extra=''):
        return f'<div class="tile"><b>{big}</b><span>{small}</span>{extra}</div>'

    tiles = ''.join([
        tile(_ago(saved_dt, now) if saved_dt else '—', 'since last good backup'),
        tile(f'{nxt:%a %-I:%M %p}', f'next scheduled run (in {nxt_h:.0f} h)'),
        tile(f'{ok_n}/{len(recent)} ok', f'last 30 days · {warn_n} warnings · {fail_n} failed'),
        tile(_mb(repo_now), f'stored in R2 · {pct:.0f}% of {R2_FREE_GB:.0f} GB free tier',
             f'<i class="meter"><u style="width:{pct:.0f}%"></u></i>'),
        tile(_mb(up7), 'uploaded in the last 7 days'),
        tile(growth, 'repo growth rate', f'<span>{growth_sub}</span>'),
        tile(f'{snaps_n}', 'snapshots kept · 14 daily / 8 weekly / 12 monthly'),
        tile(_mb(src), f'source data · {nfiles:,} files' if nfiles else 'source data scanned'),
        tile(f'${est_now:,.2f}/mo', f'est. storage cost (first {R2_FREE_GB:.0f} GB free)'),
    ])
    return f'{banner}{prob_html}<div class="tiles">{tiles}</div>'


def _auth_ok():
    try:
        from monitor.web import check_auth
    except ImportError:
        return True
    a = request.authorization
    return bool(a and check_auth(a.username, a.password))


@backup_bp.route('/backup')
def backup_page():
    if not _auth_ok():
        return Response('Authentication required', 401,
                        {'WWW-Authenticate': 'Basic realm="Pumphouse"'})
    rows = _load()
    days = defaultdict(lambda: {'runs': 0, 'ok': 0, 'added': 0, 'processed': 0, 'new': 0,
                                'changed': 0, 'repo': 0})
    months = defaultdict(lambda: {'runs': 0, 'added': 0, 'repo_sum': 0, 'repo_n': 0,
                                  'repo_end': 0, 'failed': 0})
    for r in rows:
        d, m = r['ts'][:10], r['ts'][:7]
        dd, mm = days[d], months[m]
        dd['runs'] += 1
        mm['runs'] += 1
        if r['status'] in SAVED:
            dd['ok'] += 1
            dd['added'] += _int(r['bytes_added'])
            dd['new'] += _int(r['files_new'])
            dd['changed'] += _int(r['files_changed'])
            dd['processed'] = max(dd['processed'], _int(r['bytes_processed']))
            dd['repo'] = _int(r['repo_bytes'])
            mm['added'] += _int(r['bytes_added'])
            mm['repo_sum'] += _int(r['repo_bytes'])
            mm['repo_n'] += 1
            mm['repo_end'] = _int(r['repo_bytes'])
        else:
            mm['failed'] += 1

    detail_by_snap = _load_detail()
    latest_detail = None
    for r in reversed(rows):
        if r['status'] in SAVED and r['snapshot'] in detail_by_snap:
            latest_detail = detail_by_snap[r['snapshot']]
            break
    run_rows = ''
    for r in reversed(rows[-14:]):
        d = detail_by_snap.get(r['snapshot'])
        if d and d.get('prev'):
            parts = sorted(d['groups'].items(), key=lambda kv: -kv[1]['changed_bytes'])
            what = '; '.join(f'{html.escape(k.split(" (")[0])}: +{g["added"]}/~{g["modified"]}'
                             for k, g in parts if g['added'] or g['modified'] or g['removed'])[:300] or 'no changes'
        else:
            what = 'initial full backup' if d else ''
        st = {'ok': '<span class="good">ok</span>', 'WARN': '<span class="warn">warn</span>'}.get(
            r['status'], f'<span class="bad">{html.escape(r["status"])}</span>')
        run_rows += (f'<tr><td>{html.escape(r["ts"].replace("T", " "))}</td><td>{st}</td><td>{html.escape(r["snapshot"])}</td>'
                     f'<td class="n">{_mb(_int(r["bytes_added"]))}</td><td class="wrap">{what}</td></tr>')

    last = rows[-1] if rows else None
    last_ok = next((r for r in reversed(rows) if r['status'] in SAVED and r['repo_bytes']), None)
    last_saved = next((r for r in reversed(rows) if r['status'] in SAVED), None)
    repo_now = _int(last_ok['repo_bytes']) if last_ok else 0
    gb = repo_now / 1e9
    est_now = max(0.0, gb - R2_FREE_GB) * R2_STORAGE_PER_GB_MONTH

    day_keys = sorted(days)[-30:]
    peak = max([days[d]['added'] for d in day_keys] + [1])
    day_rows = ''.join(
        f'<tr><td>{d}</td><td>{days[d]["ok"]}/{days[d]["runs"]}</td>'
        f'<td class="n">{days[d]["new"]:,}</td><td class="n">{days[d]["changed"]:,}</td>'
        f'<td class="n">{_mb(days[d]["added"])}</td>'
        f'<td><span class="bar" style="width:{100 * days[d]["added"] / peak:.0f}%"></span></td>'
        f'<td class="n">{_mb(days[d]["repo"])}</td></tr>'
        for d in reversed(day_keys))

    month_rows = ''
    for m in sorted(months, reverse=True):
        mm = months[m]
        avg = mm['repo_sum'] / mm['repo_n'] if mm['repo_n'] else 0
        cost = max(0.0, avg / 1e9 - R2_FREE_GB) * R2_STORAGE_PER_GB_MONTH
        month_rows += (
            f'<tr><td>{m}</td><td class="n">{mm["runs"]}</td><td class="n">{mm["failed"]}</td>'
            f'<td class="n">{_mb(mm["added"])}</td><td class="n">{_mb(mm["repo_end"])}</td>'
            f'<td class="n">{_mb(avg)}</td><td class="n">${cost:,.2f}</td></tr>')

    if last:
        bad = last['status'] != 'ok'
        status = (f'<span class="{"bad" if bad else "good"}">{html.escape(last["status"])}</span> '
                  f'at {html.escape(last["ts"])} ({last["duration_s"]}s)'
                  + (f' — {html.escape(last["error"])}' if last['error'] else ''))
    else:
        status = 'No backup has run yet.'

    contents = _contents_html(latest_detail)
    summary = _summary_html(rows, repo_now, latest_detail, est_now)
    not_backed = ''.join(f'<tr><td>{html.escape(a)}</td><td>{html.escape(b)}</td></tr>' for a, b in NOT_BACKED_UP)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backup Status</title>
<style>
:root{{--bg:#fafafa;--fg:#1c1c1e;--mut:#6b6b70;--card:#fff;--bd:#e3e3e6;--acc:#2563eb;--ok:#15803d;--bad:#b91c1c;--warn:#b45309}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111113;--fg:#ececef;--mut:#9a9aa2;--card:#1b1b1f;--bd:#2c2c31;--acc:#60a5fa;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}}
main{{max-width:900px;margin:0 auto}}h1{{font-size:1.4rem;margin:.2rem 0 1rem}}
h2{{font-size:1rem;margin:1.6rem 0 .5rem;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.tile{{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px}}
.tile b{{display:block;font-size:1.35rem}}.tile span{{color:var(--mut);font-size:.8rem}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:10px 12px;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:.88rem}}th,td{{padding:5px 8px;text-align:left;border-bottom:1px solid var(--bd);white-space:nowrap}}
th{{color:var(--mut);font-weight:600}}td.n{{text-align:right;font-variant-numeric:tabular-nums}}
.bar{{display:inline-block;height:8px;min-width:1px;background:var(--acc);border-radius:4px;vertical-align:middle}}
.sub{{padding-left:28px;color:var(--mut)}}.path{{font-family:ui-monospace,monospace;font-size:.78rem;white-space:normal;word-break:break-all}}
.wrap{{white-space:normal;max-width:420px}}.grp td{{font-weight:600}}tr.kids td{{padding:0 8px;border-bottom:1px solid var(--bd)}}
details summary{{cursor:pointer;list-style:none}}summary.sm{{color:var(--mut);font-size:.78rem;font-weight:400}}
.banner{{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:baseline;padding:12px 14px;border-radius:10px;border:1px solid var(--bd);margin-bottom:12px}}
.banner b{{font-size:1.1rem}}.banner span{{color:var(--mut)}}
.banner.good{{border-color:var(--ok);background:color-mix(in srgb,var(--ok) 10%,var(--card))}}
.banner.warn{{border-color:var(--warn);background:color-mix(in srgb,var(--warn) 12%,var(--card))}}
.banner.bad{{border-color:var(--bad);background:color-mix(in srgb,var(--bad) 10%,var(--card))}}
.probs{{margin-bottom:12px;font-size:.85rem}}.probs ul{{margin:.3rem 0 0;padding-left:1.1rem}}.probs li{{margin:.2rem 0}}
.meter{{display:block;height:6px;background:var(--bd);border-radius:3px;margin-top:6px;overflow:hidden}}.meter u{{display:block;height:100%;background:var(--acc)}}
.warn{{color:var(--warn);font-weight:600}}
.good{{color:var(--ok);font-weight:600}}.bad{{color:var(--bad);font-weight:600}}.note{{color:var(--mut);font-size:.8rem;margin-top:.6rem}}
</style></head><body><main>
<h1>Pi backup → Cloudflare R2</h1>
{summary}
<h2>What's in the latest backup</h2>
{contents}
<h2>Recent runs — what changed</h2>
<div class="card"><table><tr><th>When</th><th>Status</th><th>Snapshot</th><th>Uploaded</th><th>Changes (new/modified files)</th></tr>
{run_rows or '<tr><td colspan="5">No runs yet</td></tr>'}</table></div>
<h2>Not backed up</h2>
<div class="card"><table>{not_backed}</table></div>
<h2>Per month</h2>
<div class="card"><table><tr><th>Month</th><th>Runs</th><th>Failed</th><th>Uploaded</th><th>Repo at end</th><th>Avg repo</th><th>Est. storage</th></tr>
{month_rows or '<tr><td colspan="7">No data yet</td></tr>'}</table></div>
<h2>Per day (last 30)</h2>
<div class="card"><table><tr><th>Day</th><th>OK/runs</th><th>New files</th><th>Changed</th><th>Uploaded</th><th></th><th>Repo size</th></tr>
{day_rows or '<tr><td colspan="7">No data yet</td></tr>'}</table></div>
<p class="note">"Uploaded" is new data after dedup and compression. Estimates use R2 standard storage
(${R2_STORAGE_PER_GB_MONTH}/GB-month, {R2_FREE_GB:.0f} GB free). Operation charges (Class A/B requests) are not
shown here — check the R2 metrics page in the Cloudflare dashboard; a nightly run is a few hundred to a few
thousand operations, far inside the free tier.</p>
</main></body></html>"""
    return Response(page, mimetype='text/html')
