#!/usr/bin/env bash
# Build a self-contained, relocatable VISTA package for one platform.
#
# The output is an archive a researcher unpacks and runs with `./vista`: no
# Docker, no HuggingFace token, no GitLab access, no `.env`. Everything the
# running system needs -- interpreters, virtual environments, the standalone
# UI, the sandbox image, the AI-safety corpus and its vector store,
# the embedding weights -- is inside it. The molten-salt corpus and MSTDB are
# packed only with --science-projects.
#
# This script is the opposite side of that bargain: the build host needs the
# credentials and tooling so the recipient does not.
#
# Usage:
#   ./scripts/build_local_package.sh                    # build for this platform
#   ./scripts/build_local_package.sh --check            # preflight only, no build
#   ./scripts/build_local_package.sh --payload DIR      # use an unpacked vista-data tree
#   ./scripts/build_local_package.sh --science-projects # also pack molten-salt + MSTDB
#   ./scripts/build_local_package.sh --output-dir DIR   # where the archive lands
#
# Options:
#   --check              Run the preflight and exit; builds nothing
#   --payload DIR        Unpacked vista-data tree to use instead of fetching it
#                         with VISTA_DATA_TOKEN. Only the folders the build
#                         needs are copied from it
#   --output-dir DIR     Archive destination (default: dist/)
#   --archive-format FMT gz (default), zstd, zip (Windows only, and its
#                         default), or none to leave the tree unpacked
#   --without-citations  Build the vector store without citation metadata
#                         (titles, authors, DOIs), and record that
#   --vector-store DIR   Optional. Reuse an already-built AI-safety vector
#                         store instead of indexing the corpus again. Only for
#                         skipping re-embedding; omitting it is always correct
#   --science-projects   Also require and pack the molten-salt corpus, its
#                         vector store, MSTDB and the forge-tune CSV, so the
#                         package seeds the molten-salt and alloy-design
#                         projects. Also enabled by
#                         VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true
#   --science-projects-vector-store DIR
#                         Optional, and only with --science-projects. Reuse an
#                         already-built molten-salt vector store instead of
#                         indexing that corpus again
#   --sandbox-image TAR  Use an already-exported sandbox image archive instead
#                         of building one, so no container runtime is needed.
#                         Its architecture must match the target
#   --skip-smoke-test    Skip the post-build unpack-and-run verification
#   --keep-staging       Leave the staging tree in place for inspection
#   -h, --help           Show this help
#
# On Windows, run it from Git Bash; it builds a Windows x64 package whose
# launcher is PowerShell, so the recipient needs no bash.

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
    # A .env written on Windows usually has CRLF endings.
    line="${line%$'\r'}"
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
  # Printed by walking the header block rather than by line number: the range
  # this used to hardcode had already fallen four lines behind the block it
  # printed, silently dropping the last two options from `--help`.
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
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

# Copy the contents of one directory into another, skipping the given
# rsync-style exclusions: `name/` matches that directory at any depth, and a
# leading slash anchors it to the top of the copy.
#
# rsync where there is one. Git Bash has none, so there the same copy is a tar
# pipe, with each exclusion translated to GNU tar's form of it -- anchored ones
# become `./name`, which only the top-level entry can match.
copy_tree() {
  local src="$1" dst="$2" pattern
  shift 2
  mkdir -p "$dst"
  if command -v rsync >/dev/null 2>&1; then
    local excludes=()
    for pattern in "$@"; do excludes+=(--exclude "$pattern"); done
    rsync -a ${excludes[@]+"${excludes[@]}"} "$src/" "$dst/"
  else
    local excludes=()
    for pattern in "$@"; do
      pattern="${pattern%/}"
      [[ "$pattern" == /* ]] && pattern=".$pattern"
      excludes+=("--exclude=$pattern")
    done
    tar -C "$src" ${excludes[@]+"${excludes[@]}"} -cf - . | tar -C "$dst" -xf -
  fi
}

# ─── defaults ───────────────────────────────────────────────────────────────

CHECK_ONLY=false
PAYLOAD_DIR=''
OUTPUT_DIR="$REPO_ROOT/dist"
ARCHIVE_FORMAT=''  # resolved per platform below
WITHOUT_CITATIONS=false
REUSE_STORE=''
SCIENCE_PROJECTS=false
SCIENCE_STORE=''
SANDBOX_IMAGE_TAR=''
SKIP_SMOKE_TEST=false
KEEP_STAGING=false

# The backend setting that seeds the science projects also says whether to pack
# them, so one switch in the repo-root .env drives both.
case "$(printf '%s' "${VISTA_BACKEND_SEED_SCIENCE_PROJECTS:-}" | tr '[:upper:]' '[:lower:]')" in
  true|1|yes|on) SCIENCE_PROJECTS=true ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --check) CHECK_ONLY=true ;;
    --without-citations) WITHOUT_CITATIONS=true ;;
    --vector-store)
      [[ $# -ge 2 ]] || die "--vector-store needs a directory"
      REUSE_STORE="$2"; shift ;;
    --science-projects) SCIENCE_PROJECTS=true ;;
    --science-projects-vector-store)
      [[ $# -ge 2 ]] || die "--science-projects-vector-store needs a directory"
      SCIENCE_STORE="$2"; shift ;;
    --sandbox-image)
      [[ $# -ge 2 ]] || die "--sandbox-image needs a tar archive"
      SANDBOX_IMAGE_TAR="$2"; shift ;;
    --skip-smoke-test) SKIP_SMOKE_TEST=true ;;
    --keep-staging) KEEP_STAGING=true ;;
    --payload)
      [[ $# -ge 2 ]] || die "--payload needs a directory"
      PAYLOAD_DIR="$2"; shift ;;
    --output-dir)
      [[ $# -ge 2 ]] || die "--output-dir needs a directory"
      OUTPUT_DIR="$2"; shift ;;
    --archive-format)
      [[ $# -ge 2 ]] || die "--archive-format needs gz, zstd, zip, or none"
      ARCHIVE_FORMAT="$2"; shift ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

[[ -z "$SCIENCE_STORE" || "$SCIENCE_PROJECTS" == true ]] \
  || die "--science-projects-vector-store only applies with --science-projects"

case "$ARCHIVE_FORMAT" in
  ''|gz|zstd|zip|none) ;;
  *) die "unknown archive format: $ARCHIVE_FORMAT (want gz, zstd, zip, or none)" ;;
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

# Both of these are overridable so a build can run against a tree with no
# `.git`, such as one extracted with `git archive`. Left to query
# git, the manifest's commit field came out as an empty string -- the build
# still succeeded and the artifact simply lost its traceability.
COMMIT="${VISTA_COMMIT:-}"
if [[ -z "$COMMIT" ]]; then
  COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
fi

case "$(uname -s)" in
  Darwin) TARGET_OS=macos ;;
  Linux)  TARGET_OS=linux ;;
  MINGW*|MSYS*) TARGET_OS=windows ;;
  *) die "unsupported build platform: $(uname -s) (want Darwin, Linux, or Git Bash on Windows)" ;;
esac
TARGET_ARCH="$(uname -m)"

# The package's folder and archive name, e.g. vista-0.1.0-win-x86. Kept short
# on purpose: the folder name sits in front of every path in the package, and
# on Windows every one of those counts against the 260-character limit --
# twice over when Explorer's Extract All makes a folder named after the zip.
# So the name carries the release version and the platform only; the full
# build identity (commit, dirty tree) is in VERSION and manifest.json, and the
# manifest keeps the full OS and architecture names the launchers check.
case "$TARGET_OS" in
  macos)   PACKAGE_OS=mac ;;
  windows) PACKAGE_OS=win ;;
  *)       PACKAGE_OS="$TARGET_OS" ;;
esac
case "$TARGET_ARCH" in
  x86_64|amd64)  PACKAGE_ARCH=x86 ;;
  aarch64|arm64) PACKAGE_ARCH=arm64 ;;
  *)             PACKAGE_ARCH="$TARGET_ARCH" ;;
esac
PACKAGE_NAME="vista-${VERSION%%+*}-${PACKAGE_OS}-${PACKAGE_ARCH}"

# Where Windows lays things out differently. Environments keep their
# interpreter in `Scripts\` rather than `bin/`, and every executable carries
# `.exe`. Git Bash hands POSIX paths to native programs as Windows paths, both
# as arguments and in the environment, so the rest of this script can go on
# spelling them the POSIX way.
EXE=''
VENV_PYTHON="bin/python"
if [[ "$TARGET_OS" == windows ]]; then
  EXE=.exe
  VENV_PYTHON="Scripts/python.exe"
  # bsdtar, which ships with Windows 10 and later, for zip archives. Named by
  # path because Git Bash's own GNU tar comes first on PATH and cannot write
  # a zip.
  WIN_TAR="$(cygpath -u "$SYSTEMROOT")/System32/tar.exe"
  # Paths given as C:\... would otherwise read to GNU tar as a remote host
  # (`C:`) and to this script as relative.
  for var in PAYLOAD_DIR OUTPUT_DIR REUSE_STORE SCIENCE_STORE SANDBOX_IMAGE_TAR; do
    [[ -n "${!var}" ]] && printf -v "$var" '%s' "$(cygpath -u "${!var}")"
  done
fi

# gzip is the default because the recipient has to extract before anything of
# ours runs, so "install a decompressor first" is an instruction with nowhere
# to go. On Windows that argument picks zip: it is the one format Explorer
# opens on every supported version.
if [[ -z "$ARCHIVE_FORMAT" ]]; then
  ARCHIVE_FORMAT=gz
  [[ "$TARGET_OS" == windows ]] && ARCHIVE_FORMAT=zip
fi
[[ "$ARCHIVE_FORMAT" != zip || "$TARGET_OS" == windows ]] \
  || die "--archive-format zip is only for Windows packages; use gz or zstd"

# The private dependency HPC submission needs, read from the file that declares
# it so the preflight cannot check a stale URL.
AMSC_GIT_URL="$(
  sed -nE 's/.*"amscrot-py @ git\+(https:\/\/[^"]+)".*/\1/p' \
    "$REPO_ROOT/mcp_servers/vista_mcp_server/pyproject.toml" | head -1
)"

# ─── a shipped image's architecture ─────────────────────────────────────────

# `uname -m` and an OCI image config disagree on names for the same processor,
# so the comparison is made in OCI's vocabulary rather than the kernel's.
oci_arch_for_target() {
  case "$TARGET_ARCH" in
    x86_64|amd64)  echo amd64 ;;
    arm64|aarch64) echo arm64 ;;
    *)             echo "$TARGET_ARCH" ;;
  esac
}

