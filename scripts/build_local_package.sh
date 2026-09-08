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
#   --without-citations  Build the vector store without citation metadata
#                         (titles, authors, DOIs), and record that
#   --skip-smoke-test    Skip the post-build unpack-and-run verification
#   --keep-staging       Leave the staging tree in place for inspection
#   -h, --help           Show this help

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ─── configuration ──────────────────────────────────────────────────────────

# Read the repo-root .env, like build.sh and launch.sh do. The preflight below
# tells the user their credentials normally come from that file, so this script
# has to actually read it.
#
# Deliberately does NOT override a variable already set in the environment,
# matching `backend/src/vista_backend/config.py`, whose `load_dotenv` defaults
# to `override=False`. So `VISTA_DATA_TOKEN=... ./build_local_package.sh` wins
# over the file, and the preflight agrees with what the build's own Python
# steps will resolve. (`build.sh` and `launch.sh` use `set -o allexport` and
# let the file win instead; not changed here, but worth knowing they differ.)
load_env_file() {
  local file="$1" line key value
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == '#'* ]] && continue
    [[ "$line" == "export "* ]] && line="${line#export }"
    key="${line%%=*}"
    [[ "$key" == "$line" ]] && continue
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ -n "${!key:-}" ]] && continue
    value="${line#*=}"
    if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "$key=$value"
  done < "$file"
}

load_env_file "$REPO_ROOT/.env"

# ─── house idiom (matches scripts/ci-local.sh) ──────────────────────────────

usage() {
  sed -n '2,31p' "$0" | sed -E 's/^# ?//'
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
WITHOUT_CITATIONS=false
SKIP_SMOKE_TEST=false
KEEP_STAGING=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --check) CHECK_ONLY=true ;;
    --without-hpc) WITHOUT_HPC=true ;;
    --without-citations) WITHOUT_CITATIONS=true ;;
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
        CONTAINER_RUNTIME="$candidate"
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

  # An inference credential, for the citation metadata the vector store
  # carries. Building the store calls an LLM once per paper to extract title,
  # authors, journal, year, and DOI; without a credential that step is skipped
  # and the shipped corpus retrieves passages that cite nothing.
  #
  # The recipient never needs this -- the citations are baked into the store
  # they receive -- which is exactly why it has to be checked here.
  #
  # Mirrors the decision in `backend/src/vista_backend/utils/indexer.py`
  # (`has_llm_credentials`); if that resolution order changes, this follows.
  if [[ "$WITHOUT_CITATIONS" == true ]]; then
    warn "--without-citations: the vector store will have no titles, authors, \
or DOIs, and retrieval results will cite nothing"
  elif [[ -z "${OPENAI_API_KEY:-}" && -z "${AZURE_OPENAI_API_KEY:-}" ]]; then
    failures+=("no inference credential for citation extraction — set \
OPENAI_API_KEY (with OPENAI_BASE_URL and VISTA_BACKEND_MODEL) or the \
AZURE_OPENAI_* trio, normally through the repo-root .env. Building the vector \
store calls the model once per paper for title/authors/DOI; without it the \
shipped corpus returns passages that cite nothing. Pass \
--without-citations to build that way deliberately.")
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
      "huggingface.co|https://huggingface.co/api/models/microsoft/harrier-oss-v1-270m|the embedding weights" \
      "nodejs.org|https://nodejs.org/dist/|the bundled Node runtime"; do
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
  if [[ "$WITHOUT_CITATIONS" == true ]]; then
    echo "citations         : omitted (--without-citations)"
  else
    echo "citations         : credential present"
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

# ─── layout ─────────────────────────────────────────────────────────────────

# The package tree. Paths inside it are the contract the launcher relies on, so
# they are named once here.
STAGING="$OUTPUT_DIR/$PACKAGE_NAME"
STAGING_PYTHON="$STAGING/python"
STAGING_BIN="$STAGING/bin"
STAGING_APP="$STAGING/app"
STAGING_PAYLOAD="$STAGING/payload"

# uv resolves TLS against rustls' bundled roots, so a host behind an inspecting
# proxy -- corporate MITM, a zero-trust gateway -- fails every download with
# `invalid peer certificate: UnknownIssuer` while curl and git succeed. Asking
# uv to use the platform trust store instead costs nothing on a normal host.
# (`UV_NATIVE_TLS` is the older name for this and is deprecated.)
export UV_SYSTEM_CERTS=1
# Keep every interpreter this build downloads inside the package.
export UV_PYTHON_INSTALL_DIR="$STAGING_PYTHON"

