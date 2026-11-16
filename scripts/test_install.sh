#!/usr/bin/env bash
# Hermetic tests for scripts/install.sh, against fake packages served from a
# file:// URL. Needs bash, curl, tar, pgrep and sha256sum or shasum; no network
# and no real package. Each run fakes `uname`, so the macOS and the Linux
# install both run on either; the system Applications folder is a temporary one,
# and `open` a stand-in that records what it was asked to open.
#
#   scripts/test_install.sh

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
installer="$here/install.sh"
root="$(mktemp -d)"
holder=''
cleanup() {
  [[ -n "$holder" ]] && kill "$holder" 2>/dev/null
  chmod -R u+w "$root" 2>/dev/null
  rm -rf "$root"
}
trap cleanup EXIT

failures=0
pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; failures=$((failures + 1)); }
skip() { printf '  skip %s -- %s\n' "$1" "$2"; }
check() { local what="$1"; shift; if "$@"; then pass "$what"; else fail "$what"; fi; }

sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

# A fake package, laid out as the platform's is: VERSION, manifest.json, and a
# `vista` that records that it ran; on macOS a VISTA.app beside it, on Linux the
# app-menu entry point and its icon under app/window.
make_release() {
  local dir="$1" version="$2" platform="$3"
  local name="vista-$version-$platform" src="$root/src/$version-$platform"
  mkdir -p "$src/$name" "$dir"
  echo "$version" > "$src/$name/VERSION"
  echo '{}' > "$src/$name/manifest.json"
  # shellcheck disable=SC2016  # $0 and $* belong to the fake launcher
  printf '#!/usr/bin/env bash\necho "ran $0 $*" > "%s/ran"\n' "$root" > "$src/$name/vista"
  chmod +x "$src/$name/vista"
  if [[ "$platform" == mac-arm64 ]]; then
    mkdir -p "$src/$name/VISTA.app/Contents/MacOS"
  else
    mkdir -p "$src/$name/app/window"
    cp "$src/$name/vista" "$src/$name/app/window/vista-app"
    echo png > "$src/$name/app/window/vista.png"
  fi
  tar -czf "$dir/$name.tar.gz" -C "$src" "$name"
  ( cd "$dir" && sha256 "$name.tar.gz" > "$name.tar.gz.sha256" )
}

# A tools folder with a uname reporting the given system and machine, and an
# `open` that records its arguments.
tools() {
  local dir="$root/bin-$1-$2"
  mkdir -p "$dir"
  # shellcheck disable=SC2016  # $1 belongs to the fake uname
  printf '#!/bin/sh\ncase "$1" in -s) echo %s ;; -m) echo %s ;; *) echo %s ;; esac\n' "$1" "$2" "$1" \
    > "$dir/uname"
  # shellcheck disable=SC2016  # $* belongs to the fake open
  printf '#!/bin/sh\necho "open $*" > "%s/ran"\n' "$root" > "$dir/open"
  chmod +x "$dir/uname" "$dir/open"
  echo "$dir"
}
MAC="$(tools Darwin arm64)"
LINUX="$(tools Linux x86_64)"
AS_ROOT=false
[[ "$(id -u)" == 0 ]] && AS_ROOT=true

# Run the installer as curl | bash would, in a fresh HOME, as the given system.
run_installer() {
  local system="$1"; shift
  PATH="$system:$PATH" HOME="$root/home" VISTA_INSTALL_MIN_FREE_GB=0 \
    VISTA_INSTALL_SYSTEM_APPS="${SYSTEM_APPS:-$root/Applications}" VISTA_INSTALL_NO_REGISTER=1 \
    bash -s -- "$@" < "$installer"
}
installed() { head -1 "$1/VERSION" 2>/dev/null; }
no_work_dir() { ! ls -d "$1"/.vista-install.* >/dev/null 2>&1; }

mkdir -p "$root/home/.vista" "$root/Applications"
echo "my chats" > "$root/home/.vista/vista.db"
for v in 1.0.0 1.1.0 2.0.0 3.0.0; do
  make_release "$root/releases/$v" "$v" mac-arm64
  make_release "$root/releases/$v" "$v" linux-x86
done

# ─── macOS ────────────────────────────────────────────────────────────────────

echo "install.sh (macOS)"
mac_pkg="$root/Applications/VISTA"

out="$(VISTA_INSTALL_BASE_URL="file://$root/releases/1.0.0" run_installer "$MAC" --version 1.0.0 --no-launch 2>&1)" \
  || { echo "$out"; fail "a fresh install succeeds"; }
check "an administrator's install goes in /Applications/VISTA" test "$(installed "$mac_pkg")" = 1.0.0
check "VISTA.app sits at the package root" test -d "$mac_pkg/VISTA.app"
check "it links ~/.local/bin/vista to the launcher" \
  test "$(readlink "$root/home/.local/bin/vista")" = "$mac_pkg/vista"
