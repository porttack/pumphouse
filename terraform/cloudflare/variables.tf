variable "cloudflare_account_id" {
  description = "Cloudflare Account ID — visible in the dashboard URL and Workers & Pages overview."
  type        = string
}

variable "cloudflare_zone_id" {
  description = "Cloudflare Zone ID for the domain — visible in the Zone Overview right sidebar."
  type        = string
}

variable "tunnel_secret" {
  description = <<-EOT
    Base64-encoded 32-byte secret for the Cloudflare Tunnel.
    Generate a new one with:  openssl rand -base64 32
    Set via environment to avoid storing in .tfvars:
      export TF_VAR_tunnel_secret="$(openssl rand -base64 32)"
    When importing an existing tunnel, use its original secret (not recoverable
    from the dashboard — check the Pi's cloudflared credentials file at
    ~/.cloudflared/<TUNNEL_ID>.json, field "TunnelSecret").
  EOT
  type        = string
  sensitive   = true
}

variable "domain" {
  description = "Primary apex domain."
  type        = string
  default     = "onblackberryhill.com"
}

variable "flask_port" {
  description = "HTTPS port Flask listens on locally on the Pi."
  type        = number
  default     = 6443
}
