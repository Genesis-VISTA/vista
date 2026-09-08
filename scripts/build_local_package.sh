#!/usr/bin/env bash
# Build a self-contained, relocatable VISTA package for one platform.
#
# The output is an archive a researcher unpacks and runs with `./vista`: no
# Docker, no HuggingFace token, no GitLab access, no `.env`. Everything the
# running system needs -- interpreters, virtual environments, the standalone
# UI, the sandbox image, the molten-salt corpus and its prebuilt vector store,
# the embedding weights -- is inside it.
#
# This script is the opposite side of that bargain: the build host needs the
# credentials and tooling so the recipient does not.
#
# Usage:
#   ./scripts/build_local_package.sh                    # build for this platform
#   ./scripts/build_local_package.sh --check            # preflight only, no build
#   ./scripts/build_local_package.sh --payload DIR      # use an unpacked vista-data tree
#   ./scripts/build_local_package.sh --output-dir DIR   # where the archive lands
#
# Options:
#   --check              Run the preflight and exit; builds nothing
#   --payload DIR        Unpacked vista-data tree to use instead of fetching it
#                         with VISTA_DATA_TOKEN
#   --output-dir DIR     Archive destination (default: dist/)
#   --archive-format FMT gz (default), zstd, or none to leave the tree unpacked
#   --without-hpc        Build without amscrot-py; HPC job submission will not
#                         work in the result, and the manifest records that
#   --skip-smoke-test    Skip the post-build unpack-and-run verification
#   --keep-staging       Leave the staging tree in place for inspection
#   -h, --help           Show this help

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ─── house idiom (matches scripts/ci-local.sh) ──────────────────────────────

usage() {
  sed -n '2,29p' "$0" | sed -E 's/^# ?//'
}

die() {
  echo "error: $*" >&2
  exit 1
}

log() {
  printf '\n==> %s\n' "$*"
}

warn() {
  echo "warning: $*" >&2
}

# ─── defaults ───────────────────────────────────────────────────────────────

CHECK_ONLY=false
PAYLOAD_DIR=''
OUTPUT_DIR="$REPO_ROOT/dist"
ARCHIVE_FORMAT=gz
WITHOUT_HPC=false
SKIP_SMOKE_TEST=false
KEEP_STAGING=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --check) CHECK_ONLY=true ;;
    --without-hpc) WITHOUT_HPC=true ;;
    --skip-smoke-test) SKIP_SMOKE_TEST=true ;;
    --keep-staging) KEEP_STAGING=true ;;
    --payload)
      [[ $# -ge 2 ]] || die "--payload needs a directory"
      PAYLOAD_DIR="$2"; shift ;;
    --output-dir)
      [[ $# -ge 2 ]] || die "--output-dir needs a directory"
      OUTPUT_DIR="$2"; shift ;;
    --archive-format)
      [[ $# -ge 2 ]] || die "--archive-format needs gz, zstd, or none"
      ARCHIVE_FORMAT="$2"; shift ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

case "$ARCHIVE_FORMAT" in
  gz|zstd|none) ;;
  *) die "unknown archive format: $ARCHIVE_FORMAT (want gz, zstd, or none)" ;;
esac

# ─── identity ───────────────────────────────────────────────────────────────

# No tags in this repo and every component sits at 0.1.0, so the commit is what
# actually identifies a build. Readable, and traceable back to a tree.
VERSION="${VISTA_VERSION:-}"
if [[ -z "$VERSION" ]]; then
  VERSION="0.1.0+$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
  if [[ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]]; then
    VERSION="$VERSION-dirty"
  fi
fi

case "$(uname -s)" in
  Darwin) TARGET_OS=macos ;;
  Linux)  TARGET_OS=linux ;;
  *) die "unsupported build platform: $(uname -s) (want Darwin or Linux)" ;;
esac
TARGET_ARCH="$(uname -m)"
PACKAGE_NAME="vista-${VERSION}-${TARGET_OS}-${TARGET_ARCH}"

# The private dependency HPC submission needs, read from the file that declares
# it so the preflight cannot check a stale URL.
AMSC_GIT_URL="$(
  sed -nE 's/.*"amscrot-py @ git\+(https:\/\/[^"]+)".*/\1/p' \
    "$REPO_ROOT/mcp_servers/vista_mcp_server/pyproject.toml" | head -1
)"

# ─── preflight ──────────────────────────────────────────────────────────────