# Read straight out of the archive, with no container runtime involved -- the
# point of accepting one is that this host may have none.
#
# `docker save` and `podman save` both write a `manifest.json` naming the
# image's config blob, and that blob records the architecture. Checking it is
# what makes the flag safe to trust: an arm64 image inside an x86_64 package
# would `msb load` without complaint and fail only when a microVM starts, which
# no part of the build does -- so the build would hand over an artifact whose
# code execution, or whose file transfer, is broken while everything else about
# it verified clean.
image_archive_arch() {
  local archive="$1" config
  config="$(
    tar -xOf "$archive" manifest.json 2>/dev/null \
      | sed -nE 's/.*"Config":"?([^",]+)"?.*/\1/p' | head -1
  )"
  [[ -n "$config" ]] || return 1
  tar -xOf "$archive" "$config" 2>/dev/null \
    | sed -nE 's/.*"architecture":"([a-z0-9]+)".*/\1/p' | head -1
}

# Everything the preflight has to say about a supplied image archive, for each
# of the two flags that take one. Prints its complaint and returns non-zero;
# says nothing and returns 0 when the archive is fit to ship.
supplied_image_complaint() {
  local flag="$1" archive="$2" runs_when="$3" want_arch got_arch
  want_arch="$(oci_arch_for_target)"

  if [[ ! -f "$archive" ]]; then
    echo "$flag is not a file: $archive"
  elif ! got_arch="$(image_archive_arch "$archive")" || [[ -z "$got_arch" ]]; then
    echo "$flag does not look like a saved container image: \
$archive (no manifest.json naming a config blob). Produce one with \
\`docker save -o FILE IMAGE\`."
  elif [[ "$got_arch" != "$want_arch" ]]; then
    echo "$flag holds a $got_arch image; this package targets $want_arch:
    $archive
  Nothing later in the build would notice: the image loads on any \
architecture and is only executed $runs_when. Rebuild it for the target with \
\`docker buildx build --platform linux/$want_arch\`."
  else
    return 0
  fi
  return 1
}

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

  # The shipped image -- the agent's sandbox -- is either built here, which
  # needs a container runtime, or supplied as an already-exported archive, which
  # needs none -- for a build host with no container runtime of its own.
  local complaint=''
  if [[ -n "$SANDBOX_IMAGE_TAR" ]]; then
    complaint="$(supplied_image_complaint --sandbox-image "$SANDBOX_IMAGE_TAR" \
      "when an agent runs code")" || failures+=("$complaint")
  fi

  if [[ -z "$SANDBOX_IMAGE_TAR" ]]; then
    # Checked by asking the daemon, not by finding the client: a `docker` on
    # PATH with no daemon behind it is the common broken case, and it fails
    # much later.
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
      failures+=("no working container runtime — the sandbox image is built \
with docker or podman on this host, so the recipient needs neither. Start \
Docker Desktop, install podman, or pass --sandbox-image with an archive \
exported elsewhere.")
    fi
  fi

  # The corpus. Either an unpacked tree or a token that can fetch one.
  if [[ -n "$PAYLOAD_DIR" ]]; then
    [[ -d "$PAYLOAD_DIR" ]] \
      || failures+=("--payload directory does not exist: $PAYLOAD_DIR")
    [[ -d "$PAYLOAD_DIR/ai-safety" ]] \
      || failures+=("--payload does not look like a vista-data tree: \
$PAYLOAD_DIR (expected ai-safety/ inside it)")
    if [[ "$SCIENCE_PROJECTS" == true ]]; then
      [[ -d "$PAYLOAD_DIR/molten-salt-papers" && -d "$PAYLOAD_DIR/mstdb" ]] \
        || failures+=("--science-projects needs molten-salt-papers/ and mstdb/ \
in the --payload tree: $PAYLOAD_DIR")
    fi
  elif [[ -z "${VISTA_DATA_TOKEN:-}" ]]; then
    failures+=("no corpus source — set VISTA_DATA_TOKEN (a code.ornl.gov token \
for v28/vista-data) or pass --payload with an already-unpacked copy")
  fi

  # amsc2 access, for the private amscrot-py that HPC submission needs. Asked
  # of git, using whatever credentials this host already has: an SSH key via
  # the url.insteadOf rewrite in README.md, or a stored HTTPS credential.
  # Nothing is read out of the credential store and nothing is written to .env
  # -- the answer needed here is only whether the fetch will work.
  # GCM_INTERACTIVE covers Git Credential Manager, the Windows default, which
  # would otherwise answer a missing credential with a login window.
  if [[ -z "$AMSC_GIT_URL" ]]; then
    failures+=("could not read the amscrot-py URL from \
mcp_servers/vista_mcp_server/pyproject.toml — has the dependency moved?")
  elif ! GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/true GCM_INTERACTIVE=never \
         git ls-remote "$AMSC_GIT_URL" HEAD >/dev/null 2>&1; then
    failures+=("no access to the amsc2 repository that provides amscrot-py:
    $AMSC_GIT_URL
  The package bundles this dependency so researchers never need amsc2
  credentials, which means this build host does. Configure git access to
  gitlab.com/amsc2 (see README.md).")
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
  #
  # Only a corpus that is indexed here needs it: each reused store stands in for
  # one indexing run.
  local index_needed=false
  [[ -z "$REUSE_STORE" ]] && index_needed=true
  if [[ -n "$REUSE_STORE" && ! -f "$REUSE_STORE/chroma.sqlite3" ]]; then
    failures+=("--vector-store is not a Chroma store: $REUSE_STORE \
(expected chroma.sqlite3 inside it)")
  fi
  if [[ "$SCIENCE_PROJECTS" == true ]]; then
    [[ -z "$SCIENCE_STORE" ]] && index_needed=true
    if [[ -n "$SCIENCE_STORE" && ! -f "$SCIENCE_STORE/chroma.sqlite3" ]]; then
      failures+=("--science-projects-vector-store is not a Chroma store: \
$SCIENCE_STORE (expected chroma.sqlite3 inside it)")
    fi
  fi
  if [[ "$index_needed" == true ]]; then
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
  # The VISTA window: Electron's binary comes from GitHub releases on every
  # target that has a window, and on macOS it is re-signed ad hoc here.
  if [[ "$TARGET_OS" == macos || "$TARGET_OS" == linux || "$TARGET_OS" == windows ]]; then
    if ! curl -fsS -m 15 --head -o /dev/null https://github.com/electron/electron/releases 2>/dev/null; then
      failures+=("cannot reach github.com — needed to download Electron for the VISTA window")
    fi
  fi
  if [[ "$TARGET_OS" == macos ]]; then
    command -v codesign >/dev/null 2>&1 \
      || failures+=("codesign is not available — needed to re-sign the VISTA window")
    # Exactly one signing call, the window's. Any other would risk re-signing
    # `msb` and stripping the hypervisor entitlement the sandbox needs (R2).
    local signing_calls
    # grep -c prints 0 but exits 1 on no match, which set -e would turn into a
    # silent abort before the explanation below.
    signing_calls="$(grep -cE '^[[:space:]]*codesign[[:space:]].*--sign' "$REPO_ROOT/scripts/build_local_package.sh" || true)"
    if [[ "$signing_calls" != 1 ]]; then
      failures+=("build_local_package.sh has $signing_calls codesign --sign calls; only the \
VISTA window's may exist, so nothing else (msb above all) is ever re-signed")
    fi
  fi

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
    zip)
      [[ -x "$WIN_TAR" ]] \
        || failures+=("no tar.exe at $WIN_TAR — it ships with Windows 10 and \
later, and writes the zip") ;;
  esac

  # The launcher a Windows package ships. The bash one cannot be used there: a
  # researcher's machine has no bash.
  if [[ "$TARGET_OS" == windows ]]; then
    [[ -f "$REPO_ROOT/scripts/package_launcher.ps1" ]] \
      || failures+=("scripts/package_launcher.ps1 does not exist — a Windows \
package has no launcher without it")
  fi

  if (( ${#failures[@]} > 0 )); then
    echo >&2
    echo "error: ${#failures[@]} unmet prerequisite(s); nothing was built." >&2
    local failure
    for failure in "${failures[@]}"; do
      echo "  - $failure" >&2
    done
    return 1
  fi

  if [[ -n "$SANDBOX_IMAGE_TAR" ]]; then
    echo "sandbox image     : $SANDBOX_IMAGE_TAR ($(oci_arch_for_target), supplied)"
  fi
  if [[ -n "$runtime" ]]; then
    echo "container runtime : ${runtime}"
  fi
  echo "corpus source     : ${PAYLOAD_DIR:-VISTA_DATA_TOKEN (code.ornl.gov)}"
  echo "amscrot-py        : reachable"
  if [[ "$SCIENCE_PROJECTS" == true ]]; then
    echo "science projects  : included (molten-salt corpus, MSTDB, forge-tune CSV)"
  else
    echo "science projects  : not included"
  fi
  if [[ -n "$REUSE_STORE" ]]; then
    echo "ai-safety store   : reusing $REUSE_STORE (no indexing, no model calls)"
  fi
  if [[ "$SCIENCE_PROJECTS" == true && -n "$SCIENCE_STORE" ]]; then
    echo "molten-salt store : reusing $SCIENCE_STORE (no indexing, no model calls)"
  fi
  if [[ "$index_needed" == true ]]; then
    if [[ "$WITHOUT_CITATIONS" == true ]]; then
      echo "citations         : omitted (--without-citations)"
    else
      echo "citations         : credential present"
    fi
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
  windows-x86_64) NODE_PLATFORM=win-x64 ;;
  *) die "no Node build known for $TARGET_OS-$TARGET_ARCH" ;;
esac
# The Windows distribution is a zip that keeps node.exe at its top level; the
# others are tarballs with the binary under bin/.
NODE_BIN="$STAGING/node/bin/node"
[[ "$TARGET_OS" == windows ]] && NODE_BIN="$STAGING/node/node.exe"

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

  # --no-bin: install the interpreter into the package only. Without it uv also
  # puts a `python3.x` shim into the build user's ~/.local/bin, on their PATH.
  uv python install --no-bin "$PYTHON_REQUIREMENT" >/dev/null
  # The install leaves a `cpython-<minor>-<platform>` symlink beside the real
  # `cpython-<patch>-<platform>` directory, pointing at it by absolute path --
  # which dangles the moment the package is unpacked somewhere else. The real
  # directory is what everything references, so the alias is dropped. (On
  # Windows the alias is a junction, which Git Bash reports as a symlink and
  # `rm -f` removes without touching its target.)
  local alias
  while IFS= read -r alias; do
    [[ -L "$alias" ]] && rm -f "$alias"
  done < <(find "$STAGING_PYTHON" -maxdepth 1 -type l)
  rm -rf "$STAGING_PYTHON/.temp" "$STAGING_PYTHON/.lock"

  BUNDLED_PYTHON_DIR="$(
    find "$STAGING_PYTHON" -maxdepth 1 -type d -name 'cpython-*' -print -quit
  )"
  [[ -n "$BUNDLED_PYTHON_DIR" ]] \
    || die "uv python install left no interpreter in $STAGING_PYTHON"
  # A Windows interpreter keeps python.exe at the top of its install.
  BUNDLED_PYTHON="$BUNDLED_PYTHON_DIR/bin/python$PYTHON_REQUIREMENT"
  [[ "$TARGET_OS" == windows ]] && BUNDLED_PYTHON="$BUNDLED_PYTHON_DIR/python.exe"
  [[ -x "$BUNDLED_PYTHON" ]] || die "no interpreter at $BUNDLED_PYTHON"

  # uv is a runtime dependency, not just a build tool: the backend spawns the
  # sandbox MCP server with `uv run dev-mcp-server` on every agent session.
  local uv_binary
  uv_binary="$(command -v uv)$EXE"
  cp "$uv_binary" "$STAGING_BIN/uv$EXE"
  chmod +x "$STAGING_BIN/uv$EXE"

  echo "interpreter : $(basename "$BUNDLED_PYTHON_DIR")"
  echo "uv          : $("$STAGING_BIN/uv$EXE" --version)"
}

# ─── sources ────────────────────────────────────────────────────────────────

# Build the MCP app the display_file tool serves.
#
# Its single self-contained HTML file is a build artifact and is not committed,
# so a fresh checkout has nothing to stage -- `scripts/build.sh` produces it,
# and a package built without this step silently ships without it. Runs before
# the sources are staged, since the output lands inside the tree that gets
# copied.
build_mcp_app() {
  log "building the MCP app"
  (
    cd "$REPO_ROOT/mcp_servers/vista_mcp_server/mcp-apps"
    npm ci --prefer-offline >/dev/null
    npm run build >/dev/null
  )
  local emitted="$REPO_ROOT/mcp_servers/vista_mcp_server/src/vista_mcp_server/mcp-apps"
  [[ -f "$emitted/display-file.html" ]] \
    || die "the MCP app build emitted no display-file.html in $emitted"
  echo "mcp app     : $(du -sh "$emitted" | cut -f1)"
}

stage_sources() {
  log "staging application sources"

  local project source
  for project in "${PROJECTS[@]}"; do
    source="${project%%|*}"
    mkdir -p "$STAGING_APP/$(dirname "$source")"
    # `.venv` is excluded rather than copied: the package gets environments
    # built against its own interpreter below. `/mcp-apps/` is the MCP app's npm
    # project -- 137 MB of build-time dependencies whose only output is one
    # self-contained HTML file. The leading slash anchors that exclusion to the
    # top of this transfer: unanchored, it also matched the *output* directory
    # `src/vista_mcp_server/mcp-apps/`, and the app was silently left out of
    # every package.
    copy_tree "$REPO_ROOT/$source" "$STAGING_APP/$source" \
      '.venv/' '__pycache__/' '/mcp-apps/' '.pytest_cache/' 'tests/'
  done

  # `build_rag.py` sits at the repo root and is imported by the indexer, which
  # locates it by walking up from the backend package -- so it has to keep the
  # same position relative to `backend/`.
  cp "$REPO_ROOT/build_rag.py" "$STAGING_APP/build_rag.py"

  # `submit_job_mcp.py` iterates this directory at import time, so the MCP
  # server does not start without it.
  copy_tree "$REPO_ROOT/hpc_jobs" "$STAGING_APP/hpc_jobs" '__pycache__/'

  # The launcher lives at the package root, where a researcher will look for
  # it, and is the only executable they are asked to run. On Windows it is
  # PowerShell, with a `.cmd` beside it so it can be double-clicked or run
  # from cmd.
  if [[ "$TARGET_OS" == windows ]]; then
    cp "$REPO_ROOT/scripts/package_launcher.ps1" "$STAGING/vista.ps1"
    cp "$REPO_ROOT/scripts/package_launcher.cmd" "$STAGING/vista.cmd"
  else
    install -m 755 "$REPO_ROOT/scripts/package_launcher.sh" "$STAGING/vista"
  fi

  local staged_app="$STAGING_APP/mcp_servers/vista_mcp_server/src/vista_mcp_server/mcp-apps/display-file.html"
  [[ -f "$staged_app" ]] || die "the MCP app did not reach the package at $staged_app"

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
#
# Windows has neither of the first two to fix at build time. `Scripts\python.exe`
# is a copy of the venv launcher rather than a symlink, and uv's console-script
# trampolines find it beside themselves. `home` stays absolute: CPython resolves
# a relative `home` against the working directory rather than `pyvenv.cfg`, so
# the relative form written below would break it, and the build's own later
# steps need these environments working where they are. `package_launcher.ps1`
# points `home` at the unpacked interpreter before it starts anything.
relocate_environment() {
  local venv="$1"
  [[ "$TARGET_OS" == windows ]] && return 0
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
    re.sub(
        r"^home = .*$",
        f"home = {sys.argv[2]}",
        config.read_text(encoding="utf-8"),
        flags=re.M,
    ),
    encoding="utf-8",
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
      "$STAGING_BIN/uv$EXE" venv --relocatable --python "$BUNDLED_PYTHON" .venv \
        >/dev/null
      # --no-editable so the project is copied into site-packages instead of
      # pointed at by an absolute path. Without it the package unpacks to a
      # working interpreter that cannot import the application.
      UV_PROJECT_ENVIRONMENT="$venv" \
        "$STAGING_BIN/uv$EXE" sync --frozen --no-editable $flags \
        ${skip[@]+"${skip[@]}"} >/dev/null
    )
    install_cpu_torch "$venv" "${#skip[@]}"
    relocate_environment "$venv"
  done

  echo "environments: $(du -sh "$STAGING_APP" | cut -f1) total staged"

  # Run the bundled msb once, here, rather than discovering at the smoke test
  # that the build environment is older than it needs. This is the whole
  # sandbox in one binary, and it is the only bundled executable whose system
  # requirements are not the interpreter's -- microsandbox's wheel tag covers
  # its Python modules, not this.
  local msb
  msb="$(find "$STAGING_APP/mcp_servers/dev_mcp_server/.venv" \
    -path "*/microsandbox/_bundled/bin/msb$EXE" -print -quit 2>/dev/null)"
  if [[ -x "$msb" ]]; then
    local msb_error host_glibc=''
    # Guarded twice, and both guards are load-bearing. `ldd` does not exist on
    # macOS, and a standalone assignment whose command substitution fails is
    # fatal under `set -e` -- which is how this stage once exited silently,
    # with exit 127, immediately after reporting the staged size. The `|| true`
    # covers the same hazard on a Linux host whose ldd behaves unexpectedly.
    if [[ "$TARGET_OS" == linux ]]; then
      host_glibc="$(ldd --version 2>/dev/null \
        | sed -nE '1s/.*[[:space:]]([0-9]+\.[0-9]+)$/\1/p' || true)"
    fi
    if ! msb_error="$("$msb" --version 2>&1)"; then
      die "the bundled sandbox binary cannot run in this build environment:

    $msb_error

  Everything the package needs is present, but a smoke test here would fail
  and a recipient on this platform would too. On Linux this means the build
  environment's glibc is older than msb requires: ${host_glibc:-unknown} here.
  Build on a newer base."
    fi
    echo "sandbox bin : $($msb --version 2>/dev/null)"
  fi
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

  local suffix=tar.xz
  [[ "$TARGET_OS" == windows ]] && suffix=zip
  local archive="node-v${NODE_VERSION}-${NODE_PLATFORM}.${suffix}"
  local url="https://nodejs.org/dist/v${NODE_VERSION}/${archive}"
  local tmp
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/$archive" "$url" \
    || die "could not download the Node runtime from $url"
  # Official archives unpack to node-v<version>-<platform>/; strip that so the
  # layout inside the package does not carry a version in its path.
  mkdir -p "$STAGING/node"
  if [[ "$TARGET_OS" == windows ]]; then
    "$WIN_TAR" -xf "$tmp/$archive" -C "$STAGING/node" --strip-components=1
  else
    tar -xJf "$tmp/$archive" -C "$STAGING/node" --strip-components=1
  fi
  rm -rf "$tmp"

  # npm and npx are build-time tools; the package only ever runs `node
  # server.js`. Dropping them saves a little over 10 MB and removes the only
  # thing in the package that could try to install something at runtime. The
  # Windows zip keeps them, as .cmd and .ps1 shims, at its top level.
  rm -rf "$STAGING/node/lib/node_modules/npm" \
         "$STAGING/node/bin/npm" "$STAGING/node/bin/npx" \
         "$STAGING/node/include"
  if [[ "$TARGET_OS" == windows ]]; then
    rm -rf "$STAGING/node/node_modules/npm" \
           "$STAGING/node"/npm "$STAGING/node"/npm.{cmd,ps1} \
           "$STAGING/node"/npx "$STAGING/node"/npx.{cmd,ps1}
  fi

  [[ -x "$NODE_BIN" ]] || die "no node binary after unpacking"
  echo "node        : $("$NODE_BIN" --version)"
}

