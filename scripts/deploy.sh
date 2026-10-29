#!/usr/bin/env bash
# Build the image, push it to ECR, and apply Terraform.
#   ./scripts/deploy.sh            (uses the current git SHA as the image tag)
#   ENVIRONMENT=prod ./scripts/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ENVIRONMENT="${ENVIRONMENT:-dev}"
REGION="${AWS_REGION:-ap-southeast-2}"
ARCH="${LAMBDA_ARCH:-x86_64}"
PLATFORM=$([ "$ARCH" = "arm64" ] && echo linux/arm64 || echo linux/amd64)
TAG="${IMAGE_TAG:-$(git rev-parse --short=12 HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
TF_DIR=infra/terraform
TF_VARS=(-var "environment=${ENVIRONMENT}" -var "region=${REGION}" -var "image_tag=${TAG}" -var "lambda_architecture=${ARCH}")

echo "==> terraform init"
terraform -chdir="$TF_DIR" init -input=false

echo "==> ensure the ECR repository exists (first deploy only creates this)"
terraform -chdir="$TF_DIR" apply -input=false -auto-approve -target=aws_ecr_repository.app "${TF_VARS[@]}"
REPO_URL=$(terraform -chdir="$TF_DIR" output -raw ecr_repository_url)
REGISTRY="${REPO_URL%%/*}"

if aws ecr describe-images --repository-name "${REPO_URL#*/}" --image-ids imageTag="$TAG" --region "$REGION" >/dev/null 2>&1; then
  echo "==> image ${TAG} already in ECR, skipping build"
else
  echo "==> build ${TAG} for ${PLATFORM}"
  # --provenance=false: Lambda cannot use multi-manifest (attestation) images.
  docker build --platform "$PLATFORM" --provenance=false -t "${REPO_URL}:${TAG}" .

  echo "==> push ${TAG} to ECR"
  # Push with a throwaway Docker config that already holds the ECR token, instead of
  # `docker login`. On Windows, docker login always saves to Credential Manager, which
  # can't hold the long ECR token ("The stub received bad data"). The temporary file is
  # deleted when the script ends, and your own Docker config is never touched.
  PUSH_CONFIG=$(mktemp -d)
  trap 'rm -rf "$PUSH_CONFIG"' EXIT
  DOCKER_DIR="${DOCKER_CONFIG:-$HOME/.docker}"
  if [ -d "$DOCKER_DIR/contexts" ]; then cp -r "$DOCKER_DIR/contexts" "$PUSH_CONFIG/"; fi
  ECR_AUTH=$(printf 'AWS:%s' "$(aws ecr get-login-password --region "$REGION")" | base64 | tr -d '\n')
  printf '{"currentContext": "%s", "auths": {"%s": {"auth": "%s"}}}\n' \
    "$(docker context show)" "$REGISTRY" "$ECR_AUTH" >"$PUSH_CONFIG/config.json"
  unset ECR_AUTH
  docker --config "$PUSH_CONFIG" push "${REPO_URL}:${TAG}"
fi

echo "==> terraform apply"
terraform -chdir="$TF_DIR" apply -input=false "${TF_VARS[@]}" ${AUTO_APPROVE:+-auto-approve}

echo
terraform -chdir="$TF_DIR" output
echo
echo "Next: ./scripts/put_secrets.sh (first deploy only), ./scripts/deploy_web.sh, ./scripts/smoke_test.sh"
