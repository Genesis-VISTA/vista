#!/usr/bin/env bash
# Build a VISTA package for a platform other than this host's.
#
# There is no cross-compilation here. The package carries a target-specific
# interpreter, compiled wheels, a Node runtime and a sandbox image, and
# producing it means *running* on the target platform: creating the
# environments, building the standalone UI, and exercising the finished
# artifact all execute target binaries. So the build runs inside a container
# of the target platform, under emulation. That is the point rather than a
# compromise -- a host that already ran the target platform would just run
# scripts/build_local_package.sh directly.
#
# Usage:
#   ./scripts/build_in_docker.sh                       # build for linux/amd64
#   ./scripts/build_in_docker.sh --check               # preflight only
#   ./scripts/build_in_docker.sh --platform linux/arm64
#
# Options:
#   --platform PLAT   Target platform (default: linux/amd64)
#   --allow-dirty     Build even though the working tree has uncommitted
#                      changes. Those changes are still excluded: the source
#                      comes from `git archive HEAD`
#   --keep-work       Leave the extracted source and sandbox archive in place
#   -h, --help        Show this help
#
# Everything else is passed straight to build_local_package.sh, so
# --payload, --vector-store, --without-hpc, --without-citations,
# --archive-format and --skip-smoke-test all work as documented there.
# Host paths given to --payload and --vector-store are mounted into the
# container automatically.
#
# Credentials come from this environment, not from .env: the container gets a
# tree extracted from git, which never contains one. Forwarded when set:
#   VISTA_DATA_TOKEN, OPENAI_API_KEY, OPENAI_BASE_URL, VISTA_BACKEND_MODEL,
#   AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION
#   AMSC_GIT_TOKEN  -- a gitlab.com token that can read the amsc2 repository
#                      providing amscrot-py. Your macOS keychain credential is
#                      unreachable from a Linux container, so without this the
#                      preflight fails and asks for --without-hpc.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

usage() {
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
}

die() {
  echo "error: $*" >&2
  exit 1
}

log() {
  printf '\n==> %s\n' "$*"
}

