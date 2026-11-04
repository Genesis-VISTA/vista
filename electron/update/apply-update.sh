#!/usr/bin/env bash
# Install a downloaded VISTA update once VISTA has closed, then open VISTA again
# (openspec change `in-app-updates`, design D5). macOS and Linux.
#
# The window copies this into the update's own folder in VISTA_HOME/updates and
# starts it, detached, just before it stops VISTA the way closing it does. Run
# from there, nothing here is in the folder the installer replaces. Its inputs
# are in the environment, so its own command line never names the install
# folder that it waits on:
#
#   VISTA_UPDATE_INSTALL_DIR  the installed package (what the installer replaces)
#   VISTA_UPDATE_VERSION      the version being installed
#   VISTA_UPDATE_CACHE        the verified download: archive, .sha256, installer
#   VISTA_UPDATE_INSTALLER    the release's install.sh, in that folder
#   VISTA_HOME                VISTA's state; logs/update.log and
#                             updates/last-failure.json go here
#
# For tests only:
#   VISTA_UPDATE_WAIT_SECONDS how long to wait for VISTA to close (default 60)
#   VISTA_UPDATE_OPEN         the command that opens VISTA, instead of the platform's
#
# Success opens the new version. Any failure leaves the previous one in place
# (the installer moves it only once the new one is ready), records why in
# last-failure.json for the startup window, and opens it again.

set -uo pipefail

dir="${VISTA_UPDATE_INSTALL_DIR:?}"
version="${VISTA_UPDATE_VERSION:?}"
cache="${VISTA_UPDATE_CACHE:?}"
installer="${VISTA_UPDATE_INSTALLER:?}"
home="${VISTA_HOME:?}"
wait_seconds="${VISTA_UPDATE_WAIT_SECONDS:-60}"

mkdir -p "$home/logs" "$home/updates"
log="$home/logs/update.log"
exec >> "$log" 2>&1

say() { printf '%s apply-update: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# The command the installer's own launch step uses (install.sh `finish`).
open_vista() {
  if [[ -n "${VISTA_UPDATE_OPEN:-}" ]]; then
    "$VISTA_UPDATE_OPEN" "$dir"
  elif [[ "$(uname -s)" == Darwin ]]; then
    open "$dir/VISTA.app"
  elif command -v setsid >/dev/null 2>&1; then
    setsid "$dir/app/window/vista-app" < /dev/null > /dev/null 2>&1 &
  else
    nohup "$dir/app/window/vista-app" < /dev/null > /dev/null 2>&1 &
  fi
}

json_string() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="$(printf '%s' "$s" | tr '\000-\037' ' ')"
  printf '"%s"' "$s"
}

fail() {
  local reason="$1"
  say "update to $version failed: $reason"
  printf '{"version": %s, "reason": %s, "log": "update.log"}\n' \
    "$(json_string "$version")" "$(json_string "$reason")" > "$home/updates/last-failure.json"
  say "opening the installed version again"
  open_vista
  exit 1
}

# A process started from the install folder, by anyone, means VISTA is still
# running: the installer's own test (install.sh `refuse_if_running`).
running() {
  local pid
  for pid in $(pgrep -f -- "$dir/" 2>/dev/null); do
    [[ "$pid" == "$$" ]] || return 0
  done
  return 1
}

say "updating VISTA in $dir to $version"
waited=0
while running; do
  if (( waited >= wait_seconds )); then
    fail "VISTA did not finish closing within $wait_seconds seconds, so nothing was installed."
  fi
  sleep 1
  waited=$((waited + 1))
done
say "VISTA has closed (after ${waited}s); running the installer"

out="$(mktemp)"
VISTA_INSTALL_BASE_URL="file://$cache" VISTA_INSTALL_DIR="$dir" \
  bash "$installer" --version "$version" --no-launch < /dev/null > "$out" 2>&1
status=$?
cat "$out"
if (( status != 0 )); then
  reason="$(grep -E '^error: ' "$out" | tail -1 | sed -E 's/^error: //')"
  rm -f "$out"
  fail "${reason:-the installer exited with status $status.}"
fi
rm -f "$out"

say "installed VISTA $version; opening it"
open_vista
