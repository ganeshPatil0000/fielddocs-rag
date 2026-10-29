#!/usr/bin/env bash
# Post-deploy smoke test: health, auth enforcement, and one real question.
#   TOKEN=... ./scripts/smoke_test.sh
set -euo pipefail
cd "$(dirname "$0")/.."

API_URL="${API_URL:-$(terraform -chdir=infra/terraform output -raw api_url)}"
fail() { echo "FAIL: $*" >&2; exit 1; }

echo "-> GET /health"
curl -sf "$API_URL/health" | grep -q '"ok"' || fail "health check"

echo "-> unauthenticated chat must be rejected"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API_URL/v1/chat" \
  -H 'Content-Type: application/json' -d '{"question":"test question"}')
[ "$code" = "401" ] || fail "expected 401 without token, got $code"

echo "-> browser CORS preflight must work without a token"
headers=$(curl -s -o /dev/null -D - -X OPTIONS "$API_URL/v1/documents" \
  -H 'Origin: http://localhost:5173' -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: authorization')
echo "$headers" | grep -qi '^access-control-allow-origin:' ||
  fail "CORS preflight (OPTIONS) was rejected - the web app can't call the API"

if [ -n "${TOKEN:-}" ]; then
  echo "-> authenticated chat"
  body=$(curl -sf -X POST "$API_URL/v1/chat" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"question":"What received optical power is healthy at the ONT?"}') || fail "chat call"
  echo "$body" | python3 -m json.tool | head -30
fi
echo "smoke test passed"