# The interpreter every environment is built against. Read from the projects
# rather than pinned here, so a bump to `requires-python` cannot leave the
# package on an interpreter no longer supported.
PYTHON_REQUIREMENT="$(
  sed -nE 's/^requires-python = "~=([0-9]+\.[0-9]+)\..*"/\1/p' \
    "$REPO_ROOT/backend/pyproject.toml" | head -1
)"
[[ -n "$PYTHON_REQUIREMENT" ]] \
  || die "could not read requires-python from backend/pyproject.toml"

# The Node runtime the standalone UI server needs.
#
# Bundled for the same reason the interpreter is: the artifact "SHALL NOT
# require ... a language runtime" on the researcher's machine, and Next's
# standalone output is a `server.js`, not an executable. Pinned rather than
# taken from the build host so two builds of the same commit agree; override
# with VISTA_NODE_VERSION when moving to a new major.
NODE_VERSION="${VISTA_NODE_VERSION:-24.20.0}"
case "$TARGET_OS-$TARGET_ARCH" in
  macos-arm64)  NODE_PLATFORM=darwin-arm64 ;;
  macos-x86_64) NODE_PLATFORM=darwin-x64 ;;
  linux-aarch64|linux-arm64) NODE_PLATFORM=linux-arm64 ;;
  linux-x86_64) NODE_PLATFORM=linux-x64 ;;
  *) die "no Node build known for $TARGET_OS-$TARGET_ARCH" ;;
esac

# Every project that gets its own environment inside the package, as
# <source path>|<sync flags>.
PROJECTS=(
  "backend|"
  "mcp_servers/vista_mcp_server|--extra hpc"
  "mcp_servers/dev_mcp_server|"
)

prepare_staging() {
  log "staging: $STAGING"
  rm -rf "$STAGING"
  mkdir -p "$STAGING_PYTHON" "$STAGING_BIN" "$STAGING_APP" "$STAGING_PAYLOAD"
}

# ─── bundled runtime ────────────────────────────────────────────────────────

bundle_runtime() {
  log "bundling the python interpreter and uv"

  uv python install "$PYTHON_REQUIREMENT" >/dev/null
  # The install leaves a `cpython-<minor>-<platform>` symlink beside the real
  # `cpython-<patch>-<platform>` directory, pointing at it by absolute path --
  # which dangles the moment the package is unpacked somewhere else. The real
  # directory is what everything references, so the alias is dropped.
  local alias
  while IFS= read -r alias; do
    [[ -L "$alias" ]] && rm -f "$alias"
  done < <(find "$STAGING_PYTHON" -maxdepth 1 -type l)
  rm -rf "$STAGING_PYTHON/.temp" "$STAGING_PYTHON/.lock"

  BUNDLED_PYTHON_DIR="$(
    find "$STAGING_PYTHON" -maxdepth 1 -type d -name 'cpython-*' | head -1
  )"
  [[ -n "$BUNDLED_PYTHON_DIR" ]] \
    || die "uv python install left no interpreter in $STAGING_PYTHON"
  BUNDLED_PYTHON="$BUNDLED_PYTHON_DIR/bin/python$PYTHON_REQUIREMENT"
  [[ -x "$BUNDLED_PYTHON" ]] || die "no interpreter at $BUNDLED_PYTHON"

  # uv is a runtime dependency, not just a build tool: the backend spawns the
  # sandbox MCP server with `uv run dev-mcp-server` on every agent session.
  local uv_binary
  uv_binary="$(command -v uv)"
  cp "$uv_binary" "$STAGING_BIN/uv"
  chmod +x "$STAGING_BIN/uv"

  echo "interpreter : $(basename "$BUNDLED_PYTHON_DIR")"
  echo "uv          : $("$STAGING_BIN/uv" --version)"
}

# ─── sources ────────────────────────────────────────────────────────────────

stage_sources() {
  log "staging application sources"

  local project source
  for project in "${PROJECTS[@]}"; do
    source="${project%%|*}"
    mkdir -p "$STAGING_APP/$(dirname "$source")"
    # `.venv` is excluded rather than copied: the package gets environments
    # built against its own interpreter below. `mcp-apps` is the MCP app's npm
    # project -- 137 MB of build-time dependencies whose only output is the
    # single self-contained HTML file already inside `src/`.
    rsync -a \
      --exclude '.venv/' \
      --exclude '__pycache__/' \
      --exclude 'mcp-apps/' \
      --exclude '.pytest_cache/' \
      --exclude 'tests/' \
      "$REPO_ROOT/$source/" "$STAGING_APP/$source/"
  done

  # `build_rag.py` sits at the repo root and is imported by the indexer, which
  # locates it by walking up from the backend package -- so it has to keep the
  # same position relative to `backend/`.
  cp "$REPO_ROOT/build_rag.py" "$STAGING_APP/build_rag.py"

  # `submit_job_mcp.py` iterates this directory at import time, so the MCP
  # server does not start without it.
  rsync -a --exclude '__pycache__/' "$REPO_ROOT/hpc_jobs/" "$STAGING_APP/hpc_jobs/"

  echo "staged: $(du -sh "$STAGING_APP" | cut -f1)"
}

