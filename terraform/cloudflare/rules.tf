# ---------------------------------------------------------------------------
# Redirect Rules
# ---------------------------------------------------------------------------

resource "cloudflare_ruleset" "redirects" {
  zone_id     = var.cloudflare_zone_id
  name        = "Pumphouse redirects"
  description = "Root to /timelapse; www to apex"
  kind        = "zone"
  phase       = "http_request_dynamic_redirect"

  # Rule 1: / → /timelapse (on both apex and www)
  # Belt-and-suspenders: the Worker also redirects / → /timelapse, but having
  # it here keeps the redirect at the CDN layer even without the Worker.
  rules {
    description = "Root to timelapse"
    expression  = <<-EOT
      (http.host eq "${var.domain}" or http.host eq "www.${var.domain}")
      and http.request.uri.path eq "/"
    EOT
    action      = "redirect"
    enabled     = true

    action_parameters {
      from_value {
        status_code = 301

        target_url {
          value = "https://${var.domain}/timelapse"
        }

        preserve_query_string = false
      }
    }
  }

  # Rule 2: www.domain/path → domain/path  (preserves path, strips www)
  rules {
    description = "www to apex"
    expression  = "http.host eq \"www.${var.domain}\" and http.request.uri.path ne \"/\""
    action      = "redirect"
    enabled     = true

    action_parameters {
      from_value {
        status_code = 301

        target_url {
          # Dynamic expression: preserve path, switch to apex domain.
          expression = "concat(\"https://${var.domain}\", http.request.uri.path)"
        }

        preserve_query_string = false
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Cache Rules
# ---------------------------------------------------------------------------
# Cloudflare does not cache text/html by default ("Cache Everything" overrides
# this).  Binary assets (MP4, JPEG) are cached automatically by file extension
# and do not need a Cache Rule — they just need the right Cache-Control header
# from the Pi, which the Flask code provides (max-age=31536000 for past files).

resource "cloudflare_ruleset" "cache" {
  zone_id     = var.cloudflare_zone_id
  name        = "Pumphouse cache rules"
  description = "Timelapse HTML caching and query-string DoS mitigation"
  kind        = "zone"
  phase       = "http_cache_settings"

  # Rule 1: Cache timelapse viewer HTML pages.
  # Path /timelapse/20* matches all date-keyed pages (2020s and beyond).
  # Edge TTL is left to the origin's Cache-Control so the Pi's tiered policy
  # is honoured:
  #   today + yesterday → max-age=300  (5 min)
  #   2+ days old       → max-age=3600 (1 hr)
  # MP4 and JPEG assets on these pages are already cached by extension.
  rules {
    description = "Cache timelapse HTML pages"
    expression  = "starts_with(http.request.uri.path, \"/timelapse/20\")"
    action      = "set_cache_settings"
    enabled     = true

    action_parameters {
      cache = true

      edge_ttl {
        # "respect_origin_pull" means: use the Cache-Control max-age the Pi
        # sent.  Do NOT set a hard override here — the Pi's tiered values are
        # intentional (see docs/timelapse.md → Caching).
        mode = "respect_origin_pull"
      }

      browser_ttl {
        mode = "respect_origin"
      }
    }
  }

  # Rule 2: Ignore query strings on all /timelapse/* routes (DoS mitigation).
  #
  # Without this rule, an attacker can bypass Cloudflare's cache by appending
  # random query params: /timelapse/2026-01-01_1750.mp4?x=1, ?x=2, ... Each
  # unique URL is a cache miss, forcing the Pi to stream a ~4 MB MP4 on every
  # request.  Setting "exclude all" strips the query string from the cache key
  # so all variants resolve to the same cache entry.
  #
  # This is safe because no timelapse page or MP4 uses query params for content
  # differentiation.  (/timelapse?today is a redirect handled before caching.)
  rules {
    description = "Ignore query string (DoS mitigation)"
    expression  = "starts_with(http.request.uri.path, \"/timelapse/\")"
    action      = "set_cache_settings"
    enabled     = true

    action_parameters {
      cache = true

      cache_key {
        custom_key {
          query_string {
            exclude {
              all = true
            }
          }
        }
      }
    }
  }
}
