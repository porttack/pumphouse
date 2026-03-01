# ---------------------------------------------------------------------------
# KV Namespace
# ---------------------------------------------------------------------------
# Stores per-day rating aggregates: key = "YYYY-MM-DD", value = {"count":N,"sum":N}
# The Pi also maintains a local ratings.json mirror for direct-access reads.

resource "cloudflare_workers_kv_namespace" "ratings" {
  account_id = var.cloudflare_account_id
  title      = "RATINGS"
}

# ---------------------------------------------------------------------------
# Worker Script
# ---------------------------------------------------------------------------
# Reads the JS source directly from the repo so Terraform and the source file
# stay in sync.  Re-run `terraform apply` after editing ratings-worker.js to
# redeploy.
#
# The Worker intercepts these routes before they reach the Pi tunnel:
#   GET  /snapshot, /frame          — 5-min CDN cache; crop=0 stripped
#   GET  /api/ratings/YYYY-MM-DD    — reads from KV
#   POST /timelapse/YYYY-MM-DD/rate — writes to KV
#   All others                      — passed through to Pi

resource "cloudflare_worker_script" "ratings" {
  account_id = var.cloudflare_account_id
  name       = "pumphouse-ratings"

  # The script uses ES module syntax (export default {}), so module = true.
  content = file("${path.module}/../../cloudflare/ratings-worker.js")
  module  = true

  kv_namespace_binding {
    name         = "RATINGS"
    namespace_id = cloudflare_workers_kv_namespace.ratings.id
  }
}

# ---------------------------------------------------------------------------
# Worker Routes
# ---------------------------------------------------------------------------
# Route all traffic for both hostnames through the Worker.
# The Worker decides per-path whether to handle it or pass through to tunnel.

resource "cloudflare_worker_route" "main" {
  zone_id     = var.cloudflare_zone_id
  pattern     = "${var.domain}/*"
  script_name = cloudflare_worker_script.ratings.name
}

resource "cloudflare_worker_route" "www" {
  zone_id     = var.cloudflare_zone_id
  pattern     = "www.${var.domain}/*"
  script_name = cloudflare_worker_script.ratings.name
}
