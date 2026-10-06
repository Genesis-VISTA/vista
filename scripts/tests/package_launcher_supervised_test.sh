#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d /tmp/vista-launcher-test.XXXXXX)"
LAUNCHER_PID=''
PARENT_PIPE_OPEN=false

stop_fake_services() {
  local pid_file pid
  for pid_file in "$TMP"/service-pids/*.pid; do
    [[ -e "$pid_file" ]] || continue
    pid="$(cat "$pid_file")"
    kill -TERM "$pid" 2>/dev/null || true
  done
}

cleanup() {
  if [[ "$PARENT_PIPE_OPEN" == true ]]; then
    exec 9>&-
  fi
  if [[ -n "$LAUNCHER_PID" ]]; then
    kill -TERM "$LAUNCHER_PID" 2>/dev/null || true
    wait "$LAUNCHER_PID" 2>/dev/null || true
  fi
  stop_fake_services
  rm -rf "$TMP"
}
trap cleanup EXIT

PACKAGE="$TMP/package"
TOOLS="$TMP/tools"
STATE="$TMP/state"
mkdir -p \
  "$PACKAGE/app/mcp_servers/vista_mcp_server/.venv/bin" \
  "$PACKAGE/app/mcp_servers/dev_mcp_server/.venv/lib/microsandbox/_bundled/bin" \
  "$PACKAGE/app/backend/.venv/bin" \
  "$PACKAGE/app/ui" \
  "$PACKAGE/node/bin" \
  "$PACKAGE/bin" \
  "$TOOLS" \
  "$TMP/service-pids"

cp "$ROOT/scripts/package_launcher.sh" "$PACKAGE/vista"
chmod +x "$PACKAGE/vista"
printf '%s\n' 'test-version' > "$PACKAGE/VERSION"
printf '%s\n' '{"os": "macos", "arch": "arm64", "exe": "missing-window"}' \
  > "$PACKAGE/manifest.json"
: > "$PACKAGE/app/ui/server.js"

cat > "$TOOLS/uname" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  -s) printf '%s\n' "${TEST_UNAME_S:-Darwin}" ;;
  -m) printf '%s\n' arm64 ;;
  *) exec /usr/bin/uname "$@" ;;
esac
EOF

cat > "$PACKAGE/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

for service in \
  "$PACKAGE/app/mcp_servers/vista_mcp_server/.venv/bin/vista-mcp-server" \
  "$PACKAGE/app/backend/.venv/bin/vista-backend" \
  "$PACKAGE/node/bin/node"; do
  cat > "$service" <<'EOF'
#!/usr/bin/env bash
PID_FILE="$TEST_SERVICE_PIDS/${0##*/}.pid"
printf '%s\n' "$$" > "$PID_FILE"
exec perl -e '
  my $marker = shift;
  my $stop = sub {
    open my $fh, ">", $marker or die "could not write $marker: $!";
    close $fh;
    exit 0;
  };
  $SIG{TERM} = $SIG{INT} = $SIG{HUP} = $stop;
  $SIG{USR1} = sub {
    open my $fh, ">", "$marker.alive" or die "could not write alive marker: $!";
    close $fh;
  };
  open my $ready, ">", "$marker.ready" or die "could not write ready marker: $!";
  close $ready;
  sleep 1 while 1;
' "$PID_FILE.stopped"
EOF
  chmod +x "$service"
done
chmod +x "$TOOLS/uname" "$PACKAGE/bin/curl"

cat > "$PACKAGE/app/mcp_servers/dev_mcp_server/.venv/lib/microsandbox/_bundled/bin/msb" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == image && "${2:-}" == inspect ]]; then
  [[ "${TEST_IMAGE_PRESENT:-1}" == 1 ]]
  exit
fi
if [[ "${1:-}" == load ]]; then
  [[ "${TEST_MSB_IMPORT_FAIL:-0}" != 1 ]]
  exit
fi
exit 0
EOF
chmod +x "$PACKAGE/app/mcp_servers/dev_mcp_server/.venv/lib/microsandbox/_bundled/bin/msb"

