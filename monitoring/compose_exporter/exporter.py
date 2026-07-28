#!/usr/bin/env python3
import logging
import os
import time

import docker
from prometheus_client import Gauge, start_http_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("compose_exporter")

COMPOSE_PROJECT = os.getenv("COMPOSE_PROJECT", "newlevelhub-backend")
EXPECTED_SERVICES = [
    s.strip()
    for s in os.getenv(
        "COMPOSE_SERVICES",
        "backend,db,redis,celery_worker,celery_beat,minio,dozzle",
    ).split(",")
    if s.strip()
]
CONTAINER_NAMES = [
    s.strip()
    for s in os.getenv("CONTAINER_NAMES", "").split(",")
    if s.strip()
]

SERVICE_UP = Gauge(
    "compose_service_up",
    "1 if the docker-compose service has a running container, else 0",
    ["service", "project"],
)
CONTAINER_UP = Gauge(
    "container_up",
    "1 if the named Docker container is running, else 0",
    ["name"],
)

# Keep last successful sample so a transient Docker API blip does not
# flip every service to 0 and spam Slack/Telegram.
_last_good_services: dict[str, float] = {service: 1.0 for service in EXPECTED_SERVICES}
_last_good_containers: dict[str, float] = {name: 1.0 for name in CONTAINER_NAMES}


def collect() -> None:
    global _last_good_services, _last_good_containers
    try:
        client = docker.from_env()
        running_services = set()
        running_names = set()

        for container in client.containers.list(all=True):
            labels = container.labels or {}
            name = (container.name or "").lstrip("/")
            if container.status == "running":
                running_names.add(name)
                if labels.get("com.docker.compose.project") == COMPOSE_PROJECT:
                    service = labels.get("com.docker.compose.service")
                    if service:
                        running_services.add(service)

        service_snapshot = {
            service: 1.0 if service in running_services else 0.0
            for service in EXPECTED_SERVICES
        }
        container_snapshot = {
            name: 1.0 if name in running_names else 0.0
            for name in CONTAINER_NAMES
        }
        _last_good_services = service_snapshot
        _last_good_containers = container_snapshot
    except Exception:
        log.exception("Docker API collect failed — keeping last good values")
        service_snapshot = _last_good_services
        container_snapshot = _last_good_containers

    for service, value in service_snapshot.items():
        SERVICE_UP.labels(service=service, project=COMPOSE_PROJECT).set(value)
    for name, value in container_snapshot.items():
        CONTAINER_UP.labels(name=name).set(value)


def main() -> None:
    port = int(os.getenv("EXPORTER_PORT", "9101"))
    start_http_server(port)
    log.info(
        "Listening on :%s project=%s services=%s containers=%s",
        port,
        COMPOSE_PROJECT,
        ",".join(EXPECTED_SERVICES) or "(none)",
        ",".join(CONTAINER_NAMES) or "(none)",
    )
    while True:
        collect()
        time.sleep(int(os.getenv("SCRAPE_INTERVAL", "15")))


if __name__ == "__main__":
    main()
