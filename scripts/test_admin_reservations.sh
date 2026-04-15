#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

echo "Running DEV-77 admin reservations AC tests..."
docker compose -f docker-compose.local.yml exec -T backend \
  python -m pytest apps/bookings/tests/test_admin_reservations.py -q
