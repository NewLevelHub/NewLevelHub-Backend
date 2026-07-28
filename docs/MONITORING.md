# Monitoring — NewLevelHub

Static Prometheus + Alertmanager stack for **compose service state**, **named containers**, and **host health**. Alerts go to Slack and/or Telegram.

App CD (`docker-compose.prod.yml`) does **not** redeploy this stack. Monitoring runs as a separate Compose project (`nlh-monitoring`).

## Architecture

| Component | Role |
|-----------|------|
| `compose_exporter` | Reads Docker socket → `compose_service_up`, `container_up` |
| `node_exporter` | Host CPU / RAM / disk |
| `prometheus` | Scrapes + evaluates alert rules |
| `alertmanager` | Routes alerts to Slack / Telegram |

**Local:** [`docker-compose.monitoring.yml`](../docker-compose.monitoring.yml) — ports on all interfaces (`:9090`, …).  
**Production:** [`docker-compose.monitoring.prod.yml`](../docker-compose.monitoring.prod.yml) — binds **`127.0.0.1` only**.

## What is watched

### Local (`docker-compose.local.yml`)

Compose project `newlevelhub-backend`:

`backend`, `db`, `redis`, `celery_worker`, `celery_beat`, `minio`, `dozzle`

### Production (`docker-compose.prod.yml` + frontend)

Compose project `newlevelhub-backend`:

`backend`, `db`, `redis`, `celery`, `celery_beat`, `nginx`, `dozzle`

Fixed container name (frontend is another Compose project):

`nlh_frontend_prod` → metric `container_up{name="nlh_frontend_prod"}`

> Prod Celery worker service name is **`celery`**, not `celery_worker`.

## Alert rules

| Alert | Condition | For | Severity |
|-------|-----------|-----|----------|
| `ComposeServiceNotRunning` | `compose_service_up == 0` | 3m | critical |
| `ContainerNotRunning` | `container_up == 0` | 3m | critical |
| `HostDown` | `up{job="node"} == 0` | 2m | critical |
| `HostHighCPU` | CPU > 90% | 10m | warning |
| `HostHighMemory` | RAM > 90% | 5m | warning |
| `HostDiskAlmostFull` | free disk < 15% | 5m | warning |
| `MonitoringTargetDown` | scrape target down | 2m | warning |

Rules live in [`monitoring/rules/`](../monitoring/rules/).

## Alert channels

Configured via `.env.monitoring` (gitignored). At least one channel is required.

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Template: [`.env.monitoring.example`](../.env.monitoring.example).  
On production: place at `$DEPLOY_PATH/.env.monitoring` (same directory as `docker-compose.monitoring.prod.yml`).

Config is generated at Alertmanager start by [`monitoring/render-alertmanager.sh`](../monitoring/render-alertmanager.sh).

## Local quick start

```bash
cd NewLevelHub-Backend
cp .env.monitoring.example .env.monitoring   # fill secrets
docker compose -f docker-compose.local.yml up -d
docker compose -f docker-compose.monitoring.yml up -d --build
```

- Prometheus: http://localhost:9090  
- Alertmanager: http://localhost:9093  

Test:

```bash
docker compose -f docker-compose.local.yml stop redis
# ~3 minutes → Slack / Telegram: only redis
docker compose -f docker-compose.local.yml start redis
```

## Production install (manual, static)

Run on the production host inside `$DEPLOY_PATH` (backend deploy directory — must contain `docker-compose.prod.yml` and the `monitoring/` folder).

### 1. Copy files

From your laptop (adjust host/path):

```bash
scp docker-compose.monitoring.prod.yml user@prod:$DEPLOY_PATH/
scp -r monitoring user@prod:$DEPLOY_PATH/
scp .env.monitoring.example user@prod:$DEPLOY_PATH/
```

Or sync via git if the server has the Backend repo checked out.

### 2. Secrets

```bash
cd "$DEPLOY_PATH"
cp .env.monitoring.example .env.monitoring
# Edit: SLACK_WEBHOOK_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

### 3. Start

```bash
docker compose -f docker-compose.monitoring.prod.yml up -d --build
docker compose -f docker-compose.monitoring.prod.yml ps
```

### 4. Verify

```bash
curl -s http://127.0.0.1:9090/-/healthy
curl -s http://127.0.0.1:9093/-/healthy
curl -s http://127.0.0.1:9101/metrics | grep -E 'compose_service_up|container_up'
```

All expected `compose_service_up` / `container_up` series should be `1`.

UI via SSH tunnel (nothing is public):

```bash
ssh -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 user@prod
# then open http://localhost:9090 and http://localhost:9093
```

### 5. Acceptance test

```bash
docker stop nlh_redis_prod
# wait ~3 minutes → Slack + Telegram: ComposeServiceNotRunning for redis only
docker start nlh_redis_prod
# → resolved notification
```

## App CD vs monitoring

Backend CD recreates `backend`, `nginx`, `celery`, `celery_beat` (and optionally `dozzle`). Those containers can be down for up to ~60–90s during deploy.

- Alert `for: 3m` is meant to absorb typical CD windows.
- If a deploy stalls longer than 3 minutes, you **will** get a legitimate downtime alert — that is intended.
- Monitoring itself is **not** recreated by app CD (separate project `nlh-monitoring`).

To update monitoring configs later:

```bash
# after copying new monitoring/ files
docker compose -f docker-compose.monitoring.prod.yml up -d --build
# or reload Prometheus only:
curl -X POST http://127.0.0.1:9090/-/reload
```

## Local vs production differences

| | Local | Production |
|--|-------|------------|
| Compose file | `docker-compose.monitoring.yml` | `docker-compose.monitoring.prod.yml` |
| Container names | `nlh_*_local` | `nlh_*_prod` |
| Ports | `0.0.0.0:9090` etc. | `127.0.0.1:9090` etc. |
| Watched services | + minio, `celery_worker` | + nginx, `celery`, frontend container |
| node_exporter | no host rootfs mount | `--path.rootfs=/host` |
| restart policy | `unless-stopped` | `always` |

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Alertmanager exits immediately | `.env.monitoring` missing or empty Slack **and** Telegram |
| No Telegram messages | `TELEGRAM_CHAT_ID` must be **your** chat id from `getUpdates`, not the bot id from the token |
| Slack HTML / 4xx errors | Webhook must be `https://hooks.slack.com/services/T.../B.../...` |
| All services flip to 0 | Docker socket mount; exporter logs: `docker logs nlh_compose_exporter_prod` |
| Frontend always down | Confirm container name is exactly `nlh_frontend_prod` |
| Disk metrics wrong on Linux | Prod compose must use node_exporter with `/host` mount |
| Cannot open Prometheus from laptop | Use SSH tunnel; ports are localhost-only on prod |

## Out of scope (for now)

- Grafana
- Public metrics / Alertmanager URL
- Blackbox HTTPS probes
- Automatic CD for monitoring on every `main` push
