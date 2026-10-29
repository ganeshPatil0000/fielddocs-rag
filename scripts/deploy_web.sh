#!/usr/bin/env bash
# Build the web app against the deployed API and publish it to S3 + CloudFront.
#   ./scripts/deploy_web.sh        (run after ./scripts/deploy.sh)
set -euo pipefail
# Git Bash on Windows would turn the invalidation path "/index.html" into a Windows path.
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."

REGION="${AWS_REGION:-ap-southeast-2}"
out() { terraform -chdir=infra/terraform output -raw "$1"; }
API_URL=$(out api_url)
CLIENT_ID=$(out cognito_client_id)
BUCKET=$(out web_bucket)
DISTRIBUTION=$(out web_distribution_id)
WEB_URL=$(out web_url)

echo "==> build the web app for $API_URL"
(
  cd frontend
  [ -d node_modules ] || npm ci
  # Real environment variables take priority over frontend/.env* files.
  VITE_API_URL="$API_URL" VITE_COGNITO_CLIENT_ID="$CLIENT_ID" VITE_AWS_REGION="$REGION" npm run build
)

# Content types are set explicitly: on Windows the AWS CLI guesses them from the registry
# and can label .js as text/plain, which browsers refuse to run.
upload() { # upload <source dir> <s3 prefix> <pattern> <content type> <cache control> [extra args]
  aws s3 sync "$1" "s3://${BUCKET}$2" --region "$REGION" --only-show-errors \
    --exclude "*" --include "$3" "${@:6}" --content-type "$4" --cache-control "$5"
}
FOREVER="public, max-age=31536000, immutable" # file names in assets/ contain a content hash
RECHECK="no-cache"                            # always ask whether there's a newer version

echo "==> upload to s3://$BUCKET"
upload frontend/dist/assets /assets "*.js" "text/javascript; charset=utf-8" "$FOREVER"
upload frontend/dist/assets /assets "*.css" "text/css; charset=utf-8" "$FOREVER"
upload frontend/dist/assets /assets "*.woff2" "font/woff2" "$FOREVER"
upload frontend/dist/assets /assets "*.woff" "font/woff" "$FOREVER"
upload frontend/dist/assets /assets "*.svg" "image/svg+xml" "$FOREVER"
upload frontend/dist "" "*.svg" "image/svg+xml" "$RECHECK" --exclude "assets/*"
# index.html last, so it never points at assets that aren't uploaded yet.
upload frontend/dist "" "*.html" "text/html; charset=utf-8" "$RECHECK" --exclude "assets/*"

echo "==> refresh the CloudFront cache"
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" \
  --paths "/" "/index.html" "/favicon.svg" --query Invalidation.Id --output text

echo
echo "Web app: $WEB_URL"
echo "(the first deploy can take a few minutes to reach every CloudFront location)"