stage_ui() {
  log "building and staging the UI"

  (
    cd "$REPO_ROOT/ui"
    npm ci --prefer-offline >/dev/null
    npm run build >/dev/null
  )

  local out="$REPO_ROOT/ui/.next/standalone"
  [[ -d "$out" ]] \
    || die "no standalone output at $out — is output: 'standalone' still set in ui/next.config.mjs?"

  copy_tree "$out" "$STAGING_APP/ui"
  # Next deliberately leaves these out of the standalone tree, on the
  # assumption that a CDN serves them. There is no CDN here, and `server.js`
  # serves them once they are in place.
  copy_tree "$REPO_ROOT/ui/.next/static" "$STAGING_APP/ui/.next/static"
  copy_tree "$REPO_ROOT/ui/public" "$STAGING_APP/ui/public"

  [[ -f "$STAGING_APP/ui/server.js" ]] || die "staged UI has no server.js"
  echo "ui          : $(du -sh "$STAGING_APP/ui" | cut -f1)"
}

# ─── the VISTA window ───────────────────────────────────────────────────────

# The window `vista` opens once the services are up (electron/, design B1).
# The launcher finds it through the manifest's `window.exe` rather than a
# hard-coded path, which each target lays out differently. Every target has
# one: there is no browser mode, so a target without a window is not built.
WINDOW_EXE=''
ELECTRON_VERSION="$(sed -nE 's/.*"electron": "([^"]+)".*/\1/p' "$REPO_ROOT/electron/package.json")"

stage_window() {
  case "$TARGET_OS" in
    macos) stage_window_macos ;;
    linux) stage_window_linux ;;
    windows) stage_window_windows ;;
    *) die "no VISTA window for $TARGET_OS-$TARGET_ARCH" ;;
  esac
}

