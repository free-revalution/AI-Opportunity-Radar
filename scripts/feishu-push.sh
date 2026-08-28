#!/usr/bin/env bash
# Trigger a Feishu daily digest push from the command line.
#
# Usage:
#   ./scripts/feishu-push.sh          # standard 5 entries
#   ./scripts/feishu-push.sh 10       # 10 entries
#   ./scripts/feishu-push.sh 5 70.0   # 5 entries, min_score 70.0
#
# Reads X-Radar-Webhook secret from .env automatically.

set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

MAX_ENTRIES="${1:-5}"
MIN_SCORE="${2:-0}"

SECRET=$(grep '^APP_SECRET_KEY=' .env | cut -d= -f2-)
if [[ -z "$SECRET" ]]; then
  echo "❌ APP_SECRET_KEY not set in .env" >&2
  exit 1
fi

echo "📤  Pushing Feishu digest (max_entries=$MAX_ENTRIES, min_score=$MIN_SCORE)..."
RESPONSE=$(curl -s -X POST http://localhost:8000/api/internal/notifications/digest/send \
  -H "X-Radar-Webhook: $SECRET" \
  -H "Content-Type: application/json" \
  -d "{\"channel\":\"feishu\",\"max_entries\":$MAX_ENTRIES,\"min_score\":$MIN_SCORE}")

echo "$RESPONSE" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
print(f\"  delivered:  {d['notifications_delivered']}\")
print(f\"  failed:     {d['notifications_failed']}\")
print(f\"  text_chars: {d['text_chars']}\")
if d['errors']:
    print(f\"  errors:     {d['errors']}\")
print()
preview = d.get('preview', '').replace('\\\\\\\\', '\\\\').replace('\\\\.', '.')
print('--- preview ---')
print(preview[:800])
if len(preview) > 800:
    print(f'... ({len(preview) - 800} more chars)')
"