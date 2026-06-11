#!/bin/bash
set -euo pipefail

REPO_ROOT="$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")"
# Source the .env file
set -o allexport; source "$REPO_ROOT/.env" 2>/dev/null || true; set +o allexport

PROD=false
for arg in "$@"; do
  case "$arg" in
    --prod) PROD=true ;;
    *) echo "Unknown build.sh argument: $arg" >&2; exit 1 ;;
  esac
done

cd "$REPO_ROOT/mcp_servers/vista_mcp_server/mcp-apps"
npm ci
npm run build

cd "$REPO_ROOT/mcp_servers/vista_mcp_server"
# amscrot-py (the AmSC IRI SDK) is a base dependency since both NERSC (Perlmutter)
# and OLCF (Frontier) routing rely on it. The git+ssh:// URL requires GitLab SSH
# keys configured for the user running this build — see README.md.
uv sync --frozen

cd "$REPO_ROOT/mcp_servers/dev_mcp_server"
uv sync --frozen
# Pre-build the sandbox image so it's ready before the server starts.
uv run dev-mcp-server --pre-build

cd "$REPO_ROOT/backend"
uv sync --frozen

cd "$REPO_ROOT/ui"
npm ci
# In prod we serve a precompiled build via `npm start`, in dev we just run the devserver
if [[ "$PROD" == true ]]; then
    npm run build
fi

cd "$REPO_ROOT"
