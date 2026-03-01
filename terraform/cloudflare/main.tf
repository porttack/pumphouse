terraform {
  required_version = ">= 1.5"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

# Authenticate via environment variable — never hardcode in source.
#
#   export CLOUDFLARE_API_TOKEN="your-token"
#
# The token needs the following permissions (create at dash.cloudflare.com →
# My Profile → API Tokens → Create Token → Custom):
#
#   Account scope:
#     Cloudflare Tunnel : Edit
#     Workers KV Storage: Edit
#     Workers Scripts   : Edit
#   Zone scope (onblackberryhill.com):
#     Cache Rules       : Edit
#     Dynamic Redirect  : Edit
#     DNS               : Edit
#     Page Rules        : Edit
#
provider "cloudflare" {}

# ---------------------------------------------------------------------------
# Importing existing resources
# ---------------------------------------------------------------------------
# This configuration was originally set up manually (see docs/cloudflare.md).
# To bring existing resources under Terraform management without recreating:
#
#   terraform import cloudflare_zero_trust_tunnel_cloudflared.pumphouse  <ACCOUNT_ID>/<TUNNEL_ID>
#   terraform import cloudflare_workers_kv_namespace.ratings             <ACCOUNT_ID>/<NAMESPACE_ID>
#   terraform import cloudflare_worker_script.ratings                    <ACCOUNT_ID>/pumphouse-ratings
#   terraform import cloudflare_worker_route.main                        <ZONE_ID>/<ROUTE_ID>
#   terraform import cloudflare_record.apex                              <ZONE_ID>/<RECORD_ID>
#   terraform import cloudflare_record.www                               <ZONE_ID>/<RECORD_ID>
#   terraform import cloudflare_ruleset.redirects                        zone/<ZONE_ID>/http_request_dynamic_redirect
#   terraform import cloudflare_ruleset.cache                            zone/<ZONE_ID>/http_cache_settings
#
# Resource IDs are visible in the Cloudflare dashboard URLs or via:
#   curl -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
#     https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/cfd_tunnel
