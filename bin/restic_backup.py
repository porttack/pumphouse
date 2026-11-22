#!/home/pi/src/pumphouse/venv/bin/python3
"""Nightly encrypted backup of the Pi to Cloudflare R2 via restic.

Credentials come from ~/.config/restic/env (AWS_* + RESTIC_*).
Each run appends one row to ~/.local/share/pumphouse/backup_log.csv, which the
/backup page reads to show daily/monthly usage for correlating with R2 billing.

Usage: restic_backup.py [--prune]   (--prune also runs `restic prune`; cron does this weekly)
"""
import csv
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HOME = Path('/home/pi')
ENV_FILE = HOME / '.config/restic/env'
STAGING = HOME / '.backup-staging'
LOG_CSV = HOME / '.local/share/pumphouse/backup_log.csv'
FIELDS = ['ts', 'status', 'duration_s', 'snapshot', 'files_new', 'files_changed',
          'bytes_processed', 'bytes_added', 'repo_bytes', 'repo_objects', 'snapshots', 'error']

PATHS = [
    str(HOME / '.local/share/pumphouse'),
    str(HOME / '.config'),
    str(HOME / '.claude'),
    str(HOME / '.ssh'),
    str(HOME / 'src'),
    str(HOME / 'wyze'),
    str(HOME / 'timelapses'),
    str(HOME / 'install'),
    str(STAGING),
    # system-level: config and anything hand-installed outside the home dir
    '/etc', '/usr/local', '/opt', '/root', '/srv', '/var/spool/cron', '/var/backups',
    '/boot/firmware',
]
DETAIL_JSONL = HOME / '.local/share/pumphouse/backup_detail.jsonl'

# (path prefix, label) -- longest prefix wins. Shown on the /backup page.
GROUPS = [
    ('/home/pi/.local/share/pumphouse', 'Pumphouse data (CSVs, state, audio clips)'),
    ('/home/pi/src', 'Source code & project files (~/src)'),
    ('/home/pi/timelapses', 'Timelapse videos'),
    ('/home/pi/.config', 'App config & secrets (~/.config)'),
    ('/home/pi/.claude', 'Claude settings & memory'),
    ('/home/pi/.ssh', 'SSH keys'),
    ('/home/pi/wyze', 'Wyze project'),
    ('/home/pi/install', 'Install files'),
    ('/home/pi/.backup-staging', 'System-state bundle (cron, apt/pip lists, systemd, certs)'),
    ('/etc', 'System config (/etc)'),
    ('/usr/local', 'Hand-installed programs (/usr/local)'),
    ('/opt', '/opt'),
    ('/root', 'Root home (/root)'),
    ('/srv', '/srv'),
    ('/var/spool/cron', 'Cron spool'),
    ('/var/backups', 'System package backups (/var/backups)'),
    ('/boot/firmware', 'Boot config (/boot/firmware)'),
]
EXCLUDES = ['venv', '__pycache__', '.cache', 'node_modules', '*.pyc', '.git/objects/pack/tmp_*']
KEEP = ['--keep-daily', '14', '--keep-weekly', '8', '--keep-monthly', '12']


def load_env():
    env = dict(os.environ)
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith('export ') and '=' in line:
            k, v = line[7:].split('=', 1)
            env[k] = shlex.split(v)[0] if v else ''
    return env


def sh(cmd, check=True, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), check=check,
                          capture_output=True, text=True, **kw)


def stage_system_state():
    """Capture root-owned / system config that lives outside the home dir."""
    STAGING.mkdir(mode=0o700, exist_ok=True)
    s = str(STAGING)
    sh(f'dpkg --get-selections > {s}/apt-packages.txt')
    sh(f'crontab -l > {s}/crontab-pi.txt', check=False)
    sh(f'sudo -n crontab -l > {s}/crontab-root.txt', check=False)
    sh(f'{HOME}/src/pumphouse/venv/bin/pip list --format=freeze > {s}/pip-pumphouse.txt', check=False)
    sh(f'cp /etc/systemd/system/pumphouse-*.service /etc/systemd/system/cloudflared.service {s}/ 2>/dev/null; true')
    sh(f'cp /etc/ssh/sshd_config /etc/fstab /boot/firmware/config.txt /boot/firmware/cmdline.txt {s}/ 2>/dev/null; true')
    sh(f'sudo -n tar czf {s}/etc-root-state.tgz -C / etc/letsencrypt etc/cloudflared '
       f'etc/ssh/ssh_host_* 2>/dev/null; sudo -n chown pi:pi {s}/etc-root-state.tgz; chmod 600 {s}/*', check=False)


def _group(path):
    best = None
    for pre, label in GROUPS:
        if path == pre or path.startswith(pre + '/'):
            if best is None or len(pre) > len(best[0]):
                best = (pre, label)
    return best


