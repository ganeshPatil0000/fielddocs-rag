#!/usr/bin/env bash
# Create a Cognito user with a permanent password; add ADMIN=1 to grant upload/delete.
#   EMAIL=me@example.com PASSWORD='Str0ngPassword!' ADMIN=1 ./scripts/create_user.sh
set -euo pipefail
cd "$(dirname "$0")/.."

: "${EMAIL:?set EMAIL}" "${PASSWORD:?set PASSWORD (12+ chars, upper, lower, number)}"
REGION="${AWS_REGION:-ap-southeast-2}"
POOL_ID=$(terraform -chdir=infra/terraform output -raw cognito_user_pool_id)

aws cognito-idp admin-create-user --region "$REGION" --user-pool-id "$POOL_ID" \
  --username "$EMAIL" --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
  --message-action SUPPRESS >/dev/null
aws cognito-idp admin-set-user-password --region "$REGION" --user-pool-id "$POOL_ID" \
  --username "$EMAIL" --password "$PASSWORD" --permanent

if [ "${ADMIN:-0}" = "1" ]; then
  aws cognito-idp admin-add-user-to-group --region "$REGION" --user-pool-id "$POOL_ID" \
    --username "$EMAIL" --group-name admin
  echo "created admin user $EMAIL"
else
  echo "created user $EMAIL"
fi
