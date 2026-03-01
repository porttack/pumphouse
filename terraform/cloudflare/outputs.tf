output "tunnel_id" {
  description = "Cloudflare Tunnel ID. Used in the cloudflared install token and in the .cfargotunnel.com CNAME."
  value       = cloudflare_zero_trust_tunnel_cloudflared.pumphouse.id
}

output "tunnel_token" {
  description = <<-EOT
    One-time install token for the Pi.
    Run on the Pi:  sudo cloudflared service install $(terraform output -raw tunnel_token)
    After install, start the service:  sudo systemctl enable --now cloudflared
  EOT
  value     = cloudflare_zero_trust_tunnel_cloudflared.pumphouse.tunnel_token
  sensitive = true
}

output "kv_namespace_id" {
  description = "RATINGS KV namespace ID — add to ~/.config/pumphouse/secrets.conf as CLOUDFLARE_KV_NAMESPACE_ID."
  value       = cloudflare_workers_kv_namespace.ratings.id
}

output "worker_name" {
  description = "Deployed Worker script name."
  value       = cloudflare_worker_script.ratings.name
}