# electron-desktop-shell P1. Nothing is signed: an unsigned VISTA.exe meets
# SmartScreen, the same accepted risk as the unsigned msb.exe beside it.
stage_window_windows() {
  log "building the VISTA window (Electron $ELECTRON_VERSION)"
  local arch
  case "$TARGET_ARCH" in
    x86_64) arch=x64 ;;
    *) die "no Electron build known for windows-$TARGET_ARCH" ;;
  esac

  local out built
  out="$(mktemp -d)"
  (
    cd "$REPO_ROOT/electron"
    npm ci --prefer-offline >/dev/null
    npx --no install-electron
  )
  # The packager prints a Windows path; bash's own tools want its POSIX form.
  built="$(node "$REPO_ROOT/electron/scripts/package.js" \
    --platform win32 --arch "$arch" --out "$out" | tail -1 | tr -d '\r')"
  built="$(cygpath -u "$built")"
  [[ -d "$built" ]] || die "the window packager produced nothing at $built"

  local window="$STAGING_APP/window"
  rm -rf "$window"
  mv "$built" "$window"
  rm -rf "$out"

  WINDOW_EXE="app/window/VISTA.exe"
  [[ -f "$STAGING/$WINDOW_EXE" ]] || die "no window executable at $WINDOW_EXE"
  echo "window      : $(du -sh "$window" | cut -f1) (Electron $ELECTRON_VERSION)"
}

# linux-desktop-window D7. Nothing is signed on Linux. Next to the window go
# the two files that decide its sandbox: window-sandbox, which the launcher
# and the smoke test run for its arguments (D1), and the AppArmor profile it
# tells an Ubuntu researcher how to install (D2).
stage_window_linux() {
  log "building the VISTA window (Electron $ELECTRON_VERSION)"
  local arch
  case "$TARGET_ARCH" in
    aarch64|arm64) arch=arm64 ;;
    x86_64) arch=x64 ;;
    *) die "no Electron build known for linux-$TARGET_ARCH" ;;
  esac

  local out built
  out="$(mktemp -d)"
  (
    cd "$REPO_ROOT/electron"
    # The binary too, as on macOS. Without it node_modules/.bin/electron has
    # nothing to start, which breaks the dev window and the e2e tests in this
    # same folder; Electron 44 has no postinstall, so `npm ci` alone leaves it
    # out. The packager shares the download cache, so a native-architecture
    # build fetches nothing twice.
    npm ci --prefer-offline >/dev/null
    npx --no install-electron
  )
  built="$(node "$REPO_ROOT/electron/scripts/package.js" \
    --platform linux --arch "$arch" --out "$out" | tail -1)"
  [[ -d "$built" ]] || die "the window packager produced nothing at $built"

  local window="$STAGING_APP/window"
  rm -rf "$window"
  mv "$built" "$window"
  rm -rf "$out"
  # The packager's output directory is created 0700, and the move keeps that,
  # which hides the window from anyone but the unpacking user.
  chmod 755 "$window"
  install -m 755 "$REPO_ROOT/electron/linux/window-sandbox" "$window/window-sandbox"
  install -m 644 "$REPO_ROOT/electron/linux/vista-window.apparmor" "$window/vista-window.apparmor"

  WINDOW_EXE="app/window/VISTA"
  [[ -x "$STAGING/$WINDOW_EXE" ]] || die "no window executable at $WINDOW_EXE"
  echo "window      : $(du -sh "$window" | cut -f1) (Electron $ELECTRON_VERSION)"
}