PLATFORM=linux/amd64
ALLOW_DIRTY=false
KEEP_WORK=false
PASSTHROUGH=()
OUTPUT_DIR="$REPO_ROOT/dist"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --platform)
      [[ $# -ge 2 ]] || die "--platform needs a value, e.g. linux/amd64"
      PLATFORM="$2"; shift ;;
    --allow-dirty) ALLOW_DIRTY=true ;;
    --keep-work) KEEP_WORK=true ;;
    # Captured rather than passed through: the container sees a different
    # path for it, and it has to be mounted.
    --output-dir)
      [[ $# -ge 2 ]] || die "--output-dir needs a directory"
      OUTPUT_DIR="$2"; shift ;;
    *) PASSTHROUGH+=("$1") ;;
  esac
  shift
done

[[ "$PLATFORM" == linux/* ]] \
  || die "only linux platforms can be built this way: got $PLATFORM"

# ─── host prerequisites ─────────────────────────────────────────────────────

command -v docker >/dev/null 2>&1 \
  || die "docker is not installed; it is what provides the target platform here"
docker info >/dev/null 2>&1 \
  || die "the docker daemon is not responding; start Docker Desktop"
docker buildx version >/dev/null 2>&1 \
  || die "docker buildx is not available, and it is what builds for another platform"

# ─── source: committed content only ─────────────────────────────────────────

# The container gets a tree extracted from git rather than a mount of the
# working copy, for a reason that bites immediately otherwise: `stage_ui` and
# `build_mcp_app` run `npm ci` and `npm run build` *inside* the source tree,
# and both skip the install when `node_modules` already exists. A mounted
# working copy would have its macOS node_modules reused under Linux, and its
# .next replaced with linux-x64 output. Extracting also means the build cannot
# depend on anything uncommitted, which is the property a release build wants.
DIRTY_COUNT="$(git -C "$REPO_ROOT" status --porcelain | wc -l | tr -d ' ')"
if [[ "$DIRTY_COUNT" != 0 && "$ALLOW_DIRTY" != true ]]; then
  die "the working tree has $DIRTY_COUNT uncommitted change(s).

  The container builds from \`git archive HEAD\`, so those changes would be
  silently absent from the artifact while the version still claimed to be
  this commit. Commit them, or pass --allow-dirty to build HEAD anyway."
fi

# Computed here and passed in, because the extracted tree has no .git and the
# build would otherwise have no way to name itself.
VERSION="${VISTA_VERSION:-}"
if [[ -z "$VERSION" ]]; then
  VERSION="0.1.0+$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
  [[ "$DIRTY_COUNT" == 0 ]] || VERSION="${VERSION}-dirty"
fi

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
WORK="$OUTPUT_DIR/.build-work"
cleanup() {
  [[ "$KEEP_WORK" == true ]] || rm -rf "$WORK"
}
trap cleanup EXIT

rm -rf "$WORK"
mkdir -p "$WORK/src"

log "extracting the source at $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
git -C "$REPO_ROOT" archive --format=tar HEAD | tar -x -C "$WORK/src"
echo "source      : $(du -sh "$WORK/src" | cut -f1)"

# ─── the sandbox image, built on the host ───────────────────────────────────

# Built out here rather than in the container, because a container has no
# daemon of its own. Building it inside via a mounted host socket would
# produce the *host's* architecture, which nothing downstream would notice --
# an arm64 sandbox loads fine inside an x86_64 package and only fails when an
# agent runs code. `--output type=docker` writes the archive directly, so the
# host's image store is never touched.
SANDBOX_DIR="$WORK/src/mcp_servers/dev_mcp_server/src/dev_mcp_server/docker"
[[ -f "$SANDBOX_DIR/Dockerfile" ]] \
  || die "no sandbox Dockerfile in the extracted source at $SANDBOX_DIR"

log "building the sandbox image for $PLATFORM"
docker buildx build \
  --platform "$PLATFORM" \
  -t vista-sandbox:latest \
  -f "$SANDBOX_DIR/Dockerfile" \
  --output "type=docker,dest=$WORK/sandbox-image.tar" \
  "$SANDBOX_DIR" >/dev/null
echo "sandbox     : $(du -sh "$WORK/sandbox-image.tar" | cut -f1)"

# ─── the build environment ──────────────────────────────────────────────────

BUILD_IMAGE="vista-build:${PLATFORM//\//-}"

log "building the build environment ($BUILD_IMAGE)"
docker build \
  --platform "$PLATFORM" \
  -t "$BUILD_IMAGE" \
  -f "$REPO_ROOT/scripts/Dockerfile.build" \
  "$REPO_ROOT/scripts" >/dev/null

# ─── mounts and environment ─────────────────────────────────────────────────

MOUNTS=(-v "$WORK/src:/build" -v "$OUTPUT_DIR:/out" -v "$WORK:/work:ro")
ARGS=(--sandbox-image /work/sandbox-image.tar --output-dir /out)

# --payload and --vector-store name host directories, so they are remapped to
# read-only mounts. Anything else passes through untouched.
i=0
while (( i < ${#PASSTHROUGH[@]} )); do
  arg="${PASSTHROUGH[$i]}"
  case "$arg" in
    --payload|--vector-store)
      (( i + 1 < ${#PASSTHROUGH[@]} )) || die "$arg needs a directory"
      host_path="${PASSTHROUGH[$((i + 1))]}"
      [[ -d "$host_path" ]] || die "$arg directory does not exist: $host_path"
      host_path="$(cd "$host_path" && pwd)"
      name="${arg#--}"
      MOUNTS+=(-v "$host_path:/mnt/$name:ro")
      ARGS+=("$arg" "/mnt/$name")
      i=$((i + 2)) ;;
    *)
      ARGS+=("$arg")
      i=$((i + 1)) ;;
  esac
done

# The embedding weights are safetensors and JSON, so they carry across
# architectures unchanged. `stage_embedding_weights` prefers a copy under
# data/huggingface over a download, so mounting the host's cache saves
# roughly 544 MB of transfer per build. Absent, the build downloads them.
HOST_HF="$REPO_ROOT/data/huggingface"
if [[ -d "$HOST_HF/hub" ]]; then
  MOUNTS+=(-v "$HOST_HF:/build/data/huggingface:ro")
  echo "weights     : reusing the host cache at $HOST_HF"
fi

ENVS=(-e "VISTA_VERSION=$VERSION")
for name in VISTA_DATA_TOKEN OPENAI_API_KEY OPENAI_BASE_URL VISTA_BACKEND_MODEL \
            AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_VERSION \
            AMSC_GIT_TOKEN; do
  [[ -n "${!name:-}" ]] && ENVS+=(-e "$name")
done

# ─── run ────────────────────────────────────────────────────────────────────

log "building $VERSION for $PLATFORM"
echo "This runs under emulation and is slow. Reuse a vector store with"
echo "--vector-store to skip the longest stage."

# The amsc2 rewrite is configured from inside the container, expanding
# AMSC_GIT_TOKEN there, so the token never appears in this host's process
# arguments where `ps` could read it.
docker run --rm \
  --platform "$PLATFORM" \
  "${MOUNTS[@]}" \
  "${ENVS[@]}" \
  -w /build \
  "$BUILD_IMAGE" \
  bash -c '
    set -euo pipefail
    if [[ -n "${AMSC_GIT_TOKEN:-}" ]]; then
      git config --global \
        url."https://oauth2:${AMSC_GIT_TOKEN}@gitlab.com/amsc2/".insteadOf \
        "https://gitlab.com/amsc2/"
    fi
    exec "$@"
  ' bash /build/scripts/build_local_package.sh "${ARGS[@]}"

log "done"
ls -la "$OUTPUT_DIR" | grep -v '^total' | grep -v '\.build-work'
