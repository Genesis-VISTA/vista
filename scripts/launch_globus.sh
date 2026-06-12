#!/bin/bash
# Launch the Vista-side Globus Connect Personal endpoint, exposing:
#   - hpc_jobs/        (source uploads:  Vista -> OLCF)
#   - data/volumes/    (per-project x user output dirs; downloads + log fetches)
# Pass --save-env to write the collection UUID to .env as
# VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID (otherwise it is just printed).
#
# First-time setup needs a one-time Globus login. Either:
#   - run this script once in an interactive terminal (browser login flow), or
#   - set GLOBUS_SETUP_KEY for headless setup, create the key with:
#         uvx --from globus-cli globus gcp create mapped "vista-server"

set -euo pipefail

SAVE_ENV=0
SETUP=0
for arg in "$@"; do
  case "$arg" in
    --save-env) SAVE_ENV=1 ;;
    --setup) SETUP=1 ;;
    *) echo "error: unknown argument: $arg" >&2; exit 1 ;;
  esac
done

REPO_ROOT="$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")"
cd "$REPO_ROOT"
set -o allexport; source .env 2>/dev/null || true; set +o allexport

HPC_JOBS_DIR="${VISTA_MCP_LOCAL_HPC_JOBS_DIR:-./hpc_jobs}"
VOLUMES_DIR="${VISTA_DATA_DIR:-./data}/volumes"
mkdir -p "$HPC_JOBS_DIR" "$VOLUMES_DIR"
HPC_JOBS_DIR=$(realpath "$HPC_JOBS_DIR")
VOLUMES_DIR=$(realpath "$VOLUMES_DIR")

if [[ "$(uname -s)" != "Linux" ]]; then
  RUNTIME=""
  for candidate in docker podman; do
    if command -v "$candidate" >/dev/null 2>&1; then
      RUNTIME="$candidate"
      break
    fi
  done
  [[ -n "$RUNTIME" ]] || { echo "error: neither docker nor podman found on PATH (required to run Globus Connect Personal on $(uname -s))" >&2; exit 1; }

  TTY_FLAG=""
  [[ -t 0 ]] && TTY_FLAG="-t"

  # Build the vista-globus image once so the deps aren't reinstalled on every run.
  echo "Building vista-globus image ..." >&2
  "$RUNTIME" build -t vista-globus - <<'DOCKERFILE'
FROM ubuntu:24.04
RUN apt-get update -qq \
    && apt-get install -y -qq curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
DOCKERFILE

  "$RUNTIME" rm -f vista-globus >/dev/null 2>&1 || true
  exec "$RUNTIME" run --rm -i $TTY_FLAG \
    --name vista-globus \
    -e GLOBUS_SETUP_KEY \
    -v vista-gcp-home:/root \
    -v "$REPO_ROOT:$REPO_ROOT" \
    -v "$HPC_JOBS_DIR:$HPC_JOBS_DIR" \
    -v "$VOLUMES_DIR:$VOLUMES_DIR" \
    -w "$REPO_ROOT" \
    vista-globus \
    bash scripts/launch_globus.sh "$@"
fi

if [[ ! -d "$HOME/.globusonline/lta" && -z "${GLOBUS_SETUP_KEY:-}" && ! -t 0 ]]; then
  echo "error: Globus Connect Personal is not set up. Run 'launch_globus.sh --setup' in an" >&2
  echo "interactive terminal or set GLOBUS_SETUP_KEY" >&2
  exit 1
fi

GCP_INSTALL_DIR="${GCP_INSTALL_DIR:-$HOME/bin/globusconnectpersonal}"
GCP_TARBALL_URL="https://downloads.globus.org/globus-connect-personal/linux/stable/globusconnectpersonal-latest.tgz"

GCP="$(command -v globusconnectpersonal || true)"
if [[ -z "$GCP" ]]; then
  GCP="$GCP_INSTALL_DIR/globusconnectpersonal"
  if [[ ! -d "$GCP_INSTALL_DIR" ]]; then
    echo "globusconnectpersonal not found; installing to $GCP_INSTALL_DIR ..."
    mkdir -p "$GCP_INSTALL_DIR"
    curl -fsSL "$GCP_TARBALL_URL" | tar -xz -C "$GCP_INSTALL_DIR" --strip-components=1
  fi
fi

if [[ ! -d "$HOME/.globusonline/lta" ]]; then
  if [[ -n "${GLOBUS_SETUP_KEY:-}" ]]; then
    "$GCP" -setup "$GLOBUS_SETUP_KEY"
  else
    "$GCP" -setup --no-gui
  fi
fi

COLLECTION_ID="$(cat "$HOME/.globusonline/lta/client-id.txt" | tr -d '[:space:]')"
if [[ "$SAVE_ENV" -eq 1 ]]; then
  touch ".env"
  if grep -qE '^[[:space:]]*#?[[:space:]]*VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=' ".env"; then
    sed -i -E "s|^[[:space:]]*#?[[:space:]]*VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=.*|VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=$COLLECTION_ID|" ".env"
  else
    echo  >> ".env"
    echo "VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=$COLLECTION_ID" >> ".env"
  fi
  echo "Wrote VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=$COLLECTION_ID to .env"
else
  echo "VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=$COLLECTION_ID"
  echo "(pass --save-env to write this to .env)"
fi

if [[ "$SETUP" -eq 1 ]]; then
  echo "Globus endpoint setup complete."
  exit 0
fi

exec "$GCP" -start -restrict-paths "r${HPC_JOBS_DIR}/,rw${VOLUMES_DIR}/"
