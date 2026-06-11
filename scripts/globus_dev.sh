#!/bin/bash
# Dev helper: run a Globus Connect Personal endpoint on this host for Frontier
# file ops, exposing exactly the two directories Vista transfers against:
#   - hpc_jobs/        (source uploads:  Vista GCS -> OLCF DTN)
#   - data/volumes/    (per-project x user output dirs; downloads + log fetches)
# Put the printed collection UUID into VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID.

set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This script only supports Linux." >&2
  exit 1
fi

REPO_ROOT="$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")"
cd "$REPO_ROOT"
set -o allexport; source .env 2>/dev/null || true; set +o allexport

GCP_INSTALL_DIR="${GCP_INSTALL_DIR:-$HOME/bin/globusconnectpersonal}"
GCP_TARBALL_URL="https://downloads.globus.org/globus-connect-personal/linux/stable/globusconnectpersonal-latest.tgz"

HPC_JOBS_DIR=$(realpath "${VISTA_MCP_LOCAL_HPC_JOBS_DIR:-./hpc_jobs}")
VOLUMES_DIR=$(realpath "${VISTA_DATA_DIR:-./data}/volumes")

GCP="$(command -v globusconnectpersonal || true)"
if [[ -z "$GCP" ]]; then
  GCP="$GCP_INSTALL_DIR/globusconnectpersonal"
  if [[ ! -d "$GCP_INSTALL_DIR" ]]; then
    echo "globusconnectpersonal not found; installing to $GCP_INSTALL_DIR ..."
    mkdir -p "$GCP_INSTALL_DIR"
    curl -fsSL "$GCP_TARBALL_URL" | tar -xz -C "$GCP_INSTALL_DIR" --strip-components=1
  fi
fi

mkdir -p "$HPC_JOBS_DIR" "$VOLUMES_DIR"

if [[ ! -d "$HOME/.globusonline/lta" ]]; then
  "$GCP" -setup --no-gui
fi

COLLECTION_ID="$(cat "$HOME/.globusonline/lta/client-id.txt" | tr -d '[:space:]')"
touch ".env"
if grep -qE '^[[:space:]]*#?[[:space:]]*VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=' ".env"; then
  sed -i -E "s|^[[:space:]]*#?[[:space:]]*VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=.*|VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=$COLLECTION_ID|" ".env"
else
  echo  >> ".env"
  echo "VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=$COLLECTION_ID" >> ".env"
fi
echo "Wrote VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=$COLLECTION_ID to .env"

"$GCP" -debug -restrict-paths "r${HPC_JOBS_DIR}/,rw${VOLUMES_DIR}/"
