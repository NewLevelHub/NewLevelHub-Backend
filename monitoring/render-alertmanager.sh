#!/bin/sh
set -e

OUT="${1:-/alertmanager/alertmanager.generated.yml}"

has_slack=false
has_telegram=false

if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
  has_slack=true
fi

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  has_telegram=true
fi

if [ "$has_slack" = false ] && [ "$has_telegram" = false ]; then
  echo "Configure at least one channel in .env.monitoring:"
  echo "  SLACK_WEBHOOK_URL=https://hooks.slack.com/services/..."
  echo "  TELEGRAM_BOT_TOKEN=... and TELEGRAM_CHAT_ID=..."
  exit 1
fi

{
  cat <<'EOF'
global:
  resolve_timeout: 5m

route:
  receiver: alerts
  group_by: [alertname, service, name]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: alerts
EOF

  if [ "$has_slack" = true ]; then
    cat <<'EOF'
    slack_configs:
      - send_resolved: true
        title: '[{{ .Status | toUpper }}] {{ .CommonLabels.alertname }}'
        text: |-
          {{ range .Alerts }}
          *{{ .Annotations.summary }}*
          {{ .Annotations.description }}
          {{ if .Labels.service }}*Service:* `{{ .Labels.service }}`{{ end }}
          {{ if .Labels.name }}*Container:* `{{ .Labels.name }}`{{ end }}
          {{ if .Labels.severity }}*Severity:* `{{ .Labels.severity }}`{{ end }}
          {{ end }}
EOF
    printf '        api_url: %s\n' "$SLACK_WEBHOOK_URL"
  fi

  if [ "$has_telegram" = true ]; then
    cat <<'EOF'
    telegram_configs:
      - send_resolved: true
        parse_mode: HTML
        message: |
          {{ range .Alerts -}}
          <b>{{ .Status | toUpper }}</b>: {{ .Annotations.summary }}
          {{ .Annotations.description }}
          {{ if .Labels.service -}}
          Service: <code>{{ .Labels.service }}</code>
          {{ end -}}
          {{ if .Labels.name -}}
          Container: <code>{{ .Labels.name }}</code>
          {{ end -}}
          {{ if .Labels.severity -}}
          Severity: <code>{{ .Labels.severity }}</code>
          {{ end }}
          {{ end }}
EOF
    printf '        bot_token: "%s"\n' "$TELEGRAM_BOT_TOKEN"
    printf '        chat_id: %s\n' "$TELEGRAM_CHAT_ID"
  fi

  cat <<'EOF'

inhibit_rules:
  - source_match:
      severity: critical
    target_match:
      severity: warning
    equal: [alertname, service]
EOF
} > "$OUT"

echo "Alertmanager config written to $OUT (slack=$has_slack telegram=$has_telegram)"
