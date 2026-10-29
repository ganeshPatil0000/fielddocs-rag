#!/usr/bin/env bash
# Store API keys in SSM Parameter Store (SecureString). Prompts so keys never
# land in shell history. Lambdas read them on their next cold start.
set -euo pipefail
# Git Bash on Windows rewrites arguments that start with "/" into Windows paths,
# which would turn /fielddocs/dev/... into C:/Program Files/Git/fielddocs/dev/...
export MSYS_NO_PATHCONV=1

ENVIRONMENT="${ENVIRONMENT:-dev}"
PROJECT="${PROJECT:-fielddocs}"
REGION="${AWS_REGION:-ap-southeast-2}"
PREFIX="/${PROJECT}/${ENVIRONMENT}"

# Each prompt is optional: press Enter to keep the value already stored.
put_secret() { # $1 = parameter name, $2 = prompt
  local value
  read -r -s -p "$2 (Enter to keep current): " value && echo
  if [ -n "$value" ]; then
    aws ssm put-parameter --region "$REGION" --name "${PREFIX}/$1" \
      --type SecureString --overwrite --value "$value" >/dev/null
    echo "stored ${PREFIX}/$1 (${#value} characters)"
  else
    echo "kept ${PREFIX}/$1"
  fi
}

put_secret PINECONE_API_KEY "Pinecone API key"
put_secret LANGSMITH_API_KEY "LangSmith API key"
put_secret OPENAI_API_KEY "OpenAI-compatible API key (AIRouter)"

# Force new containers so the next request picks up the new values.
for fn in "${PROJECT}-${ENVIRONMENT}-api" "${PROJECT}-${ENVIRONMENT}-ingest"; do
  aws lambda update-function-configuration --region "$REGION" --function-name "$fn" \
    --description "secrets rotated $(date -u +%FT%TZ)" >/dev/null && echo "recycled $fn"
done
