#!/bin/bash
# Pushes an already-built vista server image to Model Services ECR (amsc-ms-dev).
#
#   ./aws/build-image.sh
#   ./aws/push-image.sh
#
# Registry lives in the MS account (890890990154), same place as
# vista/amsc-vista-nginx. Requires AWS creds that can ecr:PutImage there
# (PlatformAdmin, or GitLab OIDC via the amsc-ms-dev CI pusher role).
set -euo pipefail

# The local image to push, as tagged by build-image.sh.
SERVER_IMAGE="${1:-vista-server:latest}"

RUNTIME=""
for candidate in docker podman; do
  if command -v "$candidate" >/dev/null 2>&1; then
    RUNTIME="$candidate"
    break
  fi
done
[[ -n "$RUNTIME" ]] || { echo "error: neither docker nor podman found on PATH" >&2; exit 1; }

"$RUNTIME" image inspect "$SERVER_IMAGE" >/dev/null 2>&1 || {
  echo "error: local image '$SERVER_IMAGE' not found — run ./aws/build-image.sh first" >&2
  exit 1
}

AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-890890990154}"
AWS_REGION="${AWS_REGION:-us-east-1}"
IMAGE_ECR_REPO="${IMAGE_ECR_REPO:-vista/vista-server}"
# Tag by date rather than :latest so a pinned host/unit cannot silently pick
# up a different build than the one that was tested.
TAG="${TAG:-$(date +%Y%m%d-%H%M)}"
registry="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ref="${registry}/${IMAGE_ECR_REPO}:${TAG}"

echo "==> Pushing $ref"
"$RUNTIME" tag "$SERVER_IMAGE" "$ref"
aws ecr get-login-password --region "$AWS_REGION" \
  | "$RUNTIME" login --username AWS --password-stdin "$registry"
"$RUNTIME" push "$ref"

cat <<MSG

Pushed $ref

Pin this ref where the workload runs (pod image, or /etc/vista/vista.env):
  VISTA_SERVER_IMAGE=$ref
MSG
