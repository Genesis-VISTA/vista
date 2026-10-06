#!/bin/bash
set -euo pipefail

REPO_ROOT="$(dirname "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")")"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/vista-mac-dev-launcher-test.XXXXXX")"
cleanup() {
  local status=$?
  if [[ "$status" != 0 ]]; then
    for file in "$TMP/stdout" "$TMP/stderr" "$STATE/logs/dev-stack.log"; do
      [[ -f "$file" ]] || continue
      printf '%s\n' "--- ${file##*/}" >&2
      sed -n '1,160p' "$file" >&2
    done
  fi
  rm -rf "$TMP"
  exit "$status"
}
trap cleanup EXIT

PACKAGE="$TMP/package"
FAKE_REPO="$TMP/repo"
TOOLS="$TMP/tools"
STATE="$TMP/state"
mkdir -p "$PACKAGE" "$FAKE_REPO/scripts" "$TOOLS" "$STATE"
cp "$REPO_ROOT/scripts/mac_dev_launcher.sh" "$PACKAGE/vista"
chmod +x "$PACKAGE/vista"
printf '%s\n' "$FAKE_REPO" > "$PACKAGE/dev-root"

cat > "$FAKE_REPO/scripts/launch.sh" <<'EOF'
#!/bin/bash
set -euo pipefail
marker="${VISTA_FAKE_STACK_MARKER:?}"
trap 'printf stopped > "$marker"; exit 0' TERM INT HUP
printf started > "$marker"
while true; do sleep 1; done
EOF
chmod +x "$FAKE_REPO/scripts/launch.sh"

for name in uv npm; do
  cat > "$TOOLS/$name" <<'EOF'
#!/bin/sh
exit 0
EOF
  chmod +x "$TOOLS/$name"
done
cat > "$TOOLS/lsof" <<'EOF'
#!/bin/sh
exit 1
EOF
cat > "$TOOLS/curl" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$TOOLS/lsof" "$TOOLS/curl"

FIFO="$TMP/stdin"
mkfifo "$FIFO"
PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" VISTA_FAKE_STACK_MARKER="$TMP/stack" \
  "$PACKAGE/vista" --supervised --progress=jsonl < "$FIFO" \
  > "$TMP/stdout" 2> "$TMP/stderr" &
launcher_pid=$!
exec 3> "$FIFO"

for _ in {1..50}; do
  grep -q '"phase":"ui","state":"ready"' "$TMP/stdout" 2>/dev/null && break
  sleep 0.1
done
grep -qx '{"protocol":1,"phase":"preflight","state":"running","label":"Checking this computer"}' "$TMP/stdout"
grep -qx '{"protocol":1,"phase":"resources","state":"complete","skipped":true}' "$TMP/stdout"
grep -qx '{"protocol":1,"phase":"ui","state":"ready","url":"http://localhost:3000"}' "$TMP/stdout"

kill -TERM "$launcher_pid"
wait "$launcher_pid"
exec 3>&-
grep -qx '{"protocol":1,"phase":"stopping","state":"running","label":"Stopping VISTA"}' "$TMP/stdout"
grep -qx stopped "$TMP/stack"

rm "$PACKAGE/dev-root"
missing_status=0
PATH="$TOOLS:$PATH" VISTA_HOME="$STATE" "$PACKAGE/vista" \
  --supervised --progress=jsonl </dev/null > "$TMP/missing" 2>/dev/null \
  || missing_status=$?
[[ "$missing_status" != 0 ]]
grep -qx '{"protocol":1,"phase":"preflight","state":"failed","code":"missing-component"}' "$TMP/missing"

echo 'mac dev launcher tests passed'
