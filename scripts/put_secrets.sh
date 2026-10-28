#!/usr/bin/env bash
# Store API keys in SSM Parameter Store (SecureString). Prompts so keys never
# land in shell history. Lambdas read them on their next cold start.
set -euo pipefail

ENVIRONMENT="${ENVIRONMENT:-dev}"
PROJECT="${PROJECT:-fielddocs}"
REGION="${AWS_REGION:-us-east-1}"
PREFIX="/${PROJECT}/${ENVIRONMENT}"

read -r -s -p "Pinecone API key: " PINECONE && echo
read -r -s -p "LangSmith API key (blank to skip): " LANGSMITH && echo

aws ssm put-parameter --region "$REGION" --name "${PREFIX}/PINECONE_API_KEY" \
  --type SecureString --overwrite --value "$PINECONE" >/dev/null
echo "stored ${PREFIX}/PINECONE_API_KEY"

if [ -n "$LANGSMITH" ]; then
  aws ssm put-parameter --region "$REGION" --name "${PREFIX}/LANGSMITH_API_KEY" \
    --type SecureString --overwrite --value "$LANGSMITH" >/dev/null
  echo "stored ${PREFIX}/LANGSMITH_API_KEY"
fi

# Force new containers so the next request picks up the new values.
for fn in "${PROJECT}-${ENVIRONMENT}-api" "${PROJECT}-${ENVIRONMENT}-ingest"; do
  aws lambda update-function-configuration --region "$REGION" --function-name "$fn" \
    --description "secrets rotated $(date -u +%FT%TZ)" >/dev/null && echo "recycled $fn"
done
