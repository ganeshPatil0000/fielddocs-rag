#!/usr/bin/env bash
# Upload a document through the API's presigned POST flow (admin token required).
#   TOKEN=... ./scripts/upload_doc.sh sample_docs/fttp_ont_install_guide.md
set -euo pipefail
cd "$(dirname "$0")/.."

FILE="${1:?usage: upload_doc.sh <file>}"
: "${TOKEN:?export TOKEN first (scripts/get_token.sh)}"
API_URL="${API_URL:-$(terraform -chdir=infra/terraform output -raw api_url)}"
NAME=$(basename "$FILE")

RESP=$(curl -sf -X POST "$API_URL/v1/documents/upload-url" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"filename\": \"$NAME\", \"content_type\": \"application/octet-stream\"}")

DOC_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["doc_id"])' <<<"$RESP")
URL=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["url"])' <<<"$RESP")
# Turn the presigned fields into curl -F arguments (file must be the last field).
mapfile -t FORM < <(python3 -c 'import json,sys
for k, v in json.load(sys.stdin)["fields"].items(): print(f"{k}={v}")' <<<"$RESP")
ARGS=()
for f in "${FORM[@]}"; do ARGS+=(-F "$f"); done

curl -sf -X POST "$URL" "${ARGS[@]}" -F "file=@${FILE}" >/dev/null
echo "uploaded $NAME as doc_id=$DOC_ID - ingestion runs asynchronously."
echo "check status: curl -s -H \"Authorization: Bearer \$TOKEN\" $API_URL/v1/documents/$DOC_ID"
