# Infrastructure

Configuration and infrastructure-as-code for the Pumphouse system.

| Directory | Contents |
|-----------|----------|
| [`cloudflare/`](cloudflare/) | Terraform — Cloudflare Tunnel, Worker, DNS, cache/redirect rules |
| [`services/`](services/) | systemd service unit files for the three daemons |
| [`cron/`](cron/) | Crontab reference and install instructions |

---

## cloudflare/

Manages the public-facing Cloudflare infrastructure as Terraform. Run once
to provision; re-run after any change to `*.tf` files.

See [`cloudflare/README.md`](cloudflare/README.md) for prerequisites,
fresh-deployment steps, and day-to-day operations.

---

## services/

The three systemd units that keep the Pi daemons running:

| File | Service | What it runs |
|------|---------|-------------|
| `pumphouse-monitor.service` | `pumphouse-monitor` | `python -m monitor` — sensor polling loop |
| `pumphouse-web.service` | `pumphouse-web` | `python -m monitor.web` — Flask dashboard |
| `pumphouse-timelapse.service` | `pumphouse-timelapse` | `sunset_timelapse.py` — camera capture daemon |

Install with:

```bash
bin/install-services.sh
sudo systemctl enable --now pumphouse-monitor pumphouse-web pumphouse-timelapse
```

---

## cron/

Scheduled jobs that complement the three daemons (reservation scraping,
system health logging, GPH calculation). Not managed by Terraform — installed
once via `crontab -e`.

See [`cron/README.md`](cron/README.md) for the job table and install steps.
