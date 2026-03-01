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
