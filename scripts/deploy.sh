#!/usr/bin/env bash
# Build the image, push it to ECR, and apply Terraform.
#   ./scripts/deploy.sh            (uses the current git SHA as the image tag)
#   ENVIRONMENT=prod ./scripts/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ENVIRONMENT="${ENVIRONMENT:-dev}"
REGION="${AWS_REGION:-us-east-1}"
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
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
  # --provenance=false: Lambda cannot use multi-manifest (attestation) images.
  docker build --platform "$PLATFORM" --provenance=false -t "${REPO_URL}:${TAG}" .
  docker push "${REPO_URL}:${TAG}"
fi

echo "==> terraform apply"
terraform -chdir="$TF_DIR" apply -input=false "${TF_VARS[@]}" ${AUTO_APPROVE:+-auto-approve}

echo
terraform -chdir="$TF_DIR" output
echo
echo "Next: ./scripts/put_secrets.sh (first deploy), then ./scripts/smoke_test.sh"
