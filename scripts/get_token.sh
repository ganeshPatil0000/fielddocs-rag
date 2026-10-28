#!/usr/bin/env bash
# Print a Cognito ID token for calling the API:
#   export TOKEN=$(EMAIL=me@example.com PASSWORD='...' ./scripts/get_token.sh)
set -euo pipefail
cd "$(dirname "$0")/.."

: "${EMAIL:?set EMAIL}" "${PASSWORD:?set PASSWORD}"
REGION="${AWS_REGION:-us-east-1}"
CLIENT_ID=$(terraform -chdir=infra/terraform output -raw cognito_client_id)

aws cognito-idp initiate-auth --region "$REGION" --client-id "$CLIENT_ID" \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME="$EMAIL",PASSWORD="$PASSWORD" \
  --query 'AuthenticationResult.IdToken' --output text