# ─── environments ───────────────────────────────────────────────────────────

# Make one environment relocatable.
#
# `uv venv --relocatable` gets the console scripts right -- they become `sh`
# wrappers that resolve the interpreter next to themselves -- but three things
# it does not touch still carry the build host's absolute paths:
#
#   * `bin/python` is an absolute symlink to the interpreter;
#   * `pyvenv.cfg`'s `home` is an absolute path;
#   * the project itself is installed *editable*, as a `.pth` file holding the
#     build-time source directory, so after relocation the interpreter and its
#     dependencies import but the application does not.
#
# The third is fixed at install time with `--no-editable`; the first two here.
relocate_environment() {
  local venv="$1"
  local python_dir_name
  python_dir_name="$(basename "$BUNDLED_PYTHON_DIR")"

  # Depth from <venv>/bin back to the package root, so the link survives
  # wherever the package is unpacked.
  local up_to_root
  up_to_root="$(
    python3 -c '
import os, sys
print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$STAGING" "$venv/bin"
  )"
  ln -sfn "$up_to_root/python/$python_dir_name/bin/python$PYTHON_REQUIREMENT" \
    "$venv/bin/python"
  ln -sfn python "$venv/bin/python3"
  ln -sfn python "$venv/bin/python$PYTHON_REQUIREMENT"

  local home_relative
  home_relative="$(
    python3 -c '
import os, sys
print(os.path.relpath(sys.argv[1], sys.argv[2]))' \
      "$BUNDLED_PYTHON_DIR/bin" "$venv"
  )"
  python3 - "$venv/pyvenv.cfg" "$home_relative" <<'PYFIX'
import pathlib, re, sys

config = pathlib.Path(sys.argv[1])
config.write_text(
    re.sub(r"^home = .*$", f"home = {sys.argv[2]}", config.read_text(), flags=re.M)
)
PYFIX
}

create_environments() {
  log "creating relocatable environments"

  local project source flags venv
  for project in "${PROJECTS[@]}"; do
    source="${project%%|*}"
    flags="${project#*|}"
    venv="$STAGING_APP/$source/.venv"

    echo "  $source"
    local skip=()
    read -r -a skip <<< "$(cuda_packages_to_skip "$REPO_ROOT/$source/uv.lock")"
    (
      cd "$STAGING_APP/$source"
      "$STAGING_BIN/uv" venv --relocatable --python "$BUNDLED_PYTHON" .venv \
        >/dev/null
      # --no-editable so the project is copied into site-packages instead of
      # pointed at by an absolute path. Without it the package unpacks to a
      # working interpreter that cannot import the application.
      UV_PROJECT_ENVIRONMENT="$venv" \
        "$STAGING_BIN/uv" sync --frozen --no-editable $flags \
        ${skip[@]+"${skip[@]}"} >/dev/null
    )
    install_cpu_torch "$venv" "${#skip[@]}"
    relocate_environment "$venv"
  done

  echo "environments: $(du -sh "$STAGING_APP" | cut -f1) total staged"
}

# ─── CPU-only torch (Linux) ─────────────────────────────────────────────────

# On Linux the locked `torch` pulls a CUDA runtime -- 37 NVIDIA distributions
# and roughly 2.5 GB -- that a laptop cannot use.
#
# They are skipped at install time rather than installed and then removed: the
# removal would still cost the download, on every Linux build. `uv.lock` is
# never rewritten, so the committed lock stays the one CI and developers
# resolve against and this remains a property of the package alone.
#
# macOS wheels have no CUDA variant, so both halves are no-ops there.
cuda_packages_to_skip() {
  local lock="$1"
  [[ "$TARGET_OS" == linux ]] || return 0
  [[ -f "$lock" ]] || return 0
  grep -c 'name = "torch"' "$lock" >/dev/null 2>&1 || return 0

  local name
  while IFS= read -r name; do
    printf -- '--no-install-package %s ' "$name"
  done < <(
    sed -nE 's/^name = "(nvidia-[a-z0-9-]+|torch|triton)"$/\1/p' "$lock" | sort -u
  )
}