# Every prerequisite is checked before any of them is allowed to stop the build,
# so a host that is missing three things learns all three from one run instead
# of three. Nothing here installs, configures, or reads a credential -- each
# check answers "can this host do the thing", and the build is what uses it.
preflight() {
  local failures=()
  local runtime=''

  log "preflight: build prerequisites"

  # Build tooling.
  command -v uv >/dev/null 2>&1 \
    || failures+=("uv is not installed — see https://docs.astral.sh/uv/")
  command -v npm >/dev/null 2>&1 \
    || failures+=("npm is not installed — needed to build the UI and the MCP app")
  command -v git >/dev/null 2>&1 \
    || failures+=("git is not installed")

  # A container runtime, to build and export the sandbox image. Checked by
  # asking the daemon, not by finding the client: a `docker` on PATH with no
  # daemon behind it is the common broken case, and it fails much later.
  for candidate in docker podman; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" info >/dev/null 2>&1; then
        runtime="$candidate"
        break
      fi
      warn "$candidate is installed but its daemon is not responding"
    fi
  done
  if [[ -z "$runtime" ]]; then
    failures+=("no working container runtime — the sandbox image is built with \
docker or podman on this host, so the recipient needs neither. Start Docker \
Desktop, or install podman.")
  fi

  # The corpus. Either an unpacked tree or a token that can fetch one.
  if [[ -n "$PAYLOAD_DIR" ]]; then
    [[ -d "$PAYLOAD_DIR" ]] \
      || failures+=("--payload directory does not exist: $PAYLOAD_DIR")
    [[ -d "$PAYLOAD_DIR/molten-salt-papers" && -d "$PAYLOAD_DIR/mstdb" ]] \
      || failures+=("--payload does not look like a vista-data tree: \
$PAYLOAD_DIR (expected molten-salt-papers/ and mstdb/ inside it)")
  elif [[ -z "${VISTA_DATA_TOKEN:-}" ]]; then
    failures+=("no corpus source — set VISTA_DATA_TOKEN (a code.ornl.gov token \
for v28/vista-data) or pass --payload with an already-unpacked copy")
  fi

  # amsc2 access, for the private amscrot-py that HPC submission needs. Asked
  # of git, using whatever credentials this host already has: an SSH key via
  # the url.insteadOf rewrite in README.md, or a stored HTTPS credential.
  # Nothing is read out of the credential store and nothing is written to .env
  # -- the answer needed here is only whether the fetch will work.
  if [[ "$WITHOUT_HPC" == true ]]; then
    warn "--without-hpc: amscrot-py will be omitted and HPC job submission \
will not work in the resulting package"
  elif [[ -z "$AMSC_GIT_URL" ]]; then
    failures+=("could not read the amscrot-py URL from \
mcp_servers/vista_mcp_server/pyproject.toml — has the dependency moved?")
  elif ! GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/true \
         git ls-remote "$AMSC_GIT_URL" HEAD >/dev/null 2>&1; then
    failures+=("no access to the amsc2 repository that provides amscrot-py:
    $AMSC_GIT_URL
  The package bundles this dependency so researchers never need amsc2
  credentials, which means this build host does. Configure git access to
  gitlab.com/amsc2 (see README.md), or pass --without-hpc to build a package
  with HPC job submission disabled.")
  fi

  # Network, per host rather than as one "is the internet up" question: a
  # restricted network that allows pypi but blocks huggingface is a real
  # configuration, and finding out mid-build costs an hour. HEAD only --
  # `pypi.org/simple/` is a 45 MB index, and GET-ing it as a reachability probe
  # times out on a working connection.
  local host_probe
  for host_probe in \
      "pypi.org|https://pypi.org/simple/|python dependencies" \
      "registry.npmjs.org|https://registry.npmjs.org/|the UI and MCP app builds" \
      "huggingface.co|https://huggingface.co/api/models/microsoft/harrier-oss-v1-270m|the embedding weights"; do
    local probe_host="${host_probe%%|*}"
    local probe_rest="${host_probe#*|}"
    local probe_url="${probe_rest%%|*}"
    local probe_why="${probe_rest##*|}"
    if ! curl -fsS -m 15 --head -o /dev/null "$probe_url" 2>/dev/null; then
      failures+=("cannot reach $probe_host — needed for $probe_why")
    fi
  done
  if [[ -z "$PAYLOAD_DIR" ]] \
     && ! curl -fsS -m 15 --head -o /dev/null https://code.ornl.gov 2>/dev/null; then
    failures+=("cannot reach code.ornl.gov — needed to fetch the corpus with \
VISTA_DATA_TOKEN; pass --payload to use a local copy instead")
  fi

  # The requested archiver.
  case "$ARCHIVE_FORMAT" in
    zstd)
      command -v zstd >/dev/null 2>&1 \
        || failures+=("zstd is not installed but --archive-format zstd was \
requested — gz needs no extra tool and is the default for that reason") ;;
    gz)
      command -v gzip >/dev/null 2>&1 \
        || failures+=("gzip is not installed") ;;
  esac

  if (( ${#failures[@]} > 0 )); then
    echo >&2
    echo "error: ${#failures[@]} unmet prerequisite(s); nothing was built." >&2
    local failure
    for failure in "${failures[@]}"; do
      echo "  - $failure" >&2
    done
    return 1
  fi

  echo "container runtime : ${runtime}"
  echo "corpus source     : ${PAYLOAD_DIR:-VISTA_DATA_TOKEN (code.ornl.gov)}"
  if [[ "$WITHOUT_HPC" == true ]]; then
    echo "amscrot-py        : omitted (--without-hpc)"
  else
    echo "amscrot-py        : reachable"
  fi
  echo "archive format    : ${ARCHIVE_FORMAT}"
  echo "package           : ${PACKAGE_NAME}"
  return 0
}

CONTAINER_RUNTIME=''

preflight || exit 1

if [[ "$CHECK_ONLY" == true ]]; then
  log "preflight passed; --check requested, so nothing was built"
  exit 0
fi

die "not implemented past the preflight yet"