stage_window_macos() {
  log "building the VISTA window (Electron $ELECTRON_VERSION)"
  local arch
  case "$TARGET_ARCH" in
    arm64) arch=arm64 ;;
    x86_64) arch=x64 ;;
    *) die "no Electron build known for macos-$TARGET_ARCH" ;;
  esac

  local out built
  out="$(mktemp -d)"
  (
    cd "$REPO_ROOT/electron"
    npm ci --prefer-offline >/dev/null
    npx --no install-electron
  )
  built="$(node "$REPO_ROOT/electron/scripts/package.js" \
    --platform darwin --arch "$arch" --out "$out" | tail -1)"
  [[ -d "$built" ]] || die "the window packager produced nothing at $built"

  # macOS is app-first: this bundle is the package's Finder/Dock entrypoint.
  # The diagnostic `vista` launcher and the large runtime remain its siblings.
  local app="$STAGING/VISTA.app"
  mv "$built" "$app"
  rm -rf "$out"

  # Renaming the app invalidates Electron's own ad-hoc signature, and an
  # invalid one is killed on launch. Re-signed ad hoc -- no Developer ID -- and
  # only this path: `msb` carries a hypervisor entitlement that re-signing
  # would strip (design R2), which is also why preflight refuses any other
  # `codesign` in this script.
  codesign --force --deep --sign - "$app"
  codesign --verify --deep --strict "$app" \
    || die "the VISTA window's signature does not verify after signing"

  WINDOW_EXE="VISTA.app/Contents/MacOS/VISTA"
  [[ -x "$STAGING/$WINDOW_EXE" ]] || die "no window executable at $WINDOW_EXE"
  echo "window      : $(du -sh "$app" | cut -f1) (Electron $ELECTRON_VERSION)"
}

# ─── corpus payload ─────────────────────────────────────────────────────────

# The vista-data files, the vector store built from them, and the embedding
# weights, all inside the package.
#
# `payload/` is installed into the state directory on first run rather than read
# in place, because the knowledge-base row records absolute paths and the corpus
# is a researcher's to add to. It is staged as folders, which the steps below
# read, and packed into `payload/payload.tar` just before archiving (see
# pack_payload). The tree under `payload/vista-data` deliberately
# mirrors the repository, so `db/seed.LocalRepoClient` resolves the same
# repo-relative paths the GitLab client would.
#
# Only the folders this build needs are copied: `ai-safety/` always, and with
# --science-projects the molten-salt corpus and MSTDB as well. A default package
# therefore carries no science data at all.
stage_payload() {
  log "assembling the corpus payload"

  local vista_data="$STAGING_PAYLOAD/vista-data"
  mkdir -p "$vista_data"

  local folders=(ai-safety)
  [[ "$SCIENCE_PROJECTS" == true ]] && folders+=(molten-salt-papers mstdb)

  local source_tree="$PAYLOAD_DIR" tmp=''
  if [[ -z "$PAYLOAD_DIR" ]]; then
    tmp="$(mktemp -d)"
    # The empty helper first clears any configured ones, so the token is what
    # answers: a stored credential for code.ornl.gov -- which Git Credential
    # Manager on Windows would otherwise offer first -- cannot stand in for it,
    # and the token is not stored anywhere.
    GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never git -c credential.helper= \
      -c "credential.helper=!f() { \
      echo username=oauth2; echo \"password=\$VISTA_DATA_TOKEN\"; }; f" \
      clone --depth 1 https://code.ornl.gov/v28/vista-data.git "$tmp/vista-data" \
      >/dev/null 2>&1 \
      || die "could not clone v28/vista-data — is VISTA_DATA_TOKEN still valid?"
    source_tree="$tmp/vista-data"
  fi

  local folder
  for folder in "${folders[@]}"; do
    [[ -d "$source_tree/$folder" ]] || die "payload has no $folder directory"
    copy_tree "$source_tree/$folder" "$vista_data/$folder" '.git/'
  done
  if [[ -n "$tmp" ]]; then
    rm -rf "$tmp"
  fi

  local pdf_count
  pdf_count="$(find "$vista_data/ai-safety" -name '*.pdf' | wc -l | tr -d ' ')"
  (( pdf_count > 0 )) || die "payload contains no AI-safety PDFs"
  echo "corpus      : ai-safety, $pdf_count PDFs, $(du -sh "$vista_data/ai-safety" | cut -f1)"

  if [[ "$SCIENCE_PROJECTS" == true ]]; then
    local science_pdfs
    science_pdfs="$(find "$vista_data/molten-salt-papers" -name '*.pdf' | wc -l | tr -d ' ')"
    (( science_pdfs > 0 )) || die "payload contains no molten-salt PDFs"

    # The CSV `hpc_jobs/forge-tune` reads. Staged here rather than left to
    # first-run seeding so the job works on a package built with a payload.
    local job_csv="$vista_data/mstdb/Molten_Salt_Thermophysical_Properties.csv"
    [[ -f "$job_csv" ]] || die "payload has no mstdb CSV for hpc_jobs/forge-tune"
    mkdir -p "$STAGING_APP/hpc_jobs/forge-tune"
    cp "$job_csv" "$STAGING_APP/hpc_jobs/forge-tune/"
    echo "corpus      : molten-salt-papers, $science_pdfs PDFs, plus MSTDB"
  fi

  # What the launcher installs, one tar member per line (see pack_payload).
  printf '%s\n' "${PAYLOAD_MEMBERS[@]}" > "$STAGING_PAYLOAD/parts.txt"
}

# Stage the embedding weights in HuggingFace cache layout, so retrieval loads
# them with the network switched off. Reuses the build host's cache when it
# already holds the model rather than re-downloading 500 MB.
stage_embedding_weights() {
  log "staging the embedding weights"

  local hf="$STAGING_PAYLOAD/huggingface"
  mkdir -p "$hf"
  local model
  model="$(
    sed -nE 's/^    rag_model: str = "([^"]+)"/\1/p' \
      "$REPO_ROOT/mcp_servers/vista_mcp_server/src/vista_mcp_server/config.py" | head -1
  )"
  [[ -n "$model" ]] || die "could not read rag_model from the MCP server config"
  local cache_name="models--${model//\//--}"

  local host_cache="$REPO_ROOT/data/huggingface/hub/$cache_name"
  if [[ -d "$host_cache" ]]; then
    copy_tree "$host_cache" "$hf/hub/$cache_name"
  else
    HF_HOME="$hf" HF_HUB_DISABLE_TELEMETRY=1 \
      "$STAGING_APP/mcp_servers/vista_mcp_server/.venv/$VENV_PYTHON" - "$model" <<'PYHF'
import sys
from huggingface_hub import snapshot_download

snapshot_download(sys.argv[1])
PYHF
  fi

  [[ -d "$hf/hub/$cache_name" ]] || die "no weights staged for $model"
  echo "weights     : $model, $(du -sh "$hf" | cut -f1)"
}

# Refuse a reused store that was built from a different corpus.
#
# The one real hazard of reuse: a store whose chunks cite papers the payload
# does not contain ships a knowledge base whose citations are dead links, and
# nothing downstream would notice -- the store is non-empty and every count
# looks healthy.
check_store_matches_corpus() {
  local kb="$1"
  VISTA_DATA_DIR="$STAGING_PAYLOAD" \
    "$STAGING_APP/backend/.venv/$VENV_PYTHON" - "$kb" <<'PYMATCH'
import sys
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

kb = Path(sys.argv[1])
client = chromadb.PersistentClient(
    path=str(kb / "rag_db"), settings=ChromaSettings(anonymized_telemetry=False)
)
sources = {
    (m or {}).get("source")
    for m in client.get_collection("text_chunks").get(include=["metadatas"])["metadatas"]
}
sources.discard(None)
present = {p.name for p in (kb / "pdfs").rglob("*.pdf")}
missing = sorted(s for s in sources if s not in present)
if missing:
    sys.exit(
        f"the reused vector store cites {len(missing)} document(s) that are not "
        f"in this payload, so its citations would be dead links. First few: "
        f"{missing[:3]}"
    )
print(f"  store covers {len(sources)} of {len(present)} payload documents")
PYMATCH
}