expect_failure() {
  local expected="$1"
  shift
  local output status=0
  output="$("$@" 2>&1)" || status=$?
  if [[ "$status" == 0 || "$output" != *"$expected"* ]]; then
    printf 'expected failure containing %q, got status %s:\n%s\n' \
      "$expected" "$status" "$output" >&2
    exit 1
  fi
}

expect_fake_services_stopped() {
  local pid_file pid
  for pid_file in "$TMP"/service-pids/*.pid; do
    [[ -e "$pid_file" ]] || continue
    pid="$(cat "$pid_file")"
    if [[ ! -e "$pid_file.stopped" ]]; then
      kill -USR1 "$pid" 2>/dev/null || continue
      sleep 0.05
    fi
    if [[ -e "$pid_file.stopped.alive" ]]; then
      printf 'fake service %s did not receive launcher cleanup\n' \
        "${pid_file##*/}" >&2
      exit 1
    fi
  done
}

wait_for_fake_services() {
  local i ready
  for (( i = 0; i < 100; i++ )); do
    ready=0
    for marker in "$TMP"/service-pids/*.pid.stopped.ready; do
      [[ -e "$marker" ]] && ready=$((ready + 1))
    done
    (( ready == 3 )) && return 0
    sleep 0.05
  done
  printf 'fake services did not install their signal handlers\n' >&2
  exit 1
}

expect_failure "--supervised requires --progress=jsonl" \
  env PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" "$PACKAGE/vista" --supervised
expect_failure "--progress=jsonl requires --supervised" \
  env PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" "$PACKAGE/vista" --progress=jsonl

# Every supervised scenario, run once per platform that has the bash mode.
# Only `uname` is faked, so the Linux pass runs the Linux branches of the
# launcher on whatever host runs the test. A Mac host has no /dev/kvm, so that
# pass skips the KVM check the way the build's smoke test does; the check's
# own failure is covered below.
supervised_suite() {
  local os="$1"
  PLATFORM_ENV=(TEST_UNAME_S="$2")
  [[ "$os" == linux ]] && PLATFORM_ENV+=(VISTA_VERIFY_WITHOUT_SANDBOX=1)
  printf '{"os": "%s", "arch": "arm64", "exe": "missing-window"}\n' "$os" \
    > "$PACKAGE/manifest.json"
  stop_fake_services
  rm -f "$TMP"/service-pids/*.pid "$TMP"/service-pids/*.stopped \
    "$TMP"/service-pids/*.alive "$TMP"/service-pids/*.ready

  mkdir -p "$PACKAGE/payload"
  tar -cf "$PACKAGE/payload/payload.tar" -T /dev/null
  : > "$PACKAGE/payload/parts.txt"
  : > "$PACKAGE/payload/sandbox-image.tar"

  # Stable failures expose only their phase, code and an optional log basename.
  rm "$PACKAGE/payload/parts.txt"
  missing_status=0
  env "${PLATFORM_ENV[@]}" PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" \
    VISTA_UI_PORT="$PORT_BASE" \
    VISTA_MCP_PORT="$(( PORT_BASE + 1 ))" \
    VISTA_BACKEND_PORT="$(( PORT_BASE + 2 ))" \
    "$PACKAGE/vista" --supervised --progress=jsonl \
    > "$TMP/missing-stdout" 2> "$TMP/missing-stderr" || missing_status=$?
  [[ "$missing_status" != 0 ]]
  grep -qx '{"protocol":1,"phase":"preflight","state":"failed","code":"missing-component"}' \
    "$TMP/missing-stdout"
  : > "$PACKAGE/payload/parts.txt"

  long_state="/tmp/vista-launcher-state-that-is-deliberately-too-long-for-microsandbox"
  state_status=0
  env "${PLATFORM_ENV[@]}" PATH="$TOOLS:$PATH" VISTA_HOME="$long_state" \
    VISTA_UI_PORT="$PORT_BASE" \
    VISTA_MCP_PORT="$(( PORT_BASE + 1 ))" \
    VISTA_BACKEND_PORT="$(( PORT_BASE + 2 ))" \
    "$PACKAGE/vista" --supervised --progress=jsonl \
    > "$TMP/state-stdout" 2> "$TMP/state-stderr" || state_status=$?
  [[ "$state_status" != 0 ]]
  grep -qx '{"protocol":1,"phase":"preflight","state":"failed","code":"invalid-state-path"}' \
    "$TMP/state-stdout"

  # A setup failure stays machine-readable on stdout and identifies its phase.
  printf '%s\n' 'missing-resource' > "$PACKAGE/payload/parts.txt"
  failure_status=0
  env "${PLATFORM_ENV[@]}" PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" \
    VISTA_UI_PORT="$PORT_BASE" \
    VISTA_MCP_PORT="$(( PORT_BASE + 1 ))" \
    VISTA_BACKEND_PORT="$(( PORT_BASE + 2 ))" \
    "$PACKAGE/vista" --supervised --progress=jsonl \
    > "$TMP/failure-stdout" 2> "$TMP/failure-stderr" || failure_status=$?
  if [[ "$failure_status" == 0 ]]; then
    printf 'expected resource setup to fail\n' >&2
    exit 1
  fi
  grep -qx '{"protocol":1,"phase":"resources","state":"failed","code":"resource-extraction-failed"}' \
    "$TMP/failure-stdout"
  if grep -v '^{"protocol":1,' "$TMP/failure-stdout"; then
    printf 'supervised stdout included a non-protocol line\n' >&2
    exit 1
  fi
  : > "$PACKAGE/payload/parts.txt"

  image_status=0
  env "${PLATFORM_ENV[@]}" PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" \
    TEST_IMAGE_PRESENT=0 TEST_MSB_IMPORT_FAIL=1 \
    VISTA_UI_PORT="$PORT_BASE" \
    VISTA_MCP_PORT="$(( PORT_BASE + 1 ))" \
    VISTA_BACKEND_PORT="$(( PORT_BASE + 2 ))" \
    "$PACKAGE/vista" --supervised --progress=jsonl \
    > "$TMP/image-stdout" 2> "$TMP/image-stderr" || image_status=$?
  [[ "$image_status" != 0 ]]
  grep -qx '{"protocol":1,"phase":"sandbox","state":"failed","code":"sandbox-image-import-failed","log":"setup.log"}' \
    "$TMP/image-stdout"

  mkfifo "$TMP/parent-$os.pipe"
  exec 9<> "$TMP/parent-$os.pipe"
  PARENT_PIPE_OPEN=true
  env "${PLATFORM_ENV[@]}" PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" \
    TEST_SERVICE_PIDS="$TMP/service-pids" \
    VISTA_UI_PORT="$PORT_BASE" \
    VISTA_MCP_PORT="$(( PORT_BASE + 1 ))" \
    VISTA_BACKEND_PORT="$(( PORT_BASE + 2 ))" \
    "$PACKAGE/vista" --supervised --progress=jsonl \
    < "$TMP/parent-$os.pipe" 9>&- > "$TMP/stdout" 2> "$TMP/stderr" &
  LAUNCHER_PID=$!

  for (( i = 0; i < 100; i++ )); do
    grep -q '"phase":"ui","state":"ready"' "$TMP/stdout" 2>/dev/null && break
    kill -0 "$LAUNCHER_PID" 2>/dev/null || {
      printf 'supervised launcher exited early:\n' >&2
      cat "$TMP/stdout" "$TMP/stderr" >&2
      exit 1
    }
    sleep 0.05
  done
  grep -q '"phase":"ui","state":"ready"' "$TMP/stdout"
  wait_for_fake_services

  # The missing window executable proves supervised mode skipped both validation
  # and window creation. Termination must still run the launcher's normal cleanup.
  kill -TERM "$LAUNCHER_PID"
  launcher_status=0
  wait "$LAUNCHER_PID" || launcher_status=$?
  if [[ "$launcher_status" != 0 && "$launcher_status" != 126 \
        && "$launcher_status" != 143 ]]; then
    printf 'supervised launcher exited with unexpected status %s\n' \
      "$launcher_status" >&2
    cat "$TMP/stdout" "$TMP/stderr" >&2
    exit 1
  fi
  # Bash 3.2 can report 126 for a monitor-mode shell whose wait was interrupted
  # by the signal it handled. The launcher itself must still be gone, and the
  # existing stop path must have run.
  if kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    printf 'supervised launcher survived SIGTERM\n' >&2
    exit 1
  fi
  LAUNCHER_PID=''
  exec 9>&-
  PARENT_PIPE_OPEN=false
  expect_fake_services_stopped

  {
    printf '%s\n' '{"protocol":1,"phase":"preflight","state":"running","label":"Checking this computer"}'
    printf '%s\n' '{"protocol":1,"phase":"preflight","state":"complete"}'
    printf '%s\n' '{"protocol":1,"phase":"resources","state":"running","label":"Installing bundled resources"}'
    printf '%s\n' '{"protocol":1,"phase":"resources","state":"complete","skipped":true}'
    printf '%s\n' '{"protocol":1,"phase":"sandbox","state":"running","label":"Preparing the code-execution sandbox"}'
    printf '%s\n' '{"protocol":1,"phase":"sandbox","state":"complete","skipped":true}'
    printf '%s\n' '{"protocol":1,"phase":"mcp","state":"running","label":"Starting scientific tools"}'
    printf '%s\n' '{"protocol":1,"phase":"mcp","state":"ready"}'
    printf '%s\n' '{"protocol":1,"phase":"backend","state":"running","label":"Preparing VISTA"}'
    printf '%s\n' '{"protocol":1,"phase":"backend","state":"ready"}'
    printf '%s\n' '{"protocol":1,"phase":"ui","state":"running","label":"Starting the interface"}'
    printf '%s\n' \
      "{\"protocol\":1,\"phase\":\"ui\",\"state\":\"ready\",\"url\":\"http://127.0.0.1:$PORT_BASE\"}"
    printf '%s\n' '{"protocol":1,"phase":"stopping","state":"running","label":"Stopping VISTA"}'
  } > "$TMP/expected-stdout"
  diff -u "$TMP/expected-stdout" "$TMP/stdout"

  # Losing the application's stdin pipe requests the same normal stop path.
  stop_fake_services
  rm -f "$TMP"/service-pids/*.pid "$TMP"/service-pids/*.stopped \
    "$TMP"/service-pids/*.ready
  mkfifo "$TMP/parent-eof-$os.pipe"
  exec 9<> "$TMP/parent-eof-$os.pipe"
  PARENT_PIPE_OPEN=true
  env "${PLATFORM_ENV[@]}" PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" \
    TEST_SERVICE_PIDS="$TMP/service-pids" \
    VISTA_UI_PORT="$PORT_BASE" \
    VISTA_MCP_PORT="$(( PORT_BASE + 1 ))" \
    VISTA_BACKEND_PORT="$(( PORT_BASE + 2 ))" \
    "$PACKAGE/vista" --supervised --progress=jsonl \
    < "$TMP/parent-eof-$os.pipe" 9>&- > "$TMP/eof-stdout" 2> "$TMP/eof-stderr" &
  LAUNCHER_PID=$!
  for (( i = 0; i < 100; i++ )); do
    grep -q '"phase":"ui","state":"ready"' "$TMP/eof-stdout" 2>/dev/null && break
    kill -0 "$LAUNCHER_PID" 2>/dev/null || {
      printf 'EOF-test launcher exited early:\n' >&2
      cat "$TMP/eof-stdout" "$TMP/eof-stderr" >&2
      exit 1
    }
    sleep 0.05
  done
  grep -q '"phase":"ui","state":"ready"' "$TMP/eof-stdout"
  wait_for_fake_services
  exec 9>&-
  PARENT_PIPE_OPEN=false
  eof_status=0
  wait "$LAUNCHER_PID" || eof_status=$?
  if [[ "$eof_status" != 0 && "$eof_status" != 126 && "$eof_status" != 129 ]]; then
    printf 'EOF-test launcher exited with unexpected status %s\n' "$eof_status" >&2
    cat "$TMP/eof-stdout" "$TMP/eof-stderr" >&2
    exit 1
  fi
  if kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    printf 'supervised launcher survived parent EOF\n' >&2
    exit 1
  fi
  LAUNCHER_PID=''
  grep -qx '{"protocol":1,"phase":"stopping","state":"running","label":"Stopping VISTA"}' \
    "$TMP/eof-stdout"
  expect_fake_services_stopped
}

PORT_BASE=$(( 40000 + $$ % 10000 ))
supervised_suite macos Darwin
supervised_suite linux Linux

# The existing diagnostic CLI remains human-readable and does not emit JSON.
printf '%s\n' '{"os": "macos", "arch": "arm64", "exe": "missing-window"}' \
  > "$PACKAGE/manifest.json"
stop_fake_services
rm -f "$TMP"/service-pids/*.pid "$TMP"/service-pids/*.stopped \
  "$TMP"/service-pids/*.alive "$TMP"/service-pids/*.ready
env PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" VISTA_NO_WINDOW=1 \
  TEST_SERVICE_PIDS="$TMP/service-pids" \
  VISTA_UI_PORT="$PORT_BASE" \
  VISTA_MCP_PORT="$(( PORT_BASE + 1 ))" \
  VISTA_BACKEND_PORT="$(( PORT_BASE + 2 ))" \
  "$PACKAGE/vista" > "$TMP/cli-stdout" 2> "$TMP/cli-stderr" &
LAUNCHER_PID=$!
for (( i = 0; i < 100; i++ )); do
  grep -q 'VISTA is running at' "$TMP/cli-stdout" 2>/dev/null && break
  kill -0 "$LAUNCHER_PID" 2>/dev/null || {
    printf 'ordinary CLI exited early:\n' >&2
    cat "$TMP/cli-stdout" "$TMP/cli-stderr" >&2
    exit 1
  }
  sleep 0.05
done
grep -q "VISTA is running at http://127.0.0.1:$PORT_BASE (no window: VISTA_NO_WINDOW is set)" \
  "$TMP/cli-stdout"
wait_for_fake_services
if grep -q '^{"protocol":' "$TMP/cli-stdout"; then
  printf 'ordinary CLI emitted supervised protocol output\n' >&2
  exit 1
fi
kill -TERM "$LAUNCHER_PID"
wait "$LAUNCHER_PID" 2>/dev/null || true
LAUNCHER_PID=''
expect_fake_services_stopped

# On Linux without KVM, supervised mode fails in preflight with its own code,
# so the application can say what is wrong instead of a generic failure.
if [[ ! -e /dev/kvm ]]; then
  printf '%s\n' '{"os": "linux", "arch": "arm64", "exe": "missing-window"}' \
    > "$PACKAGE/manifest.json"
  kvm_status=0
  env TEST_UNAME_S=Linux PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" \
    VISTA_UI_PORT="$PORT_BASE" \
    VISTA_MCP_PORT="$(( PORT_BASE + 1 ))" \
    VISTA_BACKEND_PORT="$(( PORT_BASE + 2 ))" \
    "$PACKAGE/vista" --supervised --progress=jsonl \
    > "$TMP/kvm-stdout" 2> "$TMP/kvm-stderr" || kvm_status=$?
  [[ "$kvm_status" != 0 ]]
  grep -qx '{"protocol":1,"phase":"preflight","state":"failed","code":"virtualisation-unavailable"}' \
    "$TMP/kvm-stdout"
  grep -q 'VISTA needs hardware virtualisation on Linux' "$TMP/kvm-stderr"
fi

# The ordinary Linux launcher keeps its platform preflight and never enters
# the protocol.
printf '%s\n' '{"os": "linux", "arch": "arm64", "exe": "missing-window"}' \
  > "$PACKAGE/manifest.json"
linux_status=0
env TEST_UNAME_S=Linux PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" \
  VISTA_NO_WINDOW=1 "$PACKAGE/vista" \
  > "$TMP/linux-stdout" 2> "$TMP/linux-stderr" || linux_status=$?
[[ "$linux_status" != 0 ]]
grep -q 'VISTA needs hardware virtualisation on Linux' "$TMP/linux-stderr"
if grep -q '^{"protocol":' "$TMP/linux-stdout"; then
  printf 'ordinary Linux launcher emitted supervised protocol output\n' >&2
  exit 1
fi

printf '%s\n' "package launcher supervised-mode tests passed"
