#!/bin/bash
# The server image's entrypoint: puts the baked-in sandbox image into
# microsandbox's store, then runs VISTA headless. Arguments go on to launch.sh.
#
# MSB_HOME is on the /data volume, so the store outlives the container and the
# load is needed only when the image ships a different sandbox. Its digest,
# written by build-image.sh, is recorded beside the store to tell.
set -euo pipefail

SANDBOX_TAR=/app/sandbox/vista-sandbox.tar
SANDBOX_DIGEST="$(cat "$SANDBOX_TAR.digest")"
LOADED="$MSB_HOME/vista-sandbox.digest"

# The msb binary the microsandbox SDK bundles, found as the package launcher
# finds it (scripts/package_launcher.sh).
MSB="$(find /app/mcp_servers/dev_mcp_server/.venv \
  -path '*/microsandbox/_bundled/bin/msb' -print -quit)"
[[ -x "$MSB" ]] || { echo "entrypoint: no msb in the dev MCP server's venv" >&2; exit 1; }

mkdir -p "$MSB_HOME"
if [[ "$(cat "$LOADED" 2>/dev/null)" != "$SANDBOX_DIGEST" ]] \
    || ! "$MSB" image inspect --format=json "$VISTA_DEV_MCP_IMAGE" >/dev/null 2>&1; then
  echo "entrypoint: loading the sandbox image $VISTA_DEV_MCP_IMAGE ($SANDBOX_DIGEST)"
  "$MSB" load -i "$SANDBOX_TAR" -t "$VISTA_DEV_MCP_IMAGE"
  printf '%s\n' "$SANDBOX_DIGEST" > "$LOADED"
fi

# Headless: in logs mode launch.sh opens the Electron window unless told not to.
exec /app/launch.sh logs --prod --no-build --no-electron "$@"
