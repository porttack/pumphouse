#!/usr/bin/env python3
"""
TrackHS Statements Scraper

Logs into meredith.trackhs.com and downloads monthly owner statement summaries
from the /owner/statements/view-data/ JSON endpoint.

Saves monthly Period statements to statements.csv (Annual statements excluded).

Also downloads each monthly statement PDF (/owner/statements/pdf/?id=N), extracts the
per-reservation lines with pdftotext, and saves them to statement_reservations.csv so
the dashboard can show actual payout per reservation. Only statements not already
parsed, plus the most recent RESCAN_LATEST, are (re)downloaded.

Usage:
    python scrape_statements.py [--debug] [--output FILENAME]
"""

import argparse
import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

DATA_DIR = Path.home() / '.local' / 'share' / 'pumphouse'
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRACKHS_BASE_URL = "https://meredith.trackhs.com"
OWNER_PORTAL_URL = f"{TRACKHS_BASE_URL}/owner/"
STATEMENTS_DATA_URL = f"{TRACKHS_BASE_URL}/owner/statements/view-data/"

SECRETS_PATH = Path.home() / ".config" / "pumphouse" / "secrets.conf"

FIELDNAMES = ['year', 'period', 'end_date', 'revenue', 'charges', 'paid', 'balance']

STATEMENT_PDF_URL = f"{TRACKHS_BASE_URL}/owner/statements/pdf/"
RES_FIELDNAMES = ['reservation_id', 'year', 'period', 'guest', 'check_in', 'checkout',
                  'nights', 'rental_income', 'linen', 'commission', 'other_charges', 'actual']
RESCAN_LATEST = 2   # re-download newest N statements each run (late adjustments)

_RES_HEADER = re.compile(r'^Reservation #(\d+):\s*(.*?)\s*\((\d\d/\d\d/\d{4}) - (\d\d/\d\d/\d{4})\)\s+(\d+) Nights')
_LINE_ITEM = re.compile(r'^\d\d/\d\d/\d{4}\s+(.+?)\s{2,}\$(-?[\d,]+\.\d\d)\s*$')
_SECTION_END = ('Unit Work Orders', 'Payments To Owner', 'Payments From Owner', 'Transaction Summary')


def load_secrets():
    if not SECRETS_PATH.exists():
        print(f"ERROR: Secrets file not found: {SECRETS_PATH}")
        sys.exit(1)
    secrets = {}
    with open(SECRETS_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                secrets[key.strip()] = value.strip()
    username = secrets.get('TRACKHS_USERNAME')
    password = secrets.get('TRACKHS_PASSWORD')
    if not username or not password:
        print(f"ERROR: TRACKHS_USERNAME and TRACKHS_PASSWORD must be set in {SECRETS_PATH}")
        sys.exit(1)
    return username, password


def create_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    return session


def login(session, username, password, debug=False):
    from bs4 import BeautifulSoup
    try:
        response = session.get(OWNER_PORTAL_URL)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        login_form = soup.find('form')
        if not login_form:
            print("ERROR: Could not find login form")
            return False

        form_action = login_form.get('action', '') or OWNER_PORTAL_URL
        if not form_action.startswith('http'):
            form_action = TRACKHS_BASE_URL + form_action if form_action.startswith('/') else OWNER_PORTAL_URL

        payload = {}
        for inp in login_form.find_all('input', type='hidden'):
            if inp.get('name'):
                payload[inp['name']] = inp.get('value', '')

        uf = (login_form.find('input', {'name': 'username'}) or
              login_form.find('input', {'type': 'email'}) or
              login_form.find('input', {'name': 'email'}))
        pf = login_form.find('input', {'type': 'password'})
        payload[uf['name'] if uf else 'username'] = username
        payload[pf['name'] if pf else 'password'] = password

        response = session.post(form_action, data=payload, allow_redirects=True)
        response.raise_for_status()

        if 'Log in to get started' in response.text or 'Login - Owner Connect' in response.text:
            print("ERROR: Login failed")
            return False

        if debug:
            print("✓ Login successful")
        return True

    except requests.RequestException as e:
        print(f"ERROR during login: {e}")
        return False


def fetch_statements(session, debug=False):
    """Fetch all period statements from the view-data JSON endpoint."""
    try:
        response = session.get(STATEMENTS_DATA_URL)
        response.raise_for_status()
        data = response.json()
        records = data.get('data', [])
        if debug:
            print(f"Fetched {len(records)} total statement records")

        rows = []
        for rec in records:
            if rec.get('isAnnual') or rec.get('type') == 'Annual':
                continue
            rows.append({
                'year':     str(rec['year']),
                'period':   str(rec['period']),
                'end_date': rec.get('endDate', ''),
                'revenue':  rec.get('revenue', '0'),
                'charges':  rec.get('charges', '0'),
                'paid':     rec.get('paid', '0'),
                'balance':  rec.get('balance', '0'),
            })

        rows.sort(key=lambda r: r['end_date'])
        if debug:
            print(f"Kept {len(rows)} monthly (Period) statements")
        return rows

    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"ERROR fetching statements: {e}")
        return None


def _us_date_to_iso(d):
    m, dd, y = d.split('/')
    return f'{y}-{m}-{dd}'


