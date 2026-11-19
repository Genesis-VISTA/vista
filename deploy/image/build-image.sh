#!/bin/bash
# Builds the VISTA server image: the sandbox image first, exported to a tar the
# server image bakes in, then the server image with the olcf-docs corpus.
#
#   BUILD_INPUTS_DIR=<dir> ./deploy/image/build-image.sh [server-image-tag]
#
# BUILD_INPUTS_DIR is a checkout of Genesis-VISTA/vista-build-inputs, the corpus
# CI's release builds use (.github/scripts/package.sh): vista-data/olcf-docs/ and
# rag_db/. CI checks it out at the commit in .github/build-inputs.env; a checkout
# at another commit builds with a warning.
#
# CONTAINER_RUNTIME=podman picks podman where a docker CLI is also on PATH (on
# Holly, one that talks to podman's socket and has no buildx).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/../.." && pwd)"
cd "$REPO_ROOT"

die() { echo "error: $*" >&2; exit 1; }

SANDBOX_IMAGE="vista-sandbox:latest"
SERVER_IMAGE="${1:-vista-server:latest}"

TAR_FILE="$REPO_ROOT/deploy/image/vista-sandbox.tar"
DIGEST_FILE="$TAR_FILE.digest"

[[ -n "${BUILD_INPUTS_DIR:-}" ]] \
  || die "set BUILD_INPUTS_DIR to a checkout of Genesis-VISTA/vista-build-inputs"
BUILD_INPUTS_DIR="$(cd "$BUILD_INPUTS_DIR" && pwd)"
[[ -d "$BUILD_INPUTS_DIR/vista-data/olcf-docs" && -d "$BUILD_INPUTS_DIR/rag_db" ]] \
  || die "BUILD_INPUTS_DIR ($BUILD_INPUTS_DIR) has no vista-data/olcf-docs/ and rag_db/"

pin="$(sed -n 's/^BUILD_INPUTS_COMMIT=//p' .github/build-inputs.env)"
if inputs_commit="$(git -C "$BUILD_INPUTS_DIR" rev-parse HEAD 2>/dev/null)"; then
  [[ "$inputs_commit" == "$pin" ]] \
    || echo "warning: build inputs are at $inputs_commit; CI pins $pin (.github/build-inputs.env)" >&2
fi

RUNTIME="${CONTAINER_RUNTIME:-}"
if [[ -z "$RUNTIME" ]]; then
  for candidate in docker podman; do
    if command -v "$candidate" >/dev/null 2>&1; then
      RUNTIME="$candidate"
      break
    fi
  done
fi
[[ -n "$RUNTIME" ]] || die "neither docker nor podman found on PATH"
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

echo "==> Building server image $SERVER_IMAGE (corpus from $BUILD_INPUTS_DIR)"
"$RUNTIME" build \
  -f "$REPO_ROOT/deploy/image/Dockerfile" \
  --build-context build-inputs="$BUILD_INPUTS_DIR" \
  -t "$SERVER_IMAGE" \
  "$REPO_ROOT"

echo "==> Done. Built $SERVER_IMAGE"
