# ---------------------------------------------------------------------------
# DDNS record — pumphouse.onblackberryhill.com
# ---------------------------------------------------------------------------
# A DNS-only A record for direct Pi access on port 6443.
# Cloudflare does NOT proxy this record — the Pi's real IP is exposed in DNS,
# which is required for non-standard ports and direct TLS.
#
# The actual IP value is managed by ddclient on the Pi (see docs/ddns.md).
# Terraform creates/owns the record; ddclient updates its content via the API.
#
# After applying, configure ddclient on the Pi:
#   sudo apt install ddclient
#   # See docs/ddns.md for /etc/ddclient.conf

variable "ddns_subdomain" {
  description = "Subdomain for the DDNS A record (direct Pi access, DNS-only)."
  type        = string
  default     = "pumphouse"
}

variable "ddns_initial_ip" {
  description = <<-EOT
    Placeholder public IP written by Terraform on first apply.
    ddclient will overwrite this with your real IP within 5 minutes.
    Find your current public IP with: curl -s https://checkip.amazonaws.com
  EOT
  type    = string
  default = "1.2.3.4"
}

resource "cloudflare_record" "pumphouse_ddns" {
  zone_id = var.cloudflare_zone_id
  name    = var.ddns_subdomain
  type    = "A"
  content = var.ddns_initial_ip
  proxied = false   # DNS-only — required for port 6443 direct access
  ttl     = 120     # 2 minutes — fast propagation when IP changes
}