def parse_statement_text(text, year, period):
    """Parse `pdftotext -layout` output of one statement into per-reservation rows.

    Rental Income is positive; every other line under a reservation (Linen Program,
    Commission, Cleaning Fee - Owner, ...) is a charge. actual = income - charges.
    Page breaks can separate a reservation header from its lines; lines are simply
    attributed to the most recent header until a section-ending heading.
    """
    by_id = {}
    cur = None
    for line in text.splitlines():
        stripped = line.strip()
        m = _RES_HEADER.match(stripped)
        if m:
            rid, guest, cin, cout, nights = m.groups()
            cur = by_id.setdefault(rid, {
                'reservation_id': rid, 'year': str(year), 'period': str(period),
                'guest': guest, 'check_in': _us_date_to_iso(cin),
                'checkout': _us_date_to_iso(cout), 'nights': nights,
                'rental_income': 0.0, 'linen': 0.0, 'commission': 0.0, 'other_charges': 0.0,
            })
            continue
        if stripped.startswith(_SECTION_END):
            cur = None
            continue
        if cur is None:
            continue
        m = _LINE_ITEM.match(stripped)
        if not m:
            continue
        desc, amt = m.group(1), float(m.group(2).replace(',', ''))
        if desc == 'Rental Income':
            cur['rental_income'] += amt
        elif desc == 'Linen Program':
            cur['linen'] += amt
        elif desc == 'Commission':
            cur['commission'] += amt
        else:
            cur['other_charges'] += amt
    rows = []
    for r in by_id.values():
        r['actual'] = r['rental_income'] - r['linen'] - r['commission'] - r['other_charges']
        for k in ('rental_income', 'linen', 'commission', 'other_charges', 'actual'):
            r[k] = f'{r[k]:.2f}'
        rows.append(r)
    return rows


def fetch_statement_reservations(session, statement_id, year, period, debug=False):
    """Download one statement PDF and return its per-reservation rows (None on failure)."""
    try:
        resp = session.get(STATEMENT_PDF_URL, params={'id': statement_id})
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  WARN: PDF download failed for {year}-{period:02d}: {e}")
        return None
    # Downloaded content is untrusted: keep it in its own fresh dir, pass paths as args.
    with tempfile.TemporaryDirectory(prefix='statement_') as tmp:
        pdf = Path(tmp) / 'statement.pdf'
        pdf.write_bytes(resp.content)
        try:
            out = subprocess.run(['pdftotext', '-layout', str(pdf), '-'],
                                 capture_output=True, text=True, timeout=60, check=True)
        except (OSError, subprocess.SubprocessError) as e:
            print(f"  WARN: pdftotext failed for {year}-{period:02d}: {e}")
            return None
    rows = parse_statement_text(out.stdout, year, period)
    if debug:
        print(f"  {year}-{period:02d}: {len(rows)} reservations")
    return rows


def load_existing_reservation_rows(path):
    if not Path(path).exists():
        return []
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def sync_statement_reservations(session, records, output_file, debug=False):
    """Update statement_reservations.csv from the monthly statement PDFs."""
    existing = load_existing_reservation_rows(output_file)
    have = {(r['year'], r['period']) for r in existing}
    periods = sorted(((str(r['year']), str(r['period']), r['id']) for r in records
                      if not (r.get('isAnnual') or r.get('type') == 'Annual')),
                     key=lambda t: (int(t[0]), int(t[1])))
    latest = {(y, p) for y, p, _ in periods[-RESCAN_LATEST:]}
    todo = [(y, p, i) for y, p, i in periods if (y, p) not in have or (y, p) in latest]
    refreshed = {}
    for y, p, sid in todo:
        rows = fetch_statement_reservations(session, sid, int(y), int(p), debug=debug)
        if rows is not None:
            refreshed[(y, p)] = rows
    kept = [r for r in existing if (r['year'], r['period']) not in refreshed]
    out = kept + [r for rows in refreshed.values() for r in rows]
    out.sort(key=lambda r: (int(r['year']), int(r['period']), r['check_in'], r['reservation_id']))
    with open(output_file, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=RES_FIELDNAMES, extrasaction='ignore')
        w.writeheader()
        w.writerows(out)
    return len(out), len(refreshed)


def save_statements(rows, output_file, debug=False):
    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    if debug:
        print(f"✓ Saved {len(rows)} statements to {output_file}")


def main():
    parser = argparse.ArgumentParser(description='Download TrackHS owner statements')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--output', default=str(DATA_DIR / 'statements.csv'))
    parser.add_argument('--reservations-output', default=str(DATA_DIR / 'statement_reservations.csv'))
    args = parser.parse_args()

    print("TrackHS Statements Scraper")
    print("=" * 50)

    username, password = load_secrets()
    session = create_session()

    print("\n[1/4] Logging in...")
    if not login(session, username, password, debug=args.debug):
        print("✗ Login failed")
        return 1
    print("✓ Login successful")

    print("\n[2/4] Fetching statements...")
    rows = fetch_statements(session, debug=args.debug)
    if rows is None:
        print("✗ Failed to fetch statements")
        return 1
    print(f"✓ Found {len(rows)} monthly statements")

    print("\n[3/4] Saving...")
    save_statements(rows, args.output, debug=args.debug)
    print(f"✓ Saved to: {args.output}")

    # Non-fatal: the monthly summary above is already saved.
    print("\n[4/4] Parsing per-reservation detail from statement PDFs...")
    try:
        records = session.get(STATEMENTS_DATA_URL).json().get('data', [])
        total, fetched = sync_statement_reservations(
            session, records, args.reservations_output, debug=args.debug)
        print(f"✓ {total} reservation rows ({fetched} statements refreshed) -> {args.reservations_output}")
    except Exception as e:
        print(f"WARN: per-reservation detail failed: {e}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
