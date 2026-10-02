#!/bin/bash
set -euo pipefail

REPO_ROOT="$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")"
OUTPUT_DIR="$REPO_ROOT/dist/mac-dev"
REFRESH=false

usage() {
  cat <<'EOF'
usage: ./scripts/build_mac_dev_app.sh [--refresh-dependencies] [--output-dir DIR]

Builds a thin, ad-hoc-signed VISTA Dev.app backed by this source checkout.
It does not bundle corpora, runtimes, a sandbox image, or the private HPC SDK.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --refresh-dependencies) REFRESH=true; shift ;;
    --output-dir)
      [[ $# -ge 2 ]] || { echo '--output-dir needs a directory' >&2; exit 2; }
      OUTPUT_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$(uname -s)" == Darwin ]] || { echo 'the developer app can be built only on macOS' >&2; exit 1; }
for command_name in npm npx uv codesign; do
  command -v "$command_name" >/dev/null 2>&1 \
    || { echo "missing required command: $command_name" >&2; exit 1; }
done

ensure_npm() {
  local directory="$1"
  if [[ "$REFRESH" == true || ! -d "$directory/node_modules" ]]; then
    (cd "$directory" && npm ci)
  fi
}

ensure_uv() {
  local directory="$1" marker="$2"
  if [[ "$REFRESH" == true || ! -x "$directory/.venv/bin/$marker" ]]; then
    (cd "$directory" && uv sync --frozen)
  fi
}

echo '==> preparing checkout dependencies'
ensure_npm "$REPO_ROOT/ui"
ensure_npm "$REPO_ROOT/electron"
ensure_npm "$REPO_ROOT/mcp_servers/vista_mcp_server/mcp-apps"
if [[ "$REFRESH" == true || ! -d "$REPO_ROOT/mcp_servers/vista_mcp_server/src/vista_mcp_server/mcp-apps" ]]; then
  (cd "$REPO_ROOT/mcp_servers/vista_mcp_server/mcp-apps" && npm run build)
fi
ensure_uv "$REPO_ROOT/mcp_servers/vista_mcp_server" vista-mcp-server
ensure_uv "$REPO_ROOT/mcp_servers/dev_mcp_server" dev-mcp-server
ensure_uv "$REPO_ROOT/backend" vista-backend

if [[ ! -f "$REPO_ROOT/electron/node_modules/electron/path.txt" ]]; then
  (cd "$REPO_ROOT/electron" && npx --no install-electron)
fi

case "$(uname -m)" in
  arm64) ARCH=arm64 ;;
  x86_64) ARCH=x64 ;;
  *) echo "unsupported Mac architecture: $(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "$OUTPUT_DIR"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/vista-mac-dev.XXXXXX")"
cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

echo '==> packaging VISTA Dev.app'
PACKAGED_APP="$(cd "$REPO_ROOT/electron" && node scripts/package.js \
  --platform darwin --arch "$ARCH" --out "$STAGING" --development | tail -n 1)"
DESTINATION="$OUTPUT_DIR/VISTA Dev.app"
rm -rf "$DESTINATION"
mv "$PACKAGED_APP" "$DESTINATION"

cp "$REPO_ROOT/scripts/mac_dev_launcher.sh" "$OUTPUT_DIR/vista"
chmod +x "$OUTPUT_DIR/vista"
printf '%s\n' "$REPO_ROOT" > "$OUTPUT_DIR/dev-root"
cat > "$OUTPUT_DIR/manifest.json" <<EOF
{
  "target": { "os": "macos", "arch": "$ARCH" },
  "entrypoint": "VISTA Dev.app",
  "diagnostic_launcher": "vista",
  "window": { "exe": "VISTA Dev.app/Contents/MacOS/VISTA Dev" },
  "development": { "checkout_file": "dev-root", "hpc_submission": false }
}
EOF

codesign --force --deep --sign - "$DESTINATION"
codesign --verify --deep --strict "$DESTINATION"

echo
echo "Built: $DESTINATION"
echo "Open it with: open \"$DESTINATION\""
echo 'Logs: ~/.vista-dev/logs/dev-stack.log'
