#!/usr/bin/env bash
# Deploy the pumphouse-ratings Cloudflare Worker.
# Reads credentials from ~/.config/pumphouse/secrets.conf so nothing
# sensitive is hardcoded in wrangler.toml or committed to git.
set -euo pipefail

SECRETS="${HOME}/.config/pumphouse/secrets.conf"
if [ ! -f "$SECRETS" ]; then
  echo "Error: $SECRETS not found" >&2
  exit 1
fi

get_secret() {
  grep "^${1}=" "$SECRETS" | cut -d= -f2- | head -1
}

KV_NAMESPACE_ID=$(get_secret CLOUDFLARE_KV_NAMESPACE_ID)
export CLOUDFLARE_API_TOKEN=$(get_secret CLOUDFLARE_KV_API_TOKEN)

if [ -z "$KV_NAMESPACE_ID" ] || [ -z "$CLOUDFLARE_API_TOKEN" ]; then
  echo "Error: CLOUDFLARE_KV_NAMESPACE_ID or CLOUDFLARE_KV_API_TOKEN missing from $SECRETS" >&2
  exit 1
fi

cd "$(dirname "$0")"

# Build a temp wrangler.toml with the real namespace ID substituted in
TMP_TOML=$(mktemp /tmp/wrangler.XXXXXX.toml)
trap 'rm -f "$TMP_TOML"' EXIT
sed "s/REPLACE_WITH_KV_NAMESPACE_ID/${KV_NAMESPACE_ID}/" wrangler.toml > "$TMP_TOML"

echo "Deploying pumphouse-ratings worker..."
wrangler deploy --config "$TMP_TOML"
echo "Done."