check "it leaves no work directory behind" no_work_dir "$root/Applications"
check "--no-launch does not start VISTA" test ! -e "$root/ran"

out="$(VISTA_INSTALL_BASE_URL="file://$root/nowhere" run_installer "$MAC" --version v1.0.0 --no-launch 2>&1)" \
  || { echo "$out"; fail "a re-run of the installed version succeeds"; }
check "a re-run of the installed version downloads nothing" grep -q "already installed" <<<"$out"

out="$(VISTA_INSTALL_BASE_URL="file://$root/releases/1.1.0" run_installer "$MAC" --version 1.1.0 --no-launch 2>&1)" \
  || { echo "$out"; fail "an upgrade succeeds"; }
check "an upgrade replaces the package in the same folder" test "$(installed "$mac_pkg")" = 1.1.0
check "an upgrade keeps VISTA's state" grep -q "my chats" "$root/home/.vista/vista.db"
check "an upgrade leaves no work directory behind" no_work_dir "$root/Applications"

# A corrupted download: the checksum no longer matches.
cp -R "$root/releases/2.0.0" "$root/releases/bad"
echo "corrupt" >> "$root/releases/bad/vista-2.0.0-mac-arm64.tar.gz"
if out="$(VISTA_INSTALL_BASE_URL="file://$root/releases/bad" run_installer "$MAC" --version 2.0.0 --no-launch 2>&1)"; then
  fail "a checksum mismatch fails"
else
  check "a checksum mismatch fails and says so" grep -q "does not match its checksum" <<<"$out"
fi
check "a checksum mismatch keeps the installed version" test "$(installed "$mac_pkg")" = 1.1.0
check "a checksum mismatch leaves no work directory" no_work_dir "$root/Applications"

# VISTA running from the install: anything started from inside the package.
bash -c 'sleep 30; :' "$mac_pkg/VISTA.app/Contents/MacOS/VISTA" &
holder=$!
if out="$(VISTA_INSTALL_BASE_URL="file://$root/releases/2.0.0" run_installer "$MAC" --version 2.0.0 --no-launch 2>&1)"; then
  fail "an upgrade while VISTA runs is refused"
else
  check "an upgrade while VISTA runs is refused" grep -q "Close VISTA, then run this again" <<<"$out"
fi
check "a refused upgrade changes nothing" test "$(installed "$mac_pkg")" = 1.1.0
kill "$holder" 2>/dev/null; wait "$holder" 2>/dev/null || true; holder=''

# Earlier layouts: a version-named folder under ~/.local/share/vista, and the
# researcher's own ~/Applications/VISTA now that VISTA is in /Applications.
mkdir -p "$root/home/.local/share/vista/vista-0.9.0-mac-arm64" "$root/home/Applications/VISTA"
echo '{}' > "$root/home/Applications/VISTA/manifest.json"
out="$(VISTA_INSTALL_BASE_URL="file://$root/releases/2.0.0" run_installer "$MAC" --version 2.0.0 --no-launch 2>&1)" \
  || { echo "$out"; fail "an install over earlier layouts succeeds"; }
check "it removes a version-named folder from the earlier layout" \
  test ! -e "$root/home/.local/share/vista/vista-0.9.0-mac-arm64"
check "it removes the researcher's own ~/Applications/VISTA" test ! -e "$root/home/Applications/VISTA"
check "it leaves VISTA's state alone" grep -q "my chats" "$root/home/.vista/vista.db"

# Without write access to /Applications, or to another account's
# /Applications/VISTA, it goes in ~/Applications/VISTA. Root can write anywhere,
# so these need an ordinary account.
if [[ "$AS_ROOT" == true ]]; then
  skip "a standard account installs into ~/Applications/VISTA" "running as root"
  skip "another account's /Applications/VISTA is left alone" "running as root"
else
  mkdir -p "$root/locked"
  chmod 555 "$root/locked"
  out="$(SYSTEM_APPS="$root/locked" VISTA_INSTALL_BASE_URL="file://$root/releases/1.0.0" \
    run_installer "$MAC" --version 1.0.0 --no-launch 2>&1)" || { echo "$out"; fail "a standard account's install succeeds"; }
  check "a standard account installs into ~/Applications/VISTA" \
    test "$(installed "$root/home/Applications/VISTA")" = 1.0.0
  check "and says why" grep -q "cannot write to $root/locked" <<<"$out"

  mkdir -p "$root/shared/VISTA"
  echo 9.9.9 > "$root/shared/VISTA/VERSION"
  chmod 555 "$root/shared/VISTA"
  out="$(SYSTEM_APPS="$root/shared" VISTA_INSTALL_BASE_URL="file://$root/releases/1.1.0" \
    run_installer "$MAC" --version 1.1.0 --no-launch 2>&1)" || { echo "$out"; fail "an install beside another account's succeeds"; }
  check "another account's /Applications/VISTA sends the install to ~/Applications" \
    test "$(installed "$root/home/Applications/VISTA")" = 1.1.0
  check "another account's /Applications/VISTA is left alone" test "$(installed "$root/shared/VISTA")" = 9.9.9
