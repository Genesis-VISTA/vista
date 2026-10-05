#!/usr/bin/env bash
# Install and start VISTA on macOS (Apple Silicon) or Linux (x86-64), from a
# GitHub release. Each release carries its own copy of this script, with its
# version written in, so the newest release installs with:
#
#   curl -fsSL https://github.com/Genesis-VISTA/vista/releases/latest/download/install.sh | bash
#
# and a given one, prereleases included, from releases/download/<tag>/install.sh.
# Options go after `bash -s --`:
#
#   --no-launch        install, but don't start VISTA
#   --version <ver>    install another release, e.g. 0.2.0 or v0.2.0-rc1
#   --help             show this
#
# It downloads the package for this machine, checks it against its .sha256
# file, unpacks it into ~/.local/share/vista, links ~/.local/bin/vista to its
# launcher, removes any older version, and starts it. Run it again to start the
# installed copy, which downloads nothing, or to upgrade. VISTA's state
# (VISTA_HOME, ~/.vista by default) lives outside the install and is never
# touched.
#
# Environment:
#   VISTA_INSTALL_DIR       where packages go (default: ~/.local/share/vista)
#   VISTA_BIN_DIR           where the `vista` link goes (default: ~/.local/bin)
#   VISTA_INSTALL_BASE_URL  where the archives are, instead of the release's
#                           download URL; a file:// URL works. For testing.
#   VISTA_INSTALL_MIN_FREE_GB  free disk to insist on (default: 7)