# Build the vector store for one corpus (`$1`, a vista-data folder name) from
# the payload's PDFs, or reuse the prebuilt one in `$2` when it is given.
#
# Shipped prebuilt because indexing is the one first-run step that cannot be
# made fast: it reads every paper, embeds ~4400 chunks, and calls a model once
# per paper for citation metadata. `db/seed._build_knowledge_base` returns early
# when a store is already present, so the researcher's first run finds a
# searchable corpus and does none of this.
build_vector_store() {
  local slug="$1" reuse_store="${2:-}"
  log "staging the $slug vector store"

  local kb="$STAGING_PAYLOAD/knowledge-bases/$slug"
  mkdir -p "$kb"
  copy_tree "$STAGING_PAYLOAD/vista-data/$slug" "$kb/pdfs"

  # Reusing a store skips the slowest step in the build -- reading every paper,
  # embedding ~4400 chunks, and calling a model once per paper for citation
  # metadata. Worth having because packaging changes need iterating on and the
  # corpus does not change between them; the consistency check below is what
  # keeps a stale store from being shipped against a different corpus.
  if [[ -n "$reuse_store" ]]; then
    log "reusing the $slug vector store from $reuse_store"
    copy_tree "$reuse_store" "$kb/rag_db"
    check_store_matches_corpus "$kb"
    VISTA_DATA_DIR="$STAGING_PAYLOAD" \
      "$STAGING_APP/backend/.venv/$VENV_PYTHON" - "$kb" <<'PYCHECK'
import sys
from pathlib import Path

from vista_backend.db.seed import _assert_knowledge_base_indexed

_assert_knowledge_base_indexed(Path(sys.argv[1]))
PYCHECK
    echo "store       : $(du -sh "$kb/rag_db" | cut -f1) (reused)"
    return 0
  fi

  log "indexing the $slug corpus (this is the slow part)"
  local citations=1
  [[ "$WITHOUT_CITATIONS" == true ]] && citations=0

  # Driven through the backend's own indexer -- the same call first-run seeding
  # makes -- so the store is built exactly as the application would build it.
  HF_HOME="$STAGING_PAYLOAD/huggingface" \
  HF_HUB_OFFLINE=1 \
  VISTA_BUILD_RAG_DIR="$STAGING_APP" \
  VISTA_DATA_DIR="$STAGING_PAYLOAD" \
    "$STAGING_APP/backend/.venv/$VENV_PYTHON" - "$kb" "$citations" <<'PYINDEX'
import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

kb = Path(sys.argv[1])
with_citations = sys.argv[2] == "1"

from vista_backend.agents.inference import citation_credentials
from vista_backend.utils import indexer


async def main() -> int:
    pdfs = kb / "pdfs"
    names = sorted(
        str(p.relative_to(pdfs)).replace("\\", "/") for p in pdfs.rglob("*.pdf")
    )
    results = await indexer.index_publications(
        rag_db_path=str(kb / "rag_db"),
        pdfs_dir=str(pdfs),
        filenames=names,
        extract_citations=bool(with_citations),
        llm_credentials=citation_credentials() if with_citations else None,
    )
    failed = [r for r in results if r.get("status") == "failed"]
    for r in failed:
        print(f"failed: {r.get('filename')}: {r.get('error')}", file=sys.stderr)
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
PYINDEX

  # A store that exists but holds nothing is the failure a researcher could not
  # diagnose, so it is caught here rather than shipped -- the same check the
  # backend applies before recording the knowledge base.
  VISTA_DATA_DIR="$STAGING_PAYLOAD" \
    "$STAGING_APP/backend/.venv/$VENV_PYTHON" - "$kb" <<'PYCHECK'
import sys
from pathlib import Path

from vista_backend.db.seed import _assert_knowledge_base_indexed

_assert_knowledge_base_indexed(Path(sys.argv[1]))
PYCHECK

  echo "store       : $(du -sh "$kb/rag_db" | cut -f1)"
}

# ─── sandbox image ──────────────────────────────────────────────────────────

# The agent's `run_bash` runs inside a microVM created by the bundled `msb`,
# from an OCI image. Building that image needs a container runtime; *loading* it
# does not, so the image is built and exported here and the launcher imports the
# archive with `msb load` on first run. That is the whole reason a researcher
# needs neither Docker nor podman.
export_sandbox_image() {
  # An archive supplied on the command line is copied in as-is. Its own
  # RepoTags are irrelevant: the launcher loads it with `msb load -t`, which
  # names the image itself, so only the contents and architecture matter --
  # and the architecture was settled in the preflight.
  if [[ -n "$SANDBOX_IMAGE_TAR" ]]; then
    log "staging the supplied sandbox image"
    cp "$SANDBOX_IMAGE_TAR" "$STAGING_PAYLOAD/sandbox-image.tar"
    echo "image       : $SANDBOX_IMAGE (from $SANDBOX_IMAGE_TAR)"
    echo "archive     : $(du -sh "$STAGING_PAYLOAD/sandbox-image.tar" | cut -f1)"
    return 0
  fi

  log "building and exporting the sandbox image"

  local dockerfile="$REPO_ROOT/mcp_servers/dev_mcp_server/src/dev_mcp_server/docker/Dockerfile"
  [[ -f "$dockerfile" ]] || die "sandbox Dockerfile not found at $dockerfile"

  "$CONTAINER_RUNTIME" build -t "$SANDBOX_IMAGE" -f "$dockerfile" \
    "$(dirname "$dockerfile")" >/dev/null
  "$CONTAINER_RUNTIME" save -o "$STAGING_PAYLOAD/sandbox-image.tar" "$SANDBOX_IMAGE"

  echo "image       : $SANDBOX_IMAGE"
  echo "archive     : $(du -sh "$STAGING_PAYLOAD/sandbox-image.tar" | cut -f1)"
}

# ─── manifest ───────────────────────────────────────────────────────────────

# What the package contains and how big each part is, written inside the
# package and beside the archive.
#
# Two audiences. A researcher gets to see what they were given and which
# version. And a deliberately incomplete build -- `--without-citations` -- has
# to be identifiable from this file alone, because the resulting package looks
# entirely healthy right up to the moment someone reads a citation.
# The oldest system libraries the artifact can run against. Everything
# compiled into it inherits the build environment's floor, so on Linux this is
# the build host's glibc -- which has to be at least 2.39, the minimum the
# bundled `msb` needs. Recorded rather than merely known, so a host that
# cannot run the artifact is identifiable without unpacking and starting it.
target_floor() {
  case "$TARGET_OS" in
    linux)
      local glibc
      glibc="$(ldd --version 2>/dev/null | sed -nE '1s/.* ([0-9]+\.[0-9]+)$/\1/p')"
      [[ -n "$glibc" ]] && printf ', "min_glibc": "%s"' "$glibc"
      ;;
    macos)
      local macos
      macos="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
      [[ -n "$macos" ]] && printf ', "built_on_macos": "%s"' "$macos"
      ;;
    windows)
      # The build host's version, for the same reason as macOS's. And the
      # package's longest path relative to its own root: Windows caps a full
      # path at 260 characters unless long paths are enabled, so the launcher
      # adds where it was unpacked to this and can say the location is too
      # deep before anything fails to open.
      local windows longest
      windows="$(cmd.exe //c ver 2>/dev/null | tr -d '\r' \
        | sed -nE 's/.*Version ([0-9.]+).*/\1/p' || true)"
      [[ -n "$windows" ]] && printf ', "built_on_windows": "%s"' "$windows"
      longest="$(shipped_files \
        | awk '{ if (length > m) m = length } END { print m + 0 }')"
      printf ', "longest_relative_path": %s' "$longest"
      # The same for what payload.tar puts into the state directory, whose
      # location the launcher also only learns at run time. Written while the
      # payload is still staged as folders, so it is measured from them.
      local packed longest_state
      packed="$(IFS='|'; echo "${PAYLOAD_PARTS[*]}")"
      longest_state="$(find "$STAGING_PAYLOAD" -type f -printf '%P\n' \
        | grep -E "^($packed)/" \
        | awk '{ if (length > m) m = length } END { print m + 0 }')"
      printf ', "longest_state_path": %s' "$longest_state"
      ;;
  esac
}

# ─── payload archive ────────────────────────────────────────────────────────

# The parts of the payload a launcher installs into the state directory on
# first run. They ship as one uncompressed tar, `payload/payload.tar`, rather
# than as folders, and the launcher extracts the ones the state directory does
# not have yet.
#
# The reason is path length. Some corpus PDFs have file names of 150+
# characters, a few folders deep, and inside a package they sit under the
# package folder and `payload/` as well -- far enough past Windows' 260-
# character limit that the zip could not be extracted anywhere. Extracted
# straight into the state directory they are ~240. One archive also means the
# corpus is a single file to copy until first run. Uncompressed because PDFs
# and model weights barely compress, and the release archive compresses anyway.
#
# POSIX (pax) format because it is the one that records file names as UTF-8.
# GNU tar's default stores the bytes with no charset, and the tar.exe Windows
# ships reads those in the ANSI code page, so a corpus name with an accent or
# an en dash would be extracted under a different name than the store records.
# pax stores a name's bytes as they are, so they must be UTF-8 to begin with:
# Git Bash converts Windows' UTF-16 names into the locale's charset, which is
# the builder's LANG unless it is pinned here.
PAYLOAD_PARTS=(vista-data knowledge-bases huggingface)

# What goes into the tar, finer than the top-level parts above: one member per
# corpus, so a launcher can install a corpus an upgraded state directory lacks
# even though its `vista-data/` and `knowledge-bases/` already exist. Listed in
# `payload/parts.txt`, the contract between this build and its launcher.
PAYLOAD_MEMBERS=(vista-data/ai-safety knowledge-bases/ai-safety huggingface)
if [[ "$SCIENCE_PROJECTS" == true ]]; then
  PAYLOAD_MEMBERS+=(vista-data/molten-salt-papers vista-data/mstdb knowledge-bases/molten-salt-papers)
