#!/usr/bin/env bash
# Build a VISTA package for a platform other than this host's.
#
# There is no cross-compilation here. The package carries a target-specific
# interpreter, compiled wheels, a Node runtime and two OCI images, and
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
#   --ca-bundle FILE  A PEM holding your organisation's root CA, for a network
#                      that inspects TLS. Needed because a macOS host keeps
#                      that root in its keychain, which a Linux container
#                      cannot read
#   --keep-work       Leave the extracted source and image archives in place
#   -h, --help        Show this help
#
# Everything else is passed straight to build_local_package.sh, so
# --payload, --vector-store, --without-citations,
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
#                      preflight fails.
#   PALISADE_GITHUB_TOKEN -- a read-only, fine-grained GitHub PAT scoped to
#                      herronej/palisade_siege_agentic_security, the private
#                      repo `palisade` is a git dependency of. Your own git
#                      credential helper rule for github.com does not reach
#                      this container either, for the same reason as above.
#                      Same variable name CI uses for it.

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

# How to get the proxy's root out of this host's trust store. macOS keeps it
# in a keychain, which is why a container cannot see it in the first place.
ca_export_hint() {
  if [[ "$(uname -s)" == Darwin ]]; then
    cat <<'HINT'
      security find-certificate -a -c <CA name from below> \
        -p /Library/Keychains/System.keychain > ~/root-ca.pem
HINT
  else
    cat <<'HINT'
      # the root is usually already a file here:
      ls /usr/local/share/ca-certificates /etc/pki/ca-trust/source/anchors
HINT
  fi
}

PLATFORM=linux/amd64
ALLOW_DIRTY=false
KEEP_WORK=false
CA_BUNDLE="${VISTA_BUILD_CA_BUNDLE:-}"
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
    --ca-bundle)
      [[ $# -ge 2 ]] || die "--ca-bundle needs a PEM file"
      CA_BUNDLE="$2"; shift ;;
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

# ─── the shipped images, built on the host ──────────────────────────────────

# Built out here rather than in the container, because a container has no
# daemon of its own. Building them inside via a mounted host socket would
# produce the *host's* architecture, which nothing downstream would notice --
# an arm64 image loads fine inside an x86_64 package and fails only when an
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

# Staged rather than using scripts/ directly, so an extra root CA can be
# placed in the context without ever being committed. The directory is always
# created: `COPY ca/` on a missing directory fails the build outright, and
# `update-ca-certificates` reads only .crt files so the placeholder is inert.
CTX="$WORK/context"
mkdir -p "$CTX/ca"
cp "$REPO_ROOT/scripts/Dockerfile.build" "$CTX/Dockerfile.build"
: > "$CTX/ca/.keep"
if [[ -n "$CA_BUNDLE" ]]; then
  [[ -f "$CA_BUNDLE" ]] || die "--ca-bundle is not a file: $CA_BUNDLE"
  grep -q 'BEGIN CERTIFICATE' "$CA_BUNDLE" \
    || die "--ca-bundle holds no PEM certificate: $CA_BUNDLE
  Export it in PEM form, not DER: \`openssl x509 -inform der -in cert.cer -out ca.pem\`"
  # Named .crt because that is the only extension update-ca-certificates reads.
  cp "$CA_BUNDLE" "$CTX/ca/build-host-extra.crt"
  echo "ca bundle   : $CA_BUNDLE"
fi

log "building the build environment ($BUILD_IMAGE)"
if ! docker build \
     --platform "$PLATFORM" \
     -t "$BUILD_IMAGE" \
     -f "$CTX/Dockerfile.build" \
     "$CTX" >"$WORK/build-image.log" 2>&1; then
  tail -25 "$WORK/build-image.log" >&2
  echo >&2
  if grep -q 'self-signed certificate\|SSL certificate problem\|unable to get local issuer' \
       "$WORK/build-image.log"; then
    # Named from the handshake rather than guessed, so the message says which
    # certificate to go and find.
    local proxy_ca
    # `|| true` because this runs inside a failure path: if the probe cannot
    # reach the host, the guidance below still has to print rather than the
    # script dying on a failed assignment under `set -e`.
    proxy_ca="$(
      openssl s_client -connect nodejs.org:443 -servername nodejs.org </dev/null 2>/dev/null \
        | openssl x509 -noout -issuer 2>/dev/null \
        | sed -nE 's|.*/CN=([^/]+).*|\1|p' || true
    )"
    if [[ -n "$CA_BUNDLE" ]]; then
      die "TLS still failed with the CA bundle supplied.

    bundle : $CA_BUNDLE
    proxy  : ${proxy_ca:-could not be read}

  The bundle does not contain the root that signs this proxy's certificates,
  or holds only an intermediate. Export the *root* -- the last entry in the
  chain, the one issued to itself -- and try again:

      openssl s_client -connect nodejs.org:443 -servername nodejs.org \\
        </dev/null 2>/dev/null | grep 'i:'"
    fi
    die "the build environment could not verify TLS.

  This network inspects TLS: the certificate served for nodejs.org is signed
  by ${proxy_ca:-a private CA}, not by a public authority. Your host trusts
  that signer because it sits in the system keychain, which a Linux container
  cannot read, so the same URL works outside the container and fails inside
  it.

  Export that root and hand it back:
$(ca_export_hint)
      ./scripts/build_in_docker.sh --ca-bundle ~/root-ca.pem ...

  Or set VISTA_BUILD_CA_BUNDLE once, so it applies to every build."
  fi
  die "building the build environment failed; see $WORK/build-image.log"
fi

# ─── mounts and environment ─────────────────────────────────────────────────

MOUNTS=(-v "$WORK/src:/build" -v "$OUTPUT_DIR:/out" -v "$WORK:/work:ro")
ARGS=(--sandbox-image /work/sandbox-image.tar \
      --output-dir /out)

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

ENVS=(-e "VISTA_VERSION=$VERSION" -e "VISTA_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)")
# --keep-work leaves the staged context in place; without it the trap removes
# the CA copy along with everything else once the build finishes.
for name in VISTA_DATA_TOKEN OPENAI_API_KEY OPENAI_BASE_URL VISTA_BACKEND_MODEL \
            AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_VERSION \
            AMSC_GIT_TOKEN PALISADE_GITHUB_TOKEN; do
  [[ -n "${!name:-}" ]] && ENVS+=(-e "$name")
done

# ─── run ────────────────────────────────────────────────────────────────────

log "building $VERSION for $PLATFORM"
echo "This runs under emulation and is slow. Reuse a vector store with"
echo "--vector-store to skip the longest stage."

# Both rewrites are configured from inside the container, expanding their
# tokens there, so neither ever appears in this host's process arguments
# where `ps` could read it.
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
    if [[ -n "${PALISADE_GITHUB_TOKEN:-}" ]]; then
      git config --global \
        url."https://x-access-token:${PALISADE_GITHUB_TOKEN}@github.com/herronej/palisade_siege_agentic_security.git".insteadOf \
        "https://github.com/herronej/palisade_siege_agentic_security.git"
    fi
    exec "$@"
  ' bash /build/scripts/build_local_package.sh "${ARGS[@]}"

log "done"
ls -la "$OUTPUT_DIR" | grep -v '^total' | grep -v '\.build-work'
