#!/bin/bash
# Builds the vista server image
set -euo pipefail

REPO_ROOT="$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")"
cd "$REPO_ROOT"

SANDBOX_IMAGE="vista-sandbox:latest"
SERVER_IMAGE="${1:-vista-server:latest}"

TAR_FILE="$REPO_ROOT/aws/vista-sandbox.tar"
DIGEST_FILE="$TAR_FILE.digest"

RUNTIME=""
for candidate in docker podman; do
  if command -v "$candidate" >/dev/null 2>&1; then
    RUNTIME="$candidate"
    break
  fi
done
[[ -n "$RUNTIME" ]] || { echo "error: neither docker nor podman found on PATH" >&2; exit 1; }
export DOCKER_BUILDKIT=1

echo "==> Building $SANDBOX_IMAGE"
"$RUNTIME" build -t "$SANDBOX_IMAGE" "$REPO_ROOT/mcp_servers/dev_mcp_server/src/dev_mcp_server/docker"

# Config (content) digest of the just-built image. podman omits the "sha256:" prefix;
# strip it so the two runtimes compare equal.
built_digest="$("$RUNTIME" image inspect "$SANDBOX_IMAGE" --format '{{.Id}}')"
built_digest="${built_digest##sha256:}"

# Export the sandbox image to a tar (skip if it already matches)
if [[ ! -f "$TAR_FILE" || ! -f "$DIGEST_FILE" || "$(cat "$DIGEST_FILE")" != "$built_digest" ]]; then
  echo "==> Exporting $SANDBOX_IMAGE -> $TAR_FILE"
  rm -f "$TAR_FILE" "$DIGEST_FILE"
  if [[ "$RUNTIME" == podman ]]; then
    "$RUNTIME" save --format docker-archive -o "$TAR_FILE" "$SANDBOX_IMAGE"
  else
    "$RUNTIME" save -o "$TAR_FILE" "$SANDBOX_IMAGE"
  fi
  printf '%s\n' "$built_digest" > "$DIGEST_FILE"
fi

# Build the server image
# The private amscrot-py GitLab dependency is private, pass the Gitlab PAT to Docker build
if [[ -z "${GITLAB_TOKEN:-}" ]]; then
  # Read from .git-credentials
  GITLAB_TOKEN="$(printf 'protocol=https\nhost=gitlab.com\n\n' \
    | GIT_TERMINAL_PROMPT=0 git credential fill 2>/dev/null \
    | sed -n 's/^password=//p')" || true
fi

if [[ -z "${GITLAB_TOKEN:-}" ]]; then
  echo "error: GITLAB_TOKEN is not set and no stored gitlab.com credential was found for 'gitlab.com/amsc2'" >&2
  exit 1
fi
export GITLAB_TOKEN

echo "==> Building server image $SERVER_IMAGE"
"$RUNTIME" build \
  -f "$REPO_ROOT/aws/Dockerfile.server" \
  --secret "id=gitlab_token,env=GITLAB_TOKEN" \
  -t "$SERVER_IMAGE" \
  "$REPO_ROOT"

echo "==> Built $SERVER_IMAGE"

echo "Push it with: ./aws/push-image.sh $SERVER_IMAGE"