fi

pack_payload() {
  log "packing the payload"
  LC_ALL=C.UTF-8 tar --format=posix -cf "$STAGING_PAYLOAD/payload.tar" -C "$STAGING_PAYLOAD" "${PAYLOAD_MEMBERS[@]}"
  local part
  for part in "${PAYLOAD_PARTS[@]}"; do
    rm -rf "${STAGING_PAYLOAD:?}/$part"
  done
  echo "payload.tar : $(du -sh "$STAGING_PAYLOAD/payload.tar" | cut -f1)"
}

# Every file the package ships, relative to its root, as it will be once the
# payload is packed -- so the manifest, written while the payload folders
# still exist, records the layout a recipient actually unpacks.
shipped_files() {
  local packed
  packed="$(IFS='|'; echo "${PAYLOAD_PARTS[*]}")"
  find "$STAGING" -type f -printf '%P\n' | grep -vE "^payload/($packed)/"
  echo "payload/payload.tar"
}

# How deep a Windows package's files go, against Windows' 260-character path
# limit. Files past it cannot be unpacked by Explorer at all, and a Python
# module past it cannot be imported unless the recipient's machine has long
# paths enabled, which most cannot turn on.
#
# Reported rather than enforced: what matters is the unpack location, which
# only the launcher knows, and the launcher refuses a location that is too
# deep. This says how much room the build left, and names the files using it,
# so a dependency that adds a deep tree is noticed at build time.
report_path_lengths() {
  [[ "$TARGET_OS" == windows ]] || return 0
  log "checking path lengths"
  local longest room
  longest="$(find "$STAGING" -type f -printf '%P\n' \
    | awk '{ if (length > m) m = length } END { print m + 0 }')"
  # 259 characters, less the package's own folder and the separators around it.
  room=$(( 259 - longest - ${#PACKAGE_NAME} - 2 ))
  echo "longest path: $longest characters inside the package"
  echo "unpack room : $room characters for the folder the package is unpacked into"
  if (( room < 40 )); then
    warn "only $room characters are left for the unpack location, which is less \
than C:\\Users\\<name>\\Downloads\\ needs for most names. The deepest files:"
    # sed rather than head: head exits after ten lines, and under pipefail
    # the SIGPIPE that leaves sort with would fail the build.
    find "$STAGING" -type f -printf '%P\n' \
      | awk '{ print length, $0 }' | sort -rn | sed -n 1,10p >&2
  fi
}

# Text chunks and citations in the vector store at `$1` (a knowledge-base
# folder), one count per line.
count_store() {
  VISTA_DATA_DIR="$STAGING_PAYLOAD" \
    "$STAGING_APP/backend/.venv/$VENV_PYTHON" - "$1/rag_db" <<'PYCOUNT'
import sys

import chromadb
from chromadb.config import Settings as ChromaSettings

client = chromadb.PersistentClient(
    path=sys.argv[1], settings=ChromaSettings(anonymized_telemetry=False)
)
for name in ("text_chunks", "citations"):
    try:
        print(client.get_collection(name).count())
    except Exception:
        print(0)
PYCOUNT
}

write_manifest() {
  log "writing the manifest"

  local kb="$STAGING_PAYLOAD/knowledge-bases/ai-safety"
  local chunks citations pdf_count counts
  counts="$(count_store "$kb")"
  chunks="$(echo "$counts" | sed -n 1p)"
  citations="$(echo "$counts" | sed -n 2p)"
  pdf_count="$(find "$STAGING_PAYLOAD/vista-data/ai-safety" -name '*.pdf' | wc -l | tr -d ' ')"

  # The molten-salt corpus, present only in a --science-projects build.
  local science_json=null science_citations=1
  if [[ "$SCIENCE_PROJECTS" == true ]]; then
    local science_kb="$STAGING_PAYLOAD/knowledge-bases/molten-salt-papers"
    local science_chunks science_pdfs
    counts="$(count_store "$science_kb")"
    science_chunks="$(echo "$counts" | sed -n 1p)"
    science_citations="$(echo "$counts" | sed -n 2p)"
    science_pdfs="$(find "$STAGING_PAYLOAD/vista-data/molten-salt-papers" -name '*.pdf' | wc -l | tr -d ' ')"
    science_json="{ \"corpus\": { \"pdfs\": $science_pdfs, \"bytes\": $(du -sk "$STAGING_PAYLOAD/vista-data/molten-salt-papers" | cut -f1 | awk '{printf "%d", $1 * 1024}') }, \"vector_store\": { \"text_chunks\": $science_chunks, \"citations\": $science_citations, \"bytes\": $(du -sk "$science_kb/rag_db" | cut -f1 | awk '{printf "%d", $1 * 1024}') } }"
  fi

  local size_of
  size_of() { du -sk "$1" 2>/dev/null | cut -f1 | awk '{printf "%d", $1 * 1024}'; }

  # Where the launcher finds the window, relative to the package root, or null
  # on a target that has none. Kept on one line: the launcher reads `exe` with
  # the same `sed` field reader as the platform guard. The window has its own
  # byte count; on macOS it is a top-level sibling rather than part of app/.
  local window_json=null entrypoint_json=null diagnostic_launcher_json=null
  if [[ -n "$WINDOW_EXE" ]]; then
    local window_path="$STAGING_APP/window"
    if [[ "$TARGET_OS" == macos ]]; then
      window_path="$STAGING/VISTA.app"
      entrypoint_json='"VISTA.app"'
      diagnostic_launcher_json='"vista"'
    fi
    window_json="{ \"exe\": \"$WINDOW_EXE\", \"electron\": \"$ELECTRON_VERSION\", \"bytes\": $(size_of "$window_path") }"
  fi

  cat > "$STAGING/manifest.json" <<EOF
{
  "name": "$PACKAGE_NAME",
  "version": "$VERSION",
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "commit": "$COMMIT",
  "entrypoint": $entrypoint_json,
  "diagnostic_launcher": $diagnostic_launcher_json,
  "target": { "os": "$TARGET_OS", "arch": "$TARGET_ARCH"$(target_floor) },
  "runtimes": {
    "python": "$(basename "$BUNDLED_PYTHON_DIR")",
    "node": "$("$NODE_BIN" --version)",
    "uv": "$("$STAGING_BIN/uv$EXE" --version | cut -d' ' -f2)"
  },
  "components": {
    "python": $(size_of "$STAGING_PYTHON"),
    "node": $(size_of "$STAGING/node"),
    "bin": $(size_of "$STAGING_BIN"),
    "app": $(size_of "$STAGING_APP"),
    "payload": $(size_of "$STAGING_PAYLOAD")
  },
  "payload": {
    "sandbox_image": { "reference": "$SANDBOX_IMAGE", "bytes": $(size_of "$STAGING_PAYLOAD/sandbox-image.tar") },
    "corpus": { "pdfs": $pdf_count, "bytes": $(size_of "$STAGING_PAYLOAD/vista-data") },
    "vector_store": { "text_chunks": $chunks, "citations": $citations, "bytes": $(size_of "$kb/rag_db") },
    "embedding_weights": { "bytes": $(size_of "$STAGING_PAYLOAD/huggingface") },
    "science": $science_json,
    "parts": $(printf '%s\n' "${PAYLOAD_MEMBERS[@]}" | sed 's/.*/"&"/' | paste -sd, - | sed 's/^/[/; s/$/]/')
  },
  "science_projects": $SCIENCE_PROJECTS,
  "window": $window_json,
  "completeness": {
    "corpus_citations": $([[ "$citations" -gt 0 && "$science_citations" -gt 0 ]] && echo true || echo false)
  }
}
EOF
  echo "$VERSION" > "$STAGING/VERSION"

  # Fail rather than ship a manifest that claims something untrue.
  "$STAGING_APP/backend/.venv/$VENV_PYTHON" - "$STAGING/manifest.json" "$TARGET_OS" <<'PYVALID'
import json
import os
import sys
from pathlib import Path

manifest = json.loads(open(sys.argv[1], encoding="utf-8").read())
target_os = sys.argv[2]

# A package without its window cannot start at all: there is no browser mode,
# and the launcher refuses. Caught here rather than on a researcher's machine.
window = manifest.get("window")
if target_os in ("macos", "linux", "windows") and not window:
    sys.exit(f"manifest has no window on {target_os}")
if window:
    exe = Path(sys.argv[1]).parent / window["exe"]
    if not (exe.is_file() and os.access(exe, os.X_OK)):
        sys.exit(f"manifest names a window executable that is not there: {window['exe']}")
    # On Linux the launcher cannot start the window without asking this first.
    if target_os == "linux":
        sandbox = exe.parent / "window-sandbox"
        if not (sandbox.is_file() and os.access(sandbox, os.X_OK)):
            sys.exit("the Linux window has no executable window-sandbox next to it")
if target_os == "macos":
    if manifest.get("entrypoint") != "VISTA.app":
        sys.exit("the macOS manifest does not name VISTA.app as its entrypoint")
    if manifest.get("diagnostic_launcher") != "vista":
        sys.exit("the macOS manifest does not name vista as its diagnostic launcher")
    launcher = Path(sys.argv[1]).parent / manifest["diagnostic_launcher"]
    if not (launcher.is_file() and os.access(launcher, os.X_OK)):
        sys.exit("the macOS diagnostic launcher is missing or not executable")
for section, keys in (
    ("components", ("python", "node", "bin", "app", "payload")),
    ("payload", ("sandbox_image", "corpus", "vector_store", "embedding_weights")),
):
    missing = [k for k in keys if k not in manifest[section]]
    if missing:
        sys.exit(f"manifest is missing {section}: {missing}")
if manifest["payload"]["vector_store"]["text_chunks"] < 1:
    sys.exit("manifest reports an empty vector store")
# The science data is in the package exactly when the build said it would be.
science = manifest["payload"]["science"]
if bool(science) != manifest["science_projects"]:
    sys.exit("manifest's science_projects disagrees with the science payload")
if science and science["vector_store"]["text_chunks"] < 1:
    sys.exit("manifest reports an empty molten-salt vector store")
PYVALID

  echo "manifest    : $STAGING/manifest.json"
}

# ─── archive ────────────────────────────────────────────────────────────────

# Pack the tree, preserving hardlinks and extended attributes.
#
# Hardlinks because uv installs from its cache by linking, so the same wheel
# files appear in more than one environment as one inode -- tar collapses them
# back to a single copy plus link entries. Extended attributes because the
# bundled `msb` carries an adhoc code signature in them, and macOS refuses to
# execute a binary whose signature no longer matches.
#
# The default format is chosen with the platform, near the top. zstd is
# available for a faster local round trip.
#
# A Windows zip is written by Windows' own bsdtar: Git Bash's GNU tar cannot
# write zip, and there are no hardlinks or extended attributes to carry.
create_archive() {
  [[ "$ARCHIVE_FORMAT" == none ]] && { log "archive: skipped (--archive-format none)"; return 0; }

  log "creating the archive"
  local suffix
  case "$ARCHIVE_FORMAT" in
    gz) suffix=tar.gz ;;
    zstd) suffix=tar.zst ;;
    zip) suffix=zip ;;
  esac
  ARCHIVE_PATH="$OUTPUT_DIR/${PACKAGE_NAME}.${suffix}"

  if [[ "$ARCHIVE_FORMAT" == zip ]]; then
    rm -f "$ARCHIVE_PATH"
    "$WIN_TAR" -a -cf "$ARCHIVE_PATH" -C "$OUTPUT_DIR" "$PACKAGE_NAME"
    write_archive_sidecars
    return 0
  fi

  local xattr_flag=()
  # bsdtar (macOS) stores extended attributes by default and rejects --xattrs;
  # GNU tar needs to be asked.
  if tar --version 2>/dev/null | grep -qi 'gnu tar'; then
    # `no-xattr` silences one warning per file on filesystems that do not
    # carry them, which a container's overlay and bind mounts do not. The
    # signature this flag exists for is a macOS concern, and macOS uses
    # bsdtar, so on Linux the flag is kept for correctness and quieted.
    xattr_flag=(--xattrs --warning=no-xattr)
  fi

  local compressor
  case "$ARCHIVE_FORMAT" in
    gz) compressor=(gzip -c) ;;
    zstd) compressor=(zstd -T0 -q -c) ;;
  esac

  tar "${xattr_flag[@]+"${xattr_flag[@]}"}" -cf - \
    -C "$OUTPUT_DIR" "$PACKAGE_NAME" \
    | "${compressor[@]}" > "$ARCHIVE_PATH"

  write_archive_sidecars
}

