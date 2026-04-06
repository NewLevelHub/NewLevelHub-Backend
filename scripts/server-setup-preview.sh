#!/bin/bash
# One-time setup verification for per-PR preview environments.
# Run this on the staging server to confirm everything is ready.
# Usage: bash scripts/server-setup-preview.sh

set -e

echo "=== NewLevelHub Preview Environments: Server Readiness Check ==="
echo ""

PASS=0
FAIL=0

check() {
  local desc="$1"
  local cmd="$2"
  if eval "$cmd" &>/dev/null; then
    echo "  [OK]  $desc"
    PASS=$((PASS + 1))
  else
    echo "  [!!]  $desc"
    FAIL=$((FAIL + 1))
  fi
}

echo "[1] System dependencies"
check "nginx is installed and running"   "systemctl is-active nginx"
check "certbot is installed"             "which certbot"
check "docker is installed"              "which docker"
check "docker compose v2 available"     "docker compose version"
check "passwordless sudo works"          "sudo -n true"

echo ""
echo "[2] Certbot cert for staging.newlevelhub.kz"
check "cert exists" "sudo certbot certificates 2>/dev/null | grep -q 'staging.newlevelhub.kz'"

echo ""
echo "[3] Nginx config"
check "sites-enabled dir exists"  "[ -d /etc/nginx/sites-enabled ]"
check "staging config is enabled" "[ -f /etc/nginx/sites-enabled/staging.newlevelhub.kz ]"

echo ""
echo "[4] Docker"
check "docker daemon is running"  "docker info"

echo ""
echo "================================"
if [ "$FAIL" -eq 0 ]; then
  echo "All checks passed! Server is ready for preview environments."
else
  echo "Some checks failed ($FAIL). Fix issues above before using per-PR previews."
fi
echo "================================"
echo ""
echo "Remaining manual step — add wildcard DNS in ps.kz:"
echo ""
echo "  Go to: https://ps.kz → your domain → DNS records"
echo "  Add:"
echo "    Type:  A"
echo "    Name:  *.staging"
echo "    Value: $(curl -s --max-time 3 ifconfig.me 2>/dev/null || echo '<server-ip>')"
echo "    TTL:   300"
echo ""
echo "  (The existing 'staging A ...' record stays as-is)"
echo ""
echo "After DNS propagates (~5 min), push any PR to get per-PR preview URLs."
