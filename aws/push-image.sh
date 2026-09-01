#!/bin/bash
# Pushes an already-built vista server image to the VISTA account's ECR, which
# is what the beta host pulls (deploy/README.md, Stage 1).
#
#   ./aws/build-image.sh                              # build
#   AWS_ACCOUNT_ID=288834681766 ./aws/push-image.sh   # then push
#
# Pushing to the *VISTA* account, not the cluster's: the puller is an EC2
# instance in 288834681766 using its own instance profile, so a same-account
# repository needs no cross-account repository policy. The proxy image is the
# opposite case — EKS pulls it, so it lives in the cluster account
# (deploy/proxy/build-image.sh).
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

: "${AWS_ACCOUNT_ID:?set AWS_ACCOUNT_ID — the VISTA account that owns the ECR repo}"
AWS_REGION="${AWS_REGION:-us-east-1}"
IMAGE_ECR_REPO="${IMAGE_ECR_REPO:-images/vista-server}"
# Tag by date rather than :latest. vista.service pins a tag so that restarting
# the unit cannot silently pick up a different build than the one that was
# tested; :latest makes that impossible to guarantee.
TAG="${TAG:-$(date +%Y%m%d-%H%M)}"
registry="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ref="${registry}/${IMAGE_ECR_REPO}:${TAG}"

echo "==> Pushing $ref"
"$RUNTIME" tag "$SERVER_IMAGE" "$ref"
aws ecr get-login-password --region "$AWS_REGION" \
  | "$RUNTIME" login --username AWS --password-stdin "$registry"
"$RUNTIME" push "$ref"

cat <<EOF

Pushed $ref

Set this on the host, in /etc/vista/vista.env:
  VISTA_SERVER_IMAGE=$ref
EOF
