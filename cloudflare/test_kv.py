#!/usr/bin/env python3
"""
Quick test: verify Cloudflare KV connectivity using secrets.conf credentials.
Writes a test key, reads it back, then deletes it.
"""
import json
import urllib.request
import urllib.error
from pathlib import Path


def load_secrets():
    secrets = Path.home() / '.config' / 'pumphouse' / 'secrets.conf'
    cfg = {}
    with open(secrets) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            cfg[k.strip()] = v.strip()
    return cfg


def kv_request(method, key, cfg, data=None):
    account_id   = cfg['CLOUDFLARE_ACCOUNT_ID']
    namespace_id = cfg['CLOUDFLARE_KV_NAMESPACE_ID']
    api_token    = cfg['CLOUDFLARE_KV_API_TOKEN']
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
           f"/storage/kv/namespaces/{namespace_id}/values/{key}")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={'Authorization': f'Bearer {api_token}'},
    )
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main():
    cfg = load_secrets()
    print(f"Account ID:    {cfg.get('CLOUDFLARE_ACCOUNT_ID', '(missing)')}")
    print(f"Namespace ID:  {cfg.get('CLOUDFLARE_KV_NAMESPACE_ID', '(missing)')}")
    print(f"API Token:     {'(set)' if cfg.get('CLOUDFLARE_KV_API_TOKEN') else '(missing)'}")
    print()

    test_key   = '_test_connection'
    test_value = json.dumps({'ok': True})

    print(f"PUT {test_key} ...")
    status, body = kv_request('PUT', test_key, cfg, data=test_value.encode())
    print(f"  {status}: {body[:120]}")
    if status not in (200, 201):
        print("FAIL — cannot write to KV. Check account ID, namespace ID, and token permissions.")
        return

    print(f"GET {test_key} ...")
    status, body = kv_request('GET', test_key, cfg)
    print(f"  {status}: {body[:120]}")
    if status == 200 and body == test_value:
        print("  Read-back matches. ✓")
    else:
        print("  WARN — read-back mismatch or error.")

    print(f"DELETE {test_key} ...")
    status, body = kv_request('DELETE', test_key, cfg)
    print(f"  {status}: {body[:120]}")

    print()
    print("KV connectivity OK." if status in (200, 201) else "DELETE failed — check permissions.")


if __name__ == '__main__':
    main()
