#!/usr/bin/env python3
"""
Generate a ready-to-paste Scriptable widget from the checked-in template.

Reads PUMPHOUSE_HOST / PUMPHOUSE_PORT from ~/.config/pumphouse/secrets.conf and
substitutes them into scriptable-widget.js's `const BASE = "..."` line, so the
template can stay in git with a placeholder while the generated copy carries the
real hostname.

Usage:
    python3 pistat/gen_widget.py              # write scriptable-widget.generated.js
    python3 pistat/gen_widget.py out.js       # write to a specific path
    python3 pistat/gen_widget.py -            # print to stdout (pipe / copy)
"""
import pathlib
import re
import sys

SECRETS_FILE = pathlib.Path.home() / '.config' / 'pumphouse' / 'secrets.conf'
HERE         = pathlib.Path(__file__).resolve().parent
TEMPLATE     = HERE / 'scriptable-widget.js'
DEFAULT_OUT  = HERE / 'scriptable-widget.generated.js'

BASE_LINE_RE = re.compile(r'^const BASE = ".*";$', re.MULTILINE)


def load_secrets(path: pathlib.Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (same format/rules as monitor/config.py)."""
    if not path.exists():
        sys.exit(f"error: secrets file not found: {path}")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip()
    return values


def main() -> None:
    out_arg = sys.argv[1] if len(sys.argv) > 1 else None

    secrets = load_secrets(SECRETS_FILE)
    host = secrets.get('PUMPHOUSE_HOST', '')
    port = secrets.get('PUMPHOUSE_PORT', '6443')
    if not host or host == 'your-hostname.example.com':
        sys.exit("error: PUMPHOUSE_HOST is unset or still the placeholder in "
                 f"{SECRETS_FILE}")

    base = f'https://{host}:{port}'
    template = TEMPLATE.read_text()
    result, n = BASE_LINE_RE.subn(f'const BASE = "{base}";', template)
    if n != 1:
        sys.exit(f"error: expected exactly one `const BASE = \"...\";` line in "
                 f"{TEMPLATE}, found {n}")

    if out_arg == '-':
        sys.stdout.write(result)
        return

    out_path = pathlib.Path(out_arg) if out_arg else DEFAULT_OUT
    out_path.write_text(result)
    print(f"Wrote {out_path}  (BASE = {base})")
    print("Copy its contents into your Scriptable script on the phone.")


if __name__ == '__main__':
    main()