def snapshot_detail(env, snap_id, prev_id=None):
    """Summarise what is in a snapshot (per group) and what changed vs the previous one."""
    sizes, groups = {}, {}
    ls = sh(['restic', 'ls', '--json', snap_id], env=env).stdout
    for line in ls.splitlines():
        try:
            n = json.loads(line)
        except ValueError:
            continue
        if n.get('type') != 'file':
            continue
        path, size = n['path'], n.get('size', 0)
        sizes[path] = size
        g = _group(path)
        if not g:
            continue
        pre, label = g
        gd = groups.setdefault(label, {'files': 0, 'bytes': 0, 'added': 0, 'modified': 0,
                                       'removed': 0, 'changed_bytes': 0, 'children': {}})
        gd['files'] += 1
        gd['bytes'] += size
        rest = path[len(pre):].lstrip('/')
        child = rest.split('/')[0] if '/' in rest else '(files in top folder)'
        gd['children'][child] = gd['children'].get(child, 0) + size
    top = []
    if prev_id:
        out = sh(['restic', 'diff', '--json', prev_id, snap_id], env=env).stdout
        for line in out.splitlines():
            try:
                c = json.loads(line)
            except ValueError:
                continue
            if c.get('message_type') != 'change' or c['path'].endswith('/'):
                continue
            g = _group(c['path'])
            if not g:
                continue
            gd = groups.setdefault(g[1], {'files': 0, 'bytes': 0, 'added': 0, 'modified': 0,
                                          'removed': 0, 'changed_bytes': 0, 'children': {}})
            m = c['modifier']
            if m == '+':
                gd['added'] += 1
            elif m == '-':
                gd['removed'] += 1
            elif 'M' in m:
                gd['modified'] += 1
            if m != '-' and c['path'] in sizes:
                gd['changed_bytes'] += sizes[c['path']]
                top.append((sizes[c['path']], c['path'], m))
    for gd in groups.values():  # keep the 8 biggest children per group
        gd['children'] = dict(sorted(gd['children'].items(), key=lambda kv: -kv[1])[:8])
    top.sort(reverse=True)
    return {'snapshot': snap_id[:8], 'prev': (prev_id or '')[:8], 'groups': groups,
            'top_changed': [{'path': p, 'bytes': b, 'mod': m} for b, p, m in top[:12]]}


def record_detail(env, snap_id, prev_id=None):
    d = snapshot_detail(env, snap_id, prev_id)
    d['ts'] = datetime.now().isoformat(timespec='seconds')
    DETAIL_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(DETAIL_JSONL, 'a') as f:
        f.write(json.dumps(d) + '\n')


def backfill_detail(env):
    snaps = sorted(json.loads(sh(['restic', 'snapshots', '--json'], env=env).stdout),
                   key=lambda x: x['time'])
    have = set()
    if DETAIL_JSONL.exists():
        have = {json.loads(l)['snapshot'] for l in DETAIL_JSONL.read_text().splitlines() if l}
    for i, sn in enumerate(snaps):
        if sn['id'][:8] not in have:
            record_detail(env, sn['id'], snaps[i - 1]['id'] if i else None)
            print('detail recorded for', sn['id'][:8])


def notify_failure(msg):
    try:
        sys.path.insert(0, str(HOME / 'src/pumphouse'))
        from monitor.ntfy import send_notification
        send_notification('Pi backup FAILED', msg[:500], priority='high', tags=['warning'])
    except Exception as e:  # never let alerting mask the real error
        print(f'ntfy failed: {e}', file=sys.stderr)


def append_row(row):
    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG_CSV.exists()
    with open(LOG_CSV, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, '') for k in FIELDS})


def main():
    if '--backfill-detail' in sys.argv:
        return backfill_detail(load_env()) or 0
    t0 = time.time()
    row = {'ts': datetime.now().isoformat(timespec='seconds')}
    env = load_env()
    try:
        stage_system_state()
        # run as root (sudo -n, passwordless on this Pi) so /etc, /root etc. are readable
        keep = ','.join(k for k in env if k.startswith(('AWS_', 'RESTIC_')))
        cmd = ['sudo', '-n', f'--preserve-env={keep}', 'nice', '-n', '15', 'ionice', '-c3', 'restic', 'backup', '--json']
        for e in EXCLUDES:
            cmd += ['--exclude', e]
        cmd += PATHS
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        summary = None
        for line in r.stdout.splitlines():
            try:
                j = json.loads(line)
            except ValueError:
                continue
            if j.get('message_type') == 'summary':
                summary = j
        # restic exits 3 when some files vanished/unreadable but the snapshot still saved
        if r.returncode not in (0, 3) or not summary:
            raise RuntimeError(f'restic backup rc={r.returncode}: {r.stderr.strip()[-400:]}')
        row.update(snapshot=summary['snapshot_id'][:8], files_new=summary['files_new'],
                   files_changed=summary['files_changed'],
                   bytes_processed=summary['total_bytes_processed'],
                   bytes_added=summary['data_added_packed'] if 'data_added_packed' in summary else summary['data_added'])
        if r.returncode == 3:
            row['error'] = 'some files unreadable (rc=3)'

        sh(['restic', 'forget'] + KEEP + (['--prune'] if '--prune' in sys.argv else []), env=env)
        st = json.loads(sh(['restic', 'stats', '--mode', 'raw-data', '--json'], env=env).stdout)
        row.update(repo_bytes=st['total_size'], repo_objects=st['total_blob_count'])
        snaps = json.loads(sh(['restic', 'snapshots', '--json'], env=env).stdout)
        row['snapshots'] = len(snaps)
        row['status'] = 'ok'
        try:  # what's in it / what changed -- never fail the backup over this
            snaps.sort(key=lambda x: x['time'])
            ids = [x['id'] for x in snaps]
            full = next((i for i in ids if i.startswith(row['snapshot'])), ids[-1])
            idx = ids.index(full)
            record_detail(env, full, ids[idx - 1] if idx else None)
        except Exception as e:
            print(f'detail failed: {e}', file=sys.stderr)
    except Exception as e:
        row['status'] = 'FAILED'
        row['error'] = str(e).replace('\n', ' ')[:400]
        notify_failure(row['error'])
    row['duration_s'] = round(time.time() - t0)
    append_row(row)
    print(row)
    return 0 if row['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