fi

rm -f "$root/ran"
out="$(VISTA_INSTALL_BASE_URL="file://$root/releases/3.0.0" run_installer "$MAC" --version 3.0.0 2>&1)" \
  || { echo "$out"; fail "an install that launches succeeds"; }
check "without --no-launch it opens VISTA.app, not the terminal launcher" \
  grep -qx "open $mac_pkg/VISTA.app" "$root/ran"

# ─── Linux ────────────────────────────────────────────────────────────────────

echo "install.sh (Linux)"
linux_pkg="$root/home/.local/share/vista/app"
mkdir -p "$root/home/.local/share/vista/vista-0.9.0-linux-x86"

# --no-launch, since this host need not have /dev/kvm or a display.
out="$(VISTA_INSTALL_BASE_URL="file://$root/releases/1.0.0" run_installer "$LINUX" --version 1.0.0 --no-launch 2>&1)" \
  || { echo "$out"; fail "a Linux install succeeds"; }
check "it installs into ~/.local/share/vista/app" test "$(installed "$linux_pkg")" = 1.0.0
check "it removes the earlier layout's version folder" \
  test ! -e "$root/home/.local/share/vista/vista-0.9.0-linux-x86"
desktop="$root/home/.local/share/applications/vista.desktop"
check "it writes an app-menu entry that runs vista-app" \
  grep -qx "Exec=\"$linux_pkg/app/window/vista-app\"" "$desktop"
check "the entry claims the window by its class and needs no terminal" \
  bash -c "grep -qx 'StartupWMClass=vista' '$desktop' && grep -qx 'Terminal=false' '$desktop' && grep -qx 'Icon=vista' '$desktop'"
check "it installs the icon into the hicolor theme" \
  test -f "$root/home/.local/share/icons/hicolor/512x512/apps/vista.png"

out="$(VISTA_INSTALL_BASE_URL="file://$root/releases/1.1.0" run_installer "$LINUX" --version 1.1.0 --no-launch 2>&1)" \
  || { echo "$out"; fail "a Linux upgrade succeeds"; }
check "a Linux upgrade replaces the package in the same folder" test "$(installed "$linux_pkg")" = 1.1.0
check "the app-menu entry still points at it" grep -qx "Exec=\"$linux_pkg/app/window/vista-app\"" "$desktop"

# ─── either ───────────────────────────────────────────────────────────────────

# What the release workflow does with each archive (.github/scripts/package.sh).
out="$(VISTA_INSTALL_DIR="$root/scratch/app" XDG_DATA_HOME="$root/scratch/data" \
  VISTA_INSTALL_BASE_URL="file://$root/releases/1.0.0" run_installer "$LINUX" --version 1.0.0 --no-launch 2>&1)" \
  || { echo "$out"; fail "an install into VISTA_INSTALL_DIR succeeds"; }
check "VISTA_INSTALL_DIR names the package folder itself" test "$(installed "$root/scratch/app")" = 1.0.0
check "with XDG_DATA_HOME set, the app-menu entry goes there" \
  test -f "$root/scratch/data/applications/vista.desktop"

if out="$(run_installer "$(tools FreeBSD amd64)" --version 1.0.0 --no-launch 2>&1)"; then
  fail "an unsupported platform is refused"
else
  check "an unsupported platform is refused, naming what exists" grep -q "has no package for FreeBSD" <<<"$out"
fi

if out="$(run_installer "$MAC" --no-launch 2>&1)"; then
  fail "an unrendered installer without --version is refused"
else
  check "an unrendered installer without --version is refused" grep -q "no version written in" <<<"$out"
fi

# The version written in at release time.
rendered="$root/install.sh"
sed -e 's/@VISTA_VERSION@/1.0.0/' "$installer" > "$rendered"
rm -rf "$mac_pkg"
out="$(PATH="$MAC:$PATH" HOME="$root/home" VISTA_INSTALL_MIN_FREE_GB=0 VISTA_INSTALL_NO_REGISTER=1 \
  VISTA_INSTALL_SYSTEM_APPS="$root/Applications" VISTA_INSTALL_BASE_URL="file://$root/releases/1.0.0" \
  bash -s -- --no-launch < "$rendered" 2>&1)" || { echo "$out"; fail "a rendered installer succeeds"; }
check "a rendered installer installs its own version" test "$(installed "$mac_pkg")" = 1.0.0

if (( failures )); then
  echo "$failures failed"
  exit 1
fi
echo "all passed"
