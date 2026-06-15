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
GLOBUS_CONFIG_DIR="${VISTA_DATA_DIR:-./data}/globusonline" # Persist GCP config
mkdir -p "$HPC_JOBS_DIR" "$VOLUMES_DIR" "$GLOBUS_CONFIG_DIR"
HPC_JOBS_DIR=$(realpath "$HPC_JOBS_DIR")
VOLUMES_DIR=$(realpath "$VOLUMES_DIR")
GLOBUS_CONFIG_DIR=$(realpath "$GLOBUS_CONFIG_DIR")

if [[ ! -d "$GLOBUS_CONFIG_DIR/lta" && -z "${GLOBUS_SETUP_KEY:-}" && ! -t 0 ]]; then
  echo "error: Globus Connect Personal is not set up. Run 'launch_globus.sh --setup' in an" >&2
  echo "interactive terminal or set GLOBUS_SETUP_KEY" >&2
  exit 1
fi

GCP_INSTALL_DIR="${GCP_INSTALL_DIR:-$HOME/bin/globusconnectpersonal}"
GCP_TARBALL_URL="https://downloads.globus.org/globus-connect-personal/linux/stable/globusconnectpersonal-latest.tgz"

GCP="$(command -v globusconnectpersonal || true)"
if [[ -z "$GCP" ]]; then
  if [[ "$(uname -s)" == "Linux" ]]; then
    # Linux: download the standalone client to the user's home if not present.
    GCP="$GCP_INSTALL_DIR/globusconnectpersonal"
    if [[ ! -d "$GCP_INSTALL_DIR" ]]; then
      echo "globusconnectpersonal not found; installing to $GCP_INSTALL_DIR ..."
      mkdir -p "$GCP_INSTALL_DIR"
      curl -fsSL "$GCP_TARBALL_URL" | tar -xz -C "$GCP_INSTALL_DIR" --strip-components=1
    fi
  else
    # Can't easily autodownload the MacOS GUI application
    GCP="/Applications/Globus Connect Personal.app/Contents/MacOS/globusconnectpersonal"
    if [[ ! -x "$GCP" ]]; then
      echo "error: globusconnectpersonal not found" >&2
      echo "Install Globus Connect Personal from" >&2
      echo "  https://www.globus.org/globus-connect-personal" >&2
      exit 1
    fi
  fi
fi

if [[ ! -f "$GLOBUS_CONFIG_DIR/lta/client-id.txt" ]]; then
  if [[ -n "${GLOBUS_SETUP_KEY:-}" ]]; then
    "$GCP" -dir "$GLOBUS_CONFIG_DIR" -setup "$GLOBUS_SETUP_KEY"
  else
    "$GCP" -dir "$GLOBUS_CONFIG_DIR" -setup --no-gui
  fi
fi

COLLECTION_ID="$(cat "$GLOBUS_CONFIG_DIR/lta/client-id.txt" | tr -d '[:space:]')"
if [[ "$SAVE_ENV" -eq 1 ]]; then
  touch ".env"
  if grep -qE '^[[:space:]]*#?[[:space:]]*VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=' ".env"; then
    tmp="$(mktemp)"
    sed -E "s|^[[:space:]]*#?[[:space:]]*VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=.*|VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID=$COLLECTION_ID|" ".env" > "$tmp"
    mv "$tmp" ".env"
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

exec "$GCP" -dir "$GLOBUS_CONFIG_DIR" -start -restrict-paths "r${HPC_JOBS_DIR}/,rw${VOLUMES_DIR}/"