install_cpu_torch() {
  local venv="$1" skipped="$2"
  [[ "$TARGET_OS" == linux ]] || return 0
  (( skipped > 0 )) || return 0

  log "installing the CPU build of torch"
  "$STAGING_BIN/uv" pip install --python "$venv/bin/python" \
    --torch-backend=cpu torch >/dev/null
}

# ─── Node runtime and the UI ────────────────────────────────────────────────

bundle_node() {
  log "bundling the Node runtime (v$NODE_VERSION, $NODE_PLATFORM)"

  local archive="node-v${NODE_VERSION}-${NODE_PLATFORM}.tar.xz"
  local url="https://nodejs.org/dist/v${NODE_VERSION}/${archive}"
  local tmp
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/$archive" "$url" \
    || die "could not download the Node runtime from $url"
  # Official tarballs unpack to node-v<version>-<platform>/; strip that so the
  # layout inside the package does not carry a version in its path.
  mkdir -p "$STAGING/node"
  tar -xJf "$tmp/$archive" -C "$STAGING/node" --strip-components=1
  rm -rf "$tmp"

  # npm and npx are build-time tools; the package only ever runs `node
  # server.js`. Dropping them saves a little over 10 MB and removes the only
  # thing in the package that could try to install something at runtime.
  rm -rf "$STAGING/node/lib/node_modules/npm" \
         "$STAGING/node/bin/npm" "$STAGING/node/bin/npx" \
         "$STAGING/node/include"

  [[ -x "$STAGING/node/bin/node" ]] || die "no node binary after unpacking"
  echo "node        : $("$STAGING/node/bin/node" --version)"
}

stage_ui() {
  log "building and staging the UI"

  (
    cd "$REPO_ROOT/ui"
    [[ -d node_modules ]] || npm ci --prefer-offline >/dev/null
    npm run build >/dev/null
  )

  local out="$REPO_ROOT/ui/.next/standalone"
  [[ -d "$out" ]] \
    || die "no standalone output at $out — is output: 'standalone' still set in ui/next.config.mjs?"

  rsync -a "$out/" "$STAGING_APP/ui/"
  # Next deliberately leaves these out of the standalone tree, on the
  # assumption that a CDN serves them. There is no CDN here, and `server.js`
  # serves them once they are in place.
  mkdir -p "$STAGING_APP/ui/.next"
  rsync -a "$REPO_ROOT/ui/.next/static/" "$STAGING_APP/ui/.next/static/"
  rsync -a "$REPO_ROOT/ui/public/" "$STAGING_APP/ui/public/"

  [[ -f "$STAGING_APP/ui/server.js" ]] || die "staged UI has no server.js"
  echo "ui          : $(du -sh "$STAGING_APP/ui" | cut -f1)"
}

# ─── sandbox image ──────────────────────────────────────────────────────────

# The agent's `run_bash` runs inside a microVM created by the bundled `msb`,
# from an OCI image. Building that image needs a container runtime; *loading* it
# does not, so the image is built and exported here and the launcher imports the
# archive with `msb load` on first run. That is the whole reason a researcher
# needs neither Docker nor podman.
export_sandbox_image() {
  log "building and exporting the sandbox image"

  local dockerfile="$REPO_ROOT/mcp_servers/dev_mcp_server/src/dev_mcp_server/docker/Dockerfile"
  [[ -f "$dockerfile" ]] || die "sandbox Dockerfile not found at $dockerfile"

  "$CONTAINER_RUNTIME" build -t "$SANDBOX_IMAGE" -f "$dockerfile" \
    "$(dirname "$dockerfile")" >/dev/null
  "$CONTAINER_RUNTIME" save -o "$STAGING_PAYLOAD/sandbox-image.tar" "$SANDBOX_IMAGE"

  echo "image       : $SANDBOX_IMAGE"
  echo "archive     : $(du -sh "$STAGING_PAYLOAD/sandbox-image.tar" | cut -f1)"
}

SANDBOX_IMAGE="vista-sandbox:latest"
BUNDLED_PYTHON=''
BUNDLED_PYTHON_DIR=''

prepare_staging
bundle_runtime
bundle_node
stage_sources
stage_ui
create_environments
export_sandbox_image

log "staged $PACKAGE_NAME"
echo "$STAGING"
echo
echo "note: payload, archive, manifest, and smoke test are not implemented yet"

