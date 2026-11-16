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
# file, and unpacks it into one fixed folder (desktop-app-startup design D6):
#
#   macOS  /Applications/VISTA, or ~/Applications/VISTA where that cannot be
#          written (a standard account, or another account's install)
#   Linux  ~/.local/share/vista/app, with VISTA in the app menu
#
# It links ~/.local/bin/vista to the package's terminal launcher, removes what
# earlier install layouts left, and opens the VISTA application. Run it again to
# start the installed copy, which downloads nothing, or to upgrade; it refuses
# while VISTA is running. VISTA's state (VISTA_HOME, ~/.vista by default) lives
# outside the install and is never touched.
#
# Environment:
#   VISTA_INSTALL_DIR       the package folder, instead of the one above
#   VISTA_BIN_DIR           where the `vista` link goes (default: ~/.local/bin)
#   VISTA_INSTALL_BASE_URL  where the archives are, instead of the release's
#                           download URL; a file:// URL works. For testing.
#   VISTA_INSTALL_MIN_FREE_GB  free disk to insist on (default: 7)
#   VISTA_INSTALL_SYSTEM_APPS  the system Applications folder (default:
#                           /Applications). For testing.
#   VISTA_INSTALL_NO_REGISTER  1 to skip registering the app with macOS's
#                           LaunchServices and Spotlight. For testing.

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
                   || echo "see https://github.com/Genesis-VISTA/vista/blob/main/docs/installing.md"
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
  local platform os
  case "$(uname -s)/$(uname -m)" in
    Darwin/arm64) platform=mac-arm64; os=macos ;;
    Linux/x86_64) platform=linux-x86; os=linux ;;
    MINGW*|MSYS*|CYGWIN*)
      die "this is the macOS and Linux installer. On Windows, run in PowerShell:
  powershell -ExecutionPolicy Bypass -c \"irm $repo_url/releases/download/v$version/install.ps1 | iex\"" ;;
    *) die "VISTA has no package for $(uname -s) on $(uname -m). Packages exist for macOS on Apple Silicon, Linux on x86-64 and Windows on x64." ;;
  esac
  local name="vista-$version-$platform"
  local archive="$name.tar.gz"

  # ─── where it goes ──────────────────────────────────────────────────────
  local data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  local bin_dir="${VISTA_BIN_DIR:-$HOME/.local/bin}"
  local system_apps="${VISTA_INSTALL_SYSTEM_APPS:-/Applications}"
  local package
  if [[ -n "${VISTA_INSTALL_DIR:-}" ]]; then
    package="$VISTA_INSTALL_DIR"
  elif [[ "$os" == linux ]]; then
    package="$data_home/vista/app"
  else
    package="$(mac_install_folder "$system_apps")"
  fi

  # Folders an earlier layout left: each version in its own folder under
  # ~/.local/share/vista, and on macOS a ~/Applications install once VISTA lives
  # in /Applications. Never another account's /Applications/VISTA.
  local leftovers=() old
  for old in "$data_home"/vista/vista-*-"$platform"; do
    [[ -d "$old" ]] && leftovers+=("$old")
  done
  if [[ "$os" == macos && "$package" == "$system_apps/VISTA" \
        && -f "$HOME/Applications/VISTA/manifest.json" ]]; then
    leftovers+=("$HOME/Applications/VISTA")
  fi

  # ─── already installed ──────────────────────────────────────────────────
  # A build from an untagged commit records `0.0.0+g<sha>`; its file names drop
  # the `+g<sha>`.
  local installed_version
  installed_version="$(head -1 "$package/VERSION" 2>/dev/null || true)"
  if [[ -x "$package/vista" && "${installed_version%%+*}" == "$version" ]]; then
    say "VISTA $version is already installed in $package"
    link_launcher "$package" "$bin_dir"
    integrate "$os" "$package" "$data_home"
    finish "$os" "$package" "$launch"
    return 0
  fi

  # ─── before downloading ─────────────────────────────────────────────────
  # D11: replacing files a running VISTA uses can crash it. Its window, its
  # launcher and every service run from the package folder, so a process
  # started from there, by anyone, means VISTA is open. VISTA's port is not
  # asked: a development checkout answers on it too.
  refuse_if_running "$package" ${leftovers[@]+"${leftovers[@]}"}

  # Each of these would stop VISTA from running, and the download is 1.7 GB.
  if [[ "$os" == linux ]]; then
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

  local parent
  parent="$(dirname "$package")"
  mkdir -p "$parent"
  local need_gb="${VISTA_INSTALL_MIN_FREE_GB:-7}"
  local free_kb
  free_kb="$(df -Pk "$parent" | awk 'NR == 2 { print $4 }')"
  if (( free_kb < need_gb * 1024 * 1024 )); then
    die "VISTA needs about $need_gb GB free in $parent, and there is $(( free_kb / 1024 / 1024 )) GB. Free some space, or set VISTA_INSTALL_DIR to a disk with more room."
  fi

  # ─── download and check ─────────────────────────────────────────────────
  # The work directory sits beside the install, so the final move is a rename
  # on one filesystem, and an interrupted install leaves nothing where the
  # next run looks.
  local base="${VISTA_INSTALL_BASE_URL:-$repo_url/releases/download/v$version}"
  WORK="$(mktemp -d "$parent/.vista-install.XXXXXX")"
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
  say "unpacking into $package"
  mkdir "$work/unpacked"
  tar -xf "$work/$archive" -C "$work/unpacked"
  rm -f "$work/$archive"
  [[ -x "$work/unpacked/$name/vista" ]] \
    || die "$archive does not hold $name/vista, so nothing was installed"

  # The download took a while: ask again just before anything is replaced.
  refuse_if_running "$package" ${leftovers[@]+"${leftovers[@]}"}

  # The previous version goes only once the new one is ready to take its place.
  if [[ -e "$package" ]]; then
    mv "$package" "$work/previous" \
      || die "could not move the installed VISTA out of $package, so nothing was changed"
  fi
  if ! mv "$work/unpacked/$name" "$package"; then
    [[ -e "$work/previous" ]] && mv "$work/previous" "$package"
    die "could not move the new version into $package; the previous one was kept"
  fi
  [[ -e "$work/previous" ]] && remove_folder "$work/previous"

  for old in ${leftovers[@]+"${leftovers[@]}"}; do
    say "removing an earlier install, $old"
    remove_folder "$old"
  done

  say "installed VISTA $version in $package"
  link_launcher "$package" "$bin_dir"
  integrate "$os" "$package" "$data_home"
  finish "$os" "$package" "$launch"
}

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# D6: /Applications/VISTA, where Finder's Applications shows it, when this
# account can write there. An existing one decides by itself: another
# administrator's install is theirs (755), and cannot be upgraded from here.
mac_install_folder() {
  local system_apps="$1"
  if [[ -d "$system_apps/VISTA" ]]; then
    if [[ -w "$system_apps/VISTA" ]]; then
      echo "$system_apps/VISTA"
      return
    fi
    warn "$system_apps/VISTA belongs to another account, so VISTA goes in ~/Applications/VISTA for you"
  elif [[ -w "$system_apps" ]]; then
    echo "$system_apps/VISTA"
    return
  else
    warn "this account cannot write to $system_apps, so VISTA goes in ~/Applications/VISTA, where Spotlight and the Apps view still find it"
  fi
  echo "$HOME/Applications/VISTA"
}

