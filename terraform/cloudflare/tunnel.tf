# ---------------------------------------------------------------------------
# Cloudflare Tunnel (cloudflared)
# ---------------------------------------------------------------------------
# The tunnel is outbound-only: the Pi connects to Cloudflare's network.
# No inbound ports are opened on the Pi or the router.

resource "cloudflare_zero_trust_tunnel_cloudflared" "pumphouse" {
  account_id = var.cloudflare_account_id
  name       = "pumphouse"
  secret     = var.tunnel_secret
}

# Ingress rules: which hostnames map to which local services.
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "pumphouse" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.pumphouse.id

  config {
    ingress_rule {
      hostname = var.domain
      service  = "https://localhost:${var.flask_port}"

      origin_request {
        # Pi's TLS cert is for tplinkdns.com, not this domain.
        # Cloudflare handles TLS for the public; we trust our own Pi locally.
        no_tls_verify = true
      }
    }

    ingress_rule {
      hostname = "www.${var.domain}"
      service  = "https://localhost:${var.flask_port}"

      origin_request {
        no_tls_verify = true
      }
    }

    # Catch-all: any request that doesn't match a hostname above returns 404.
    # Required by cloudflared — must be the last rule.
    ingress_rule {
      service = "http_status:404"
    }
  }
}

# ---------------------------------------------------------------------------
# DNS records
# ---------------------------------------------------------------------------
# Both apex and www point to the tunnel as a proxied CNAME.
# Cloudflare resolves these to its own Anycast IPs — the Pi's real IP is
# never exposed in DNS.

resource "cloudflare_record" "apex" {
  zone_id = var.cloudflare_zone_id
  name    = "@"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.pumphouse.id}.cfargotunnel.com"
  proxied = true
}

resource "cloudflare_record" "www" {
  zone_id = var.cloudflare_zone_id
  name    = "www"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.pumphouse.id}.cfargotunnel.com"
  proxied = true
}
