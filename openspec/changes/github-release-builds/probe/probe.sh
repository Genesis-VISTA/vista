#!/usr/bin/env bash
# Report what this GitHub-hosted runner offers VISTA's release build. It changes
# nothing that matters and builds nothing. Every check is recorded, and a failing
# check never stops the rest: a negative answer is a result.
#
# Results go to the log and, as a table, to the job summary.

set -uo pipefail

MICROSANDBOX_VERSION="${MICROSANDBOX_VERSION:-0.7.2}"
SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"

case "$(uname -s)" in
  Darwin) OS=macos ;;
  Linux) OS=linux ;;
  MINGW* | MSYS*) OS=windows ;;
  *) OS="$(uname -s)" ;;
esac

{
  echo "## $OS · $(uname -m) · ${RUNNER_NAME:-local}"
  echo
  echo "| check | exit | first line of output |"
  echo "|---|---|---|"
} >> "$SUMMARY"

# probe <label> <command...>: run it, show all output in the log, and one line
# in the summary.
probe() {
  local label="$1"; shift
  local out code
  echo "::group::$label"
  out="$("$@" 2>&1)"
  code=$?
  printf '%s\n' "$out"
  echo "exit: $code"
  echo "::endgroup::"
  local first
  first="$(printf '%s\n' "$out" | grep -m1 -v '^[[:space:]]*$' | tr '|' '/' | cut -c1-140)"
  echo "| $label | $code | \`${first:-}\` |" >> "$SUMMARY"
  return 0
}

# ─── host ───────────────────────────────────────────────────────────────────

probe "uname" uname -a
case "$OS" in
  linux)
    probe "os-release" bash -c '. /etc/os-release && echo "$PRETTY_NAME"'
    probe "glibc" bash -c 'ldd --version | head -1'
    ;;
  macos)
    probe "sw_vers" sw_vers -productVersion
    probe "cpu" sysctl -n machdep.cpu.brand_string
    probe "kern.hv_support" sysctl -n kern.hv_support
    ;;
  windows)
    probe "windows version" cmd.exe //c ver
    probe "LongPathsEnabled" powershell.exe -NoProfile -Command \
      "(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem').LongPathsEnabled"
    probe "HypervisorPlatform feature" powershell.exe -NoProfile -Command \
      "(Get-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform).State"
    probe "git bash" bash --version
    ;;
esac

probe "disk free (here)" bash -c 'df -h . | tail -1'

# ─── virtualisation ─────────────────────────────────────────────────────────

if [[ "$OS" == linux ]]; then
  probe "/dev/kvm before udev rule" ls -l /dev/kvm
  # The rule the release workflow will use, so its effect is what gets measured.
  echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' \
    | sudo tee /etc/udev/rules.d/99-kvm4all.rules >/dev/null
  sudo udevadm control --reload-rules
  sudo udevadm trigger --name-match=kvm
  probe "/dev/kvm after udev rule" ls -l /dev/kvm
  probe "/dev/kvm read+write" bash -c '[[ -r /dev/kvm && -w /dev/kvm ]] && echo yes || { echo no; exit 1; }'
  probe "docker buildx" docker buildx version
fi

# ─── the sandbox runtime VISTA bundles ──────────────────────────────────────

target="${RUNNER_TEMP:-/tmp}/msb-probe"
# The workflow installs the Python VISTA pins (3.14); microsandbox's wheels need
# 3.10 or newer. On Windows `python3` can be the Microsoft Store stub, so the
# default there is `python`. PY overrides it, e.g. for a local run.
if [[ -z "${PY:-}" ]]; then
  PY=python3
  [[ "$OS" == windows ]] && PY=python
fi
probe "python" "$PY" --version
probe "pip install microsandbox==$MICROSANDBOX_VERSION" \
  "$PY" -m pip install --quiet --disable-pip-version-check --target "$target" \
  "microsandbox==$MICROSANDBOX_VERSION"
msb="$(find "$target" -path '*microsandbox/_bundled/bin/msb*' -type f 2>/dev/null | head -1)"
if [[ -n "$msb" ]]; then
  chmod +x "$msb" 2>/dev/null || true
  # An isolated MSB_HOME, so the probe never touches a real ~/.microsandbox. Kept
  # short under /tmp, because msb derives Unix socket paths from it and macOS caps
  # those at 104 bytes.
  MSB_HOME="$(mktemp -d /tmp/msb.XXXXXX)"
  export MSB_HOME
  probe "msb --version" "$msb" --version
  # doctor's verdict is its last line ("Host setup is ready."), and on macOS it
  # checks binaries and architecture but not the hypervisor itself.
  probe "msb doctor (verdict)" bash -c '"$0" doctor 2>&1 | tail -1; exit "${PIPESTATUS[0]}"' "$msb"
  # The decisive check: boot a real microVM. Bounded, because a host without a
  # usable hypervisor may hang rather than fail. perl's alarm is the portable
  # timeout: macOS has no `timeout`.
  probe "msb run alpine (boots a microVM)" \
    perl -e 'alarm shift; exec @ARGV or die "exec: $!"' 180 \
    "$msb" run alpine -- echo microvm-ok
else
  echo "| msb | - | \`no bundled msb found under $target\` |" >> "$SUMMARY"
fi

# ─── network ────────────────────────────────────────────────────────────────

for url in \
  https://huggingface.co/api/models/microsoft/harrier-oss-v1-270m \
  https://pypi.org/simple/ \
  https://nodejs.org/dist/index.json \
  https://github.com/electron/electron/releases \
  https://gitlab.com/ \
  https://code.ornl.gov/; do
  probe "reach $url" curl -sS -o /dev/null -m 20 -w '%{http_code}\n' "$url"
done