# Stops before anything changes when a process was started from any of the
# folders given.
refuse_if_running() {
  local dir
  for dir in "$@"; do
    [[ -e "$dir" ]] || continue
    if pgrep -f -- "$dir/" >/dev/null 2>&1; then
      die "VISTA is running from $dir. Close VISTA, then run this again."
    fi
  done
}

# Finder can write a .DS_Store into a folder open in a window while it is being
# removed, which fails the removal with "Directory not empty". One retry clears
# that; anything still left is reported, not fatal, since the install is done.
remove_folder() {
  rm -rf "$1" 2>/dev/null && return 0
  sleep 1
  rm -rf "$1" 2>/dev/null && return 0
  warn "could not remove all of $1; delete what is left by hand"
}

# `vista` on PATH. The launcher resolves its own location through the link.
link_launcher() {
  local package="$1" bin_dir="$2"
  mkdir -p "$bin_dir"
  ln -sfn "$package/vista" "$bin_dir/vista"
}

# Where a researcher finds VISTA afterwards. macOS: registered with
# LaunchServices and Spotlight, which otherwise index a new or renamed app only
# after a while. Linux: an app-menu entry and its icon (design D9).
integrate() {
  local os="$1" package="$2" data_home="$3"
  if [[ "$os" == macos ]]; then
    [[ -d "$package/VISTA.app" ]] || return 0
    if [[ "${VISTA_INSTALL_NO_REGISTER:-}" != 1 ]]; then
      local lsregister=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
      [[ -x "$lsregister" ]] && "$lsregister" -f "$package/VISTA.app" >/dev/null 2>&1 || true
      command -v mdimport >/dev/null 2>&1 && mdimport "$package/VISTA.app" >/dev/null 2>&1 || true
    fi
    say "open VISTA from Spotlight, Launchpad or Finder's Applications ($package)"
    return 0
  fi
  local vista_app="$package/app/window/vista-app"
  if [[ ! -x "$vista_app" ]]; then
    warn "this package has no app-menu entry point; start it with $package/vista"
    return 0
  fi
  local applications="$data_home/applications" icons="$data_home/icons/hicolor/512x512/apps"
  mkdir -p "$applications" "$icons"
  [[ -f "$package/app/window/vista.png" ]] && cp "$package/app/window/vista.png" "$icons/vista.png"
  # StartupWMClass is the window's measured X11 class; on Wayland the window
  # names this entry itself, through desktopName in electron/package.json.
  cat > "$applications/vista.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=VISTA
GenericName=Scientific assistant
Comment=Visual Intelligence for Scientific & Tooling Assistant
Exec="$vista_app"
Icon=vista
Terminal=false
Categories=Science;Education;
StartupNotify=true
StartupWMClass=vista
EOF
  command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$applications" >/dev/null 2>&1 || true
  touch "$data_home/icons/hicolor" 2>/dev/null || true
  say "open VISTA from your app menu"
}