# The whole script is one function, called on the last line, so a download that
# stops part way runs nothing.
WORK=''
main() {
  set -euo pipefail

  # Written in by .github/scripts/render-installers.sh at release time.
  local release_version='@VISTA_VERSION@'
  local repo_url='@VISTA_REPO_URL@'
  [[ "$repo_url" == @*@ ]] && repo_url='https://github.com/Genesis-VISTA/vista'

  local version="" launch=1
  while (( $# )); do
    case "$1" in
      --no-launch) launch=0 ;;
      --version) [[ $# -ge 2 ]] || die "--version needs a value"; version="${2#v}"; shift ;;
      --version=*) version="${1#--version=}"; version="${version#v}" ;;
      -h|--help) sed -n '2,/^$/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}" 2>/dev/null \
                   || echo "see https://github.com/Genesis-VISTA/vista#running-a-prebuilt-package"
                 return 0 ;;
      *) die "unknown option: $1 (try --help)" ;;
    esac
    shift
  done
  if [[ -z "$version" ]]; then
    [[ "$release_version" != @*@ ]] \
      || die "this copy of the installer has no version written in; pass --version <ver>"
    version="$release_version"
  fi

  # ─── this machine ───────────────────────────────────────────────────────
  local platform ext
  case "$(uname -s)/$(uname -m)" in
    Darwin/arm64) platform=mac-arm64 ;;
    Linux/x86_64) platform=linux-x86 ;;
    MINGW*|MSYS*|CYGWIN*)
      die "this is the macOS and Linux installer. On Windows, run in PowerShell:
  powershell -ExecutionPolicy Bypass -c \"irm $repo_url/releases/download/v$version/install.ps1 | iex\"" ;;
    *) die "VISTA has no package for $(uname -s) on $(uname -m). Packages exist for macOS on Apple Silicon, Linux on x86-64 and Windows on x64." ;;
  esac
  ext=tar.gz
  local name="vista-$version-$platform"
  local archive="$name.$ext"

  local install_dir="${VISTA_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/vista}"
  local bin_dir="${VISTA_BIN_DIR:-$HOME/.local/bin}"
  local package="$install_dir/$name"

  # ─── already installed ──────────────────────────────────────────────────
  # A build from an untagged commit records `0.0.0+g<sha>`; its file names drop
  # the `+g<sha>`.
  local installed_version
  installed_version="$(head -1 "$package/VERSION" 2>/dev/null || true)"
  if [[ -x "$package/vista" && "${installed_version%%+*}" == "$version" ]]; then
    say "VISTA $version is already installed in $package"
    link_launcher "$package" "$bin_dir"
    finish "$package" "$launch"
    return 0
  fi

  # ─── before downloading ─────────────────────────────────────────────────
  # Each of these would stop VISTA from running, and the download is 1.7 GB.
  if [[ "$platform" == linux-x86 ]]; then
    local kvm_problem=''
    if [[ ! -e /dev/kvm ]]; then
      kvm_problem="this machine has no /dev/kvm. On bare metal, enable virtualisation (VT-x or AMD-V) in the firmware. Inside a virtual machine, the host has to expose nested virtualisation to it."
    elif [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
      kvm_problem="this account cannot use /dev/kvm. Add yourself to the kvm group with \`sudo usermod -aG kvm ${USER:-$(id -un)}\`, then log out and back in."
    fi
    if [[ -n "$kvm_problem" ]]; then
      (( launch )) && die "VISTA needs hardware virtualisation on Linux, and $kvm_problem"
      warn "VISTA will not start until this is fixed: $kvm_problem"
    fi
    if (( launch )) && [[ -z "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]]; then
      die "VISTA opens in a window, and there is no graphical display here (neither DISPLAY nor WAYLAND_DISPLAY is set). Run this from a desktop session, or add --no-launch to install only."
    fi
  fi

  mkdir -p "$install_dir"
  local need_gb="${VISTA_INSTALL_MIN_FREE_GB:-7}"
  local free_kb
  free_kb="$(df -Pk "$install_dir" | awk 'NR == 2 { print $4 }')"
  if (( free_kb < need_gb * 1024 * 1024 )); then
    die "VISTA needs about $need_gb GB free in $install_dir, and there is $(( free_kb / 1024 / 1024 )) GB. Free some space, or set VISTA_INSTALL_DIR to a disk with more room."
  fi

  # ─── download and check ─────────────────────────────────────────────────
  # The work directory sits beside the install, so the final move is a rename
  # on one filesystem, and an interrupted install leaves nothing where the
  # next run looks.
  local base="${VISTA_INSTALL_BASE_URL:-$repo_url/releases/download/v$version}"
  WORK="$(mktemp -d "$install_dir/.install.XXXXXX")"
  trap 'rm -rf "$WORK"' EXIT
  local work="$WORK"

  say "downloading VISTA $version for $platform"
  curl -fL --progress-bar -o "$work/$archive" "$base/$archive" \
    || die "could not download $base/$archive"
  curl -fsSL -o "$work/$archive.sha256" "$base/$archive.sha256" \
    || die "could not download $base/$archive.sha256"

  say "checking the download"
  local sha256=(sha256sum)
  command -v sha256sum >/dev/null 2>&1 || sha256=(shasum -a 256)
  ( cd "$work" && "${sha256[@]}" -c "$archive.sha256" >/dev/null ) \
    || die "$archive does not match its checksum, so nothing was installed. Run the command again; if it fails the same way, report it."

  # ─── unpack ─────────────────────────────────────────────────────────────
  say "unpacking into $install_dir"
  mkdir "$work/unpacked"
  tar -xf "$work/$archive" -C "$work/unpacked"
  rm -f "$work/$archive"
  [[ -x "$work/unpacked/$name/vista" ]] \
    || die "$archive does not hold $name/vista, so nothing was installed"
  rm -rf "$package"
  mv "$work/unpacked/$name" "$package"

  # Older versions for this platform. Only after the new one is in place.
  local old
  for old in "$install_dir"/vista-*-"$platform"; do
    [[ -d "$old" && "$old" != "$package" ]] || continue
    say "removing the previous version, $(basename "$old")"
    rm -rf "$old"
  done

  link_launcher "$package" "$bin_dir"
  say "installed VISTA $version"
  finish "$package" "$launch"
}

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# `vista` on PATH. The launcher resolves its own location through the link.
link_launcher() {
  local package="$1" bin_dir="$2"
  mkdir -p "$bin_dir"
  ln -sfn "$package/vista" "$bin_dir/vista"
  case ":$PATH:" in
    *":$bin_dir:"*) say "start it later with: vista" ;;
    *) say "start it later with: $bin_dir/vista"
       say "or add $bin_dir to your PATH to start it with just: vista" ;;
  esac
}

# Start VISTA in place of this script. Its standard input is the pipe this
# script arrived through, so the launcher gets the terminal instead.
finish() {
  local package="$1" launch="$2"
  (( launch )) || return 0
  say "starting VISTA"
  [[ -n "${WORK:-}" ]] && rm -rf "$WORK"
  trap - EXIT
  if [[ -r /dev/tty ]] && { : < /dev/tty; } 2>/dev/null; then
    exec "$package/vista" < /dev/tty
  fi
  exec "$package/vista"
}

main "$@"