# The checksum and manifest that sit beside every archive, whatever its format.
write_archive_sidecars() {
  ( cd "$OUTPUT_DIR" && shasum -a 256 "$(basename "$ARCHIVE_PATH")" \
      > "$(basename "$ARCHIVE_PATH").sha256" )
  cp "$STAGING/manifest.json" "$ARCHIVE_PATH.manifest.json"

  echo "archive     : $ARCHIVE_PATH ($(du -sh "$ARCHIVE_PATH" | cut -f1))"
  echo "checksum    : $(cut -d' ' -f1 < "$ARCHIVE_PATH.sha256")"
}

# ─── smoke test ─────────────────────────────────────────────────────────────

# Unpack the archive somewhere else and actually run it.
#
# Relocation is the highest-risk part of this design and a manifest proves
# files exist, not that they still work. Unpacked at a different path depth
# because that is what catches a path baked in at build time; a same-depth test
# can pass on a broken package.
run_smoke_test() {
  if [[ "$SKIP_SMOKE_TEST" == true ]]; then
    log "smoke test: skipped (--skip-smoke-test)"
    return 0
  fi
  if [[ "$ARCHIVE_FORMAT" == none ]]; then
    log "smoke test: skipped (no archive was created)"
    return 0
  fi

  log "smoke test: unpacking elsewhere and running"
  # On Windows the user's Temp folder is already at a different depth from
  # the staging tree, and the extra levels would only spend the 260-character
  # path budget the launcher checks against.
  local root
  root="$(mktemp -d)/a/deeper/path"
  [[ "$TARGET_OS" == windows ]] && root="$(mktemp -d)"
  mkdir -p "$root"
  if [[ "$ARCHIVE_FORMAT" == zip ]]; then
    "$WIN_TAR" -xf "$ARCHIVE_PATH" -C "$root"
  else
    tar -xf "$ARCHIVE_PATH" -C "$root"
  fi
  local unpacked="$root/$PACKAGE_NAME"

  # msb has to survive the round trip and run here. Two different things can
  # stop it, so the loader's own words are reported rather than guessed at: on
  # macOS a lost adhoc signature, on Linux a glibc older than the binary
  # wants. Claiming the former when it was the latter sent one debugging
  # session looking for missing extended attributes on a perfectly intact
  # 29 MB binary.
  local msb
  msb="$(find "$unpacked/app/mcp_servers/dev_mcp_server/.venv" \
    -path "*/microsandbox/_bundled/bin/msb$EXE" -print -quit)"
  [[ -x "$msb" ]] || die "smoke test: no msb binary in the unpacked package"
  local msb_error
  if ! msb_error="$("$msb" --version 2>&1)"; then
    die "smoke test: the unpacked msb will not run.

    $msb_error

  On Linux that is usually a glibc older than the binary requires; compare
  \`objdump -T\` on it against \`ldd --version\` here. On macOS it is usually an
  adhoc code signature lost in archiving, so check that extended attributes
  were preserved. On Windows, check whether Defender quarantined it."
  fi

  # The package is unpacked deep on purpose -- that is what catches a path baked
  # in at build time. The state directory is not: the sandbox's socket path is
  # derived from it and has to stay under the kernel's limit, which is a
  # property of where a researcher keeps their state, not of where the package
  # sits.
  #
  # Deliberately not under `$TMPDIR`: on macOS that is a per-user directory
  # roughly 50 characters long before anything is added to it, which cannot fit
  # the sandbox's socket budget however short the rest of the path is. `/tmp` is
  # short on both platforms. Windows is handled below.
  local state
  state="$(mktemp -d /tmp/vista-smoke.XXXXXX)"
  # Under Git Bash, /tmp is the user's Temp folder, deep enough that the
  # corpus's longest paths pass Windows' 260-character limit once extracted
  # into it. The home folder puts the state as deep as the default ~/.vista.
  if [[ "$TARGET_OS" == windows ]]; then
    rm -rf "$state"
    state="$(mktemp -d "$HOME/.vista-smoke.XXXXXX")"
  fi
  local failures=0
  "$REPO_ROOT/scripts/smoke_test_package.sh" "$unpacked" "$state" || failures=1

  if (( failures )); then
    die "smoke test failed; the archive at $ARCHIVE_PATH is not usable. The \
unpacked copy was left at $unpacked for inspection."
  fi
  rm -rf "$root" "$state"
  echo "smoke test  : passed"
}

ARCHIVE_PATH=''
SANDBOX_IMAGE="vista-sandbox:latest"
BUNDLED_PYTHON=''
BUNDLED_PYTHON_DIR=''

prepare_staging
bundle_runtime
bundle_node
build_mcp_app
stage_sources
stage_ui
stage_window
create_environments
stage_payload
stage_embedding_weights
build_vector_store ai-safety "$REUSE_STORE"
if [[ "$SCIENCE_PROJECTS" == true ]]; then
  build_vector_store molten-salt-papers "$SCIENCE_STORE"
fi
export_sandbox_image
write_manifest
pack_payload
report_path_lengths
create_archive
run_smoke_test

log "built $PACKAGE_NAME"
if [[ -n "$ARCHIVE_PATH" ]]; then
  echo "$ARCHIVE_PATH"
else
  echo "$STAGING"
fi
if [[ "$KEEP_STAGING" != true && -n "$ARCHIVE_PATH" ]]; then
  rm -rf "$STAGING"
fi