# Open the VISTA application, not the terminal launcher (design D11): it shows
# its own startup and keeps running once this terminal is closed.
finish() {
  local os="$1" package="$2" launch="$3"
  local bin="${VISTA_BIN_DIR:-$HOME/.local/bin}"
  case ":$PATH:" in
    *":$bin:"*) say "for diagnostics, run: vista" ;;
    *) say "for diagnostics, run: $bin/vista" ;;
  esac
  (( launch )) || return 0
  say "starting VISTA"
  [[ -n "${WORK:-}" ]] && rm -rf "$WORK"
  trap - EXIT
  if [[ "$os" == macos && -d "$package/VISTA.app" ]]; then
    exec open "$package/VISTA.app"
  elif [[ "$os" == linux && -x "$package/app/window/vista-app" ]]; then
    # Its own session, so closing this terminal does not take VISTA with it.
    if command -v setsid >/dev/null 2>&1; then
      setsid "$package/app/window/vista-app" < /dev/null > /dev/null 2>&1 &
    else
      nohup "$package/app/window/vista-app" < /dev/null > /dev/null 2>&1 &
    fi
    return 0
  fi
  # An older release with no application entry point: its terminal launcher,
  # with the terminal as its input rather than the pipe this script came in on.
  if [[ -r /dev/tty ]] && { : < /dev/tty; } 2>/dev/null; then
    exec "$package/vista" < /dev/tty
  fi
  exec "$package/vista"
}

main "$@"
