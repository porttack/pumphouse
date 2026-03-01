# Cloudflare Infrastructure — Terraform

Manages the Cloudflare side of `onblackberryhill.com` as code.

For the **manual** step-by-step setup (dashboard clicks, cloudflared install on the Pi,
UFW firewall) see [docs/cloudflare.md](../../docs/cloudflare.md).

---

## What this manages

| Resource | Terraform file |
|----------|---------------|
| Cloudflare Tunnel (`pumphouse`) + ingress rules | `tunnel.tf` |
| Apex and www CNAME records → tunnel | `tunnel.tf` |
| `RATINGS` KV namespace | `worker.tf` |
| `pumphouse-ratings` Worker script (reads `cloudflare/ratings-worker.js`) | `worker.tf` |
| Worker routes (`onblackberryhill.com/*`, `www.*`) | `worker.tf` |
| Redirect rules (/ → /timelapse, www → apex) | `rules.tf` |
| Cache rule — timelapse HTML pages | `rules.tf` |
| Cache rule — ignore query string (DoS mitigation) | `rules.tf` |

## What this does NOT manage

| Thing | Where it lives | Why not Terraform |
|-------|---------------|-------------------|
| `cloudflared` service on the Pi | Pi systemd | OS-level; installed once with the tunnel token from `outputs.tf` |
| Flask / pumphouse app code | `monitor/` | Application, not infra |
| `pistat/scriptable-widget.js` | `pistat/` | iPhone client script, manually installed; not deployed to Cloudflare |
| UFW firewall rules on the Pi | Pi OS | In-person hardening; see Step 10 in `docs/cloudflare.md` |
| KV data (ratings) | Cloudflare KV | Application data, not infrastructure |

---

## Prerequisites

1. **Terraform ≥ 1.5** — [install](https://developer.hashicorp.com/terraform/install)
   ```bash
   terraform version
   ```

2. **Cloudflare API token** — create at:
   `dash.cloudflare.com → My Profile → API Tokens → Create Token → Custom`

   Required permissions:

   | Scope | Resource | Permission |
   |-------|----------|------------|
   | Account | Cloudflare Tunnel | Edit |
   | Account | Workers KV Storage | Edit |
   | Account | Workers Scripts | Edit |
   | Zone — onblackberryhill.com | Cache Rules | Edit |
   | Zone — onblackberryhill.com | Dynamic Redirect | Edit |
   | Zone — onblackberryhill.com | DNS | Edit |

   Export it:
   ```bash
   export CLOUDFLARE_API_TOKEN="your-token-here"
   ```

3. **Account ID and Zone ID** — both visible in the Cloudflare dashboard right
   sidebar when you select your domain.

---

## Fresh deployment (from scratch)

```bash
cd terraform/cloudflare

# 1. Supply secrets
export CLOUDFLARE_API_TOKEN="your-api-token"
export TF_VAR_tunnel_secret="$(openssl rand -base64 32)"

# 2. Create terraform.tfvars with your IDs (never commit this file)
cp terraform.tfvars.example terraform.tfvars
#    edit terraform.tfvars — fill in cloudflare_account_id and cloudflare_zone_id

# 3. Initialize (downloads provider)
terraform init

# 4. Preview
terraform plan

# 5. Deploy
terraform apply
```

After `terraform apply` completes:

```bash
# 6. Install the tunnel service on the Pi
#    (run this ON the Pi, not your laptop)
sudo cloudflared service install $(terraform output -raw tunnel_token)
sudo systemctl enable --now cloudflared
systemctl status cloudflared   # should show "active (running)"
```

```bash
# 7. Add KV credentials to the Pi's secrets file
terraform output kv_namespace_id
# → add to ~/.config/pumphouse/secrets.conf:
#     CLOUDFLARE_KV_NAMESPACE_ID=<value>
#     CLOUDFLARE_KV_API_TOKEN=<your kv api token>
#     RATINGS_BACKEND=cloudflare_kv
```

---

## Importing existing resources

The infrastructure was originally created manually. To bring it under Terraform
without destroying and recreating everything:

```bash
# Find your IDs first:
#   Tunnel ID:       dash.cloudflare.com → Zero Trust → Networks → Tunnels
#   KV Namespace ID: dash.cloudflare.com → Workers & Pages → KV
#   Record IDs:      curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
#                      "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" | jq '.result[]|{name,id}'
#   Route IDs:       curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
#                      "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/workers/routes" | jq .

terraform import cloudflare_zero_trust_tunnel_cloudflared.pumphouse  "$ACCOUNT_ID/$TUNNEL_ID"
terraform import cloudflare_workers_kv_namespace.ratings             "$ACCOUNT_ID/$KV_NAMESPACE_ID"
terraform import cloudflare_worker_script.ratings                    "$ACCOUNT_ID/pumphouse-ratings"
terraform import cloudflare_worker_route.main                        "$ZONE_ID/$ROUTE_ID_APEX"
terraform import cloudflare_worker_route.www                         "$ZONE_ID/$ROUTE_ID_WWW"
terraform import cloudflare_record.apex                              "$ZONE_ID/$RECORD_ID_APEX"
terraform import cloudflare_record.www                               "$ZONE_ID/$RECORD_ID_WWW"
terraform import cloudflare_ruleset.redirects                        "zone/$ZONE_ID/http_request_dynamic_redirect"
terraform import cloudflare_ruleset.cache                            "zone/$ZONE_ID/http_cache_settings"

# After importing, run plan to confirm no unintended changes before applying:
terraform plan
```

> **Tunnel secret when importing:** The secret stored in Cloudflare is not
> recoverable from the dashboard. Use the original value from the Pi:
> ```bash
> cat ~/.cloudflared/<TUNNEL_ID>.json | python3 -c \
>   "import sys,json; print(json.load(sys.stdin)['TunnelSecret'])"
> ```
> Set it as `TF_VAR_tunnel_secret` before importing.

---

## Day-to-day operations

### Redeploy the Worker after editing `ratings-worker.js`

```bash
export CLOUDFLARE_API_TOKEN="your-token"
terraform apply -target=cloudflare_worker_script.ratings
```

Or paste the updated file manually via the dashboard (Workers → Edit code → Deploy).

### Add or change a cache/redirect rule

Edit `rules.tf`, then:
```bash
terraform apply -target=cloudflare_ruleset.cache
# or
terraform apply -target=cloudflare_ruleset.redirects
```

### Rotate the tunnel secret

```bash
export TF_VAR_tunnel_secret="$(openssl rand -base64 32)"
terraform apply -target=cloudflare_zero_trust_tunnel_cloudflared.pumphouse
# Then restart cloudflared on the Pi to pick up the new credentials:
sudo systemctl restart cloudflared
```

---

## State file

Terraform stores resource state in `terraform.tfstate` (and `.backup`). This file:
- Is **gitignored** — never commit it
- Contains the tunnel token (sensitive) and all resource IDs
- Should be kept safe; if lost, use `terraform import` to rebuild it

For a production deployment consider a [remote backend](https://developer.hashicorp.com/terraform/language/backend)
(Terraform Cloud, S3, etc.) so state is stored outside the Pi and survives hardware failure.
For this home project, keeping it locally on the Pi is fine.
