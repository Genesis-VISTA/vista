#!/usr/bin/env bash
# Hermetic tests for scripts/install.sh, against fake packages served from a
# file:// URL. Needs bash, curl, tar and sha256sum or shasum; no network and no
# real package.
#
#   scripts/test_install.sh

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
installer="$here/install.sh"
root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT

failures=0
pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; failures=$((failures + 1)); }
check() { local what="$1"; shift; if "$@"; then pass "$what"; else fail "$what"; fi; }

sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

# A fake package: VERSION, and a `vista` that records that it ran.
make_release() {
  local dir="$1" version="$2" platform="$3"
  local name="vista-$version-$platform" src="$root/src/$version-$platform"
  mkdir -p "$src/$name" "$dir"
  echo "$version" > "$src/$name/VERSION"
  # shellcheck disable=SC2016  # $0 and $* belong to the fake launcher
  printf '#!/usr/bin/env bash\necho "ran $0 $*" > "%s/ran"\n' "$root" > "$src/$name/vista"
  chmod +x "$src/$name/vista"
  tar -czf "$dir/$name.tar.gz" -C "$src" "$name"
  ( cd "$dir" && sha256 "$name.tar.gz" > "$name.tar.gz.sha256" )
}

# A uname that reports the given system and machine.
fake_uname() {
  mkdir -p "$root/bin-$1-$2"
  # shellcheck disable=SC2016  # $1 belongs to the fake uname
  printf '#!/bin/sh\ncase "$1" in -s) echo %s ;; -m) echo %s ;; *) echo %s ;; esac\n' "$1" "$2" "$1" \
    > "$root/bin-$1-$2/uname"
  chmod +x "$root/bin-$1-$2/uname"
  echo "$root/bin-$1-$2"
}

# Run the installer as curl | bash would, in a fresh HOME.
run_installer() {
  HOME="$root/home" VISTA_INSTALL_MIN_FREE_GB=0 bash -s -- "$@" < "$installer"
}

case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) platform=mac-arm64 ;;
  Linux/x86_64) platform=linux-x86 ;;
  *) echo "these tests run on macOS arm64 or Linux x86-64"; exit 1 ;;
esac
install_dir="$root/home/.local/share/vista"
mkdir -p "$root/home/.vista"
echo "my chats" > "$root/home/.vista/vista.db"
make_release "$root/releases/1.0.0" 1.0.0 "$platform"
make_release "$root/releases/1.1.0" 1.1.0 "$platform"
make_release "$root/releases/2.0.0" 2.0.0 "$platform"
make_release "$root/releases/mac" 3.0.0 mac-arm64

echo "install.sh ($platform)"

out="$(VISTA_INSTALL_BASE_URL="file://$root/releases/1.0.0" run_installer --version 1.0.0 --no-launch 2>&1)" \
  || { echo "$out"; fail "a fresh install succeeds"; }
check "a fresh install unpacks the package" test -x "$install_dir/vista-1.0.0-$platform/vista"
check "it links ~/.local/bin/vista to the launcher" \
  test "$(readlink "$root/home/.local/bin/vista")" = "$install_dir/vista-1.0.0-$platform/vista"
check "it leaves no work directory behind" bash -c "! ls -d '$install_dir'/.install.* 2>/dev/null"
check "--no-launch does not start VISTA" test ! -e "$root/ran"

out="$(VISTA_INSTALL_BASE_URL="file://$root/nowhere" run_installer --version v1.0.0 --no-launch 2>&1)" \
  || { echo "$out"; fail "a re-run of the installed version succeeds"; }
check "a re-run of the installed version downloads nothing" grep -q "already installed" <<<"$out"

out="$(VISTA_INSTALL_BASE_URL="file://$root/releases/1.1.0" run_installer --version 1.1.0 --no-launch 2>&1)" \
  || { echo "$out"; fail "an upgrade succeeds"; }
check "an upgrade installs the new version" test -x "$install_dir/vista-1.1.0-$platform/vista"
check "an upgrade removes the old version" test ! -e "$install_dir/vista-1.0.0-$platform"
check "an upgrade keeps VISTA's state" grep -q "my chats" "$root/home/.vista/vista.db"
check "the link follows the upgrade" \
  test "$(readlink "$root/home/.local/bin/vista")" = "$install_dir/vista-1.1.0-$platform/vista"

# A corrupted download: the checksum no longer matches.
cp -R "$root/releases/2.0.0" "$root/releases/bad"
echo "corrupt" >> "$root/releases/bad/vista-2.0.0-$platform.tar.gz"
if out="$(VISTA_INSTALL_BASE_URL="file://$root/releases/bad" run_installer --version 2.0.0 --no-launch 2>&1)"; then
  fail "a checksum mismatch fails"
else
  check "a checksum mismatch fails and says so" grep -q "does not match its checksum" <<<"$out"
fi
check "a checksum mismatch installs nothing" test ! -e "$install_dir/vista-2.0.0-$platform"
check "a checksum mismatch keeps the installed version" test -x "$install_dir/vista-1.1.0-$platform/vista"
check "a checksum mismatch leaves no work directory" bash -c "! ls -d '$install_dir'/.install.* 2>/dev/null"

if out="$(PATH="$(fake_uname FreeBSD amd64):$PATH" run_installer --version 1.0.0 --no-launch 2>&1)"; then
  fail "an unsupported platform is refused"
else
  check "an unsupported platform is refused, naming what exists" grep -q "has no package for FreeBSD" <<<"$out"
fi

if out="$(run_installer --no-launch 2>&1)"; then
  fail "an unrendered installer without --version is refused"
else
  check "an unrendered installer without --version is refused" grep -q "no version written in" <<<"$out"
fi

# Starting VISTA. Pretends to be a Mac, which needs no /dev/kvm or display.
rm -f "$root/ran"
out="$(PATH="$(fake_uname Darwin arm64):$PATH" VISTA_INSTALL_BASE_URL="file://$root/releases/mac" \
  run_installer --version 3.0.0 2>&1)" || { echo "$out"; fail "an install that launches succeeds"; }
check "without --no-launch it starts the launcher" grep -q "vista-3.0.0-mac-arm64/vista" "$root/ran"

# The version written in at release time.
rendered="$root/install.sh"
sed -e 's/@VISTA_VERSION@/1.0.0/' "$installer" > "$rendered"
rm -rf "$install_dir"
out="$(HOME="$root/home" VISTA_INSTALL_MIN_FREE_GB=0 VISTA_INSTALL_BASE_URL="file://$root/releases/1.0.0" \
  bash -s -- --no-launch < "$rendered" 2>&1)" || { echo "$out"; fail "a rendered installer succeeds"; }
check "a rendered installer installs its own version" test -x "$install_dir/vista-1.0.0-$platform/vista"

if (( failures )); then
  echo "$failures failed"
  exit 1
fi
echo "all passed"
