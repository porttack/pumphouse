# DDNS — pumphouse.onblackberryhill.com

A private hostname for direct access to the Pi over port 6443, bypassing the
Cloudflare Tunnel.  Useful when you need raw access (e.g., the Scriptable
widget, `onblackberryhill2.tplinkdns.com` replacement).

**Why not use the Tunnel for this?**  The Cloudflare Tunnel proxies only ports
80/443.  Direct access to Flask on port 6443 with your own TLS cert requires a
DNS-only A record pointing to your home IP, kept current by a DDNS client.

For **infrastructure-as-code** (Terraform) see [terraform/cloudflare/ddns.tf](../terraform/cloudflare/ddns.tf).

---

## Architecture

```
You (iPhone, laptop, etc.)
  │
  ▼ pumphouse.onblackberryhill.com:6443  (DNS-only A record → home IP)
Router NAT
  │  port 6443 forwarded
  ▼
Flask on Pi https://localhost:6443  (your own TLS cert)
```

The record is **DNS-only** (gray cloud in Cloudflare) — Cloudflare does not
proxy the traffic, so your home IP is visible in DNS.  The `tplinkdns.com`
address remains the LAN fallback.

---

## Step 1 — Create the DNS record (Terraform or dashboard)

**Via Terraform (recommended):**

```bash
cd ~/src/pumphouse/terraform/cloudflare
terraform apply -target=cloudflare_record.pumphouse_ddns
```

**Or manually in the Cloudflare dashboard:**

Your domain → DNS → Records → Add record:

| Field | Value |
|-------|-------|
| Type | A |
| Name | `pumphouse` |
| IPv4 address | your current public IP (placeholder — ddclient will update it) |
| Proxy status | **DNS only** (gray cloud) |
| TTL | 2 minutes |

---

## Step 2 — Create a Cloudflare API token

The existing token used for Terraform/KV may already have DNS Edit permission.
If you need a separate token scoped to just DDNS updates:

Cloudflare dashboard → My Profile → API Tokens → Create Token:

- Template: **Edit zone DNS**
- Zone: `onblackberryhill.com`

Save the token — you'll need it in Step 3.

---

## Step 3 — Install and configure ddclient on the Pi

```bash
sudo apt install ddclient
```

Edit `/etc/ddclient.conf` (replace `YOUR_API_TOKEN` with the token from Step 2):

```
daemon=300
syslog=yes
pid=/var/run/ddclient.pid
ssl=yes

protocol=cloudflare
use=web, web=checkip.amazonaws.com
login=token
password=YOUR_API_TOKEN
zone=onblackberryhill.com
pumphouse.onblackberryhill.com
```

Enable and start:

```bash
sudo systemctl enable ddclient
sudo systemctl restart ddclient
```

Verify it updated successfully:

```bash
sudo ddclient -daemon=0 -debug -verbose -noquiet
```

---

## Step 4 — Router port forward

Ensure TCP port 6443 is forwarded to the Pi's LAN IP (same forward used by
`tplinkdns.com:6443`).  If you've already hardened the firewall for the Tunnel
(Step 10 of `cloudflare.md`), add an exception for 6443 from the WAN:

```bash
sudo ufw allow 6443
```

---

## Verification

```bash
# From outside your LAN (e.g., phone on cellular):
curl -k https://pumphouse.onblackberryhill.com:6443/api/epaper.jpg?tenant=no
```

---

## ddclient troubleshooting

```bash
# Check current public IP ddclient sees
curl -s https://checkip.amazonaws.com

# Force an immediate update (ignoring cache)
sudo ddclient -daemon=0 -force

# View logs
journalctl -u ddclient -n 50
```

---

## Updating the Scriptable widget

Replace the `tplinkdns.com` hostname in `pistat/scriptable-widget.js`:

```js
const url = "https://pumphouse.onblackberryhill.com:6443/api/epaper.bmp?tenant=no&scale=4";
const dashboardUrl = "https://onblackberryhill.com";  // via Cloudflare Tunnel
```
