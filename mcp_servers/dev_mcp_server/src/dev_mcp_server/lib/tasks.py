"""Commands that outlive the call that started them.

A task is started detached in its own session (`setsid -f`) on an exec without a terminal, with
all three descriptors redirected: under a terminal, or with a descriptor left attached, the
command dies with its exec or the exec never reports its end. The task's session id is its
process group, so one group signal ends the command and everything it started, and nothing
else. Three files record it: `<id>.pid` (the group), `<id>.exit` (the command's own status,
written only if it ends by itself) and its log.

The log is written by a small perl reader rather than `head -c`: `head` holds its output in a
stdio buffer, so a running task's log would stay empty. The reader stops writing at the cap,
adds one cut-off line, and keeps draining the pipe so the command never dies of a closed pipe.
"""

import re
from pathlib import PurePosixPath
from typing import Literal, NotRequired, TypedDict

from .sandbox import Sandbox

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_CAPPER = r"""
my ($cap, $log, $msg) = @ARGV;
open(my $fh, '>', $log) or die "cannot open $log: $!";
my ($n, $cut, $buf) = (0, 0);
while (my $r = sysread(STDIN, $buf, 65536)) {
    my $w = $cap - $n; $w = $r if $r < $w;
    if ($w > 0) { syswrite($fh, $buf, $w); $n += $w; }
    if ($r > $w && !$cut) { syswrite($fh, "\n$msg\n"); $cut = 1; }
}
"""

_WRAPPER = r"""
capper=$1 cmd=$2 log=$3 pidf=$4 exitf=$5 cap=$6 msg=$7
echo $$ > "$pidf.tmp" && mv -f "$pidf.tmp" "$pidf"
bash -c "$cmd" </dev/null 2>&1 | perl -e "$capper" "$cap" "$log" "$msg"
status=${PIPESTATUS[0]}
echo "$status" > "$exitf.tmp" && mv -f "$exitf.tmp" "$exitf"
"""

_START = r"""
wrapper=$1 log=$4 pidf=$5 exitf=$6
mkdir -p "$(dirname "$pidf")" "$(dirname "$log")" || exit 1
if [ -e "$pidf" ] || [ -e "$exitf" ]; then echo "task already exists" >&2; exit 1; fi
: > "$log" || exit 1
shift
setsid -f bash -c "$wrapper" bash "$@" </dev/null >/dev/null 2>&1 || exit 1
for _ in $(seq 100); do
    [ -s "$pidf" ] && { cat "$pidf"; exit 0; }
    sleep 0.1
done
echo "task did not start" >&2
exit 1
"""

# A pgid of 0 or 1 would signal the caller's own group or every process; refuse anything that
# is not a plain number above 1.
_READ_PGID = r"""
pgid=$(cat "$pidf" 2>/dev/null)
case "$pgid" in ''|*[!0-9]*) pgid= ;; esac
[ -n "$pgid" ] && [ "$pgid" -le 1 ] && pgid=
"""

_WAIT = (
    r"""
pidf=$1 exitf=$2 max_wait=$3
ticks=$((max_wait * 5))
while :; do
"""
    + _READ_PGID
    + r"""
    if [ -f "$exitf" ]; then echo "exited $(cat "$exitf")"; exit 0; fi
    if [ -z "$pgid" ]; then echo unknown; exit 0; fi
    if ! kill -0 -- "-$pgid" 2>/dev/null; then
        if [ -f "$exitf" ]; then echo "exited $(cat "$exitf")"; else echo gone; fi
        exit 0
    fi
    if [ "$ticks" -le 0 ]; then echo running; exit 0; fi
    ticks=$((ticks - 1))
    sleep 0.2
done
"""
)

_SIGNAL = (
    r"""
pidf=$1 grace=$2
"""
    + _READ_PGID
    + r"""
if [ -z "$pgid" ]; then echo unknown; exit 0; fi
kill -TERM -- "-$pgid" 2>/dev/null || { echo not-running; exit 0; }
for _ in $(seq $((grace * 5))); do
    kill -0 -- "-$pgid" 2>/dev/null || { echo ended; exit 0; }
    sleep 0.2
done
kill -KILL -- "-$pgid" 2>/dev/null
echo killed
"""
)


class WaitResult(TypedDict):
    state: Literal["exited", "gone", "running", "unknown"]
    exit_status: NotRequired[int]


class SignalResult(TypedDict):
    state: Literal["ended", "killed", "not-running", "unknown"]


def check_task_id(task_id: str) -> str:
    if not _TASK_ID.match(task_id):
        raise ValueError(
            f"Invalid task id {task_id!r}: use letters, digits, '-' and '_' only"
        )
    return task_id


def _files(tasks_dir: PurePosixPath, task_id: str) -> tuple[str, str]:
    check_task_id(task_id)
    return str(tasks_dir / f"{task_id}.pid"), str(tasks_dir / f"{task_id}.exit")


def default_log_path(tasks_dir: PurePosixPath, task_id: str) -> str:
    return str(tasks_dir / f"{check_task_id(task_id)}.log")


def cut_off_line(cap: int) -> str:
    mb = 1024 * 1024
    size = f"{cap // mb} MB" if cap % mb == 0 else f"{cap} bytes"
    return f"[output cut off at {size}]"


async def _run(sandbox: Sandbox, script: str, *args: str) -> str:
    # No terminal: a detached task started under one is killed when the exec ends.
    proc = await sandbox.exec("bash", args=["-c", script, "bash", *args])
    stdout, stderr = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(
            (stderr or b"").decode(errors="replace").strip()
            or f"exited with status {proc.returncode}"
        )
    return stdout.decode(errors="replace").strip()


async def start(
    sandbox: Sandbox,
    task_id: str,
    command: str,
    log_path: str,
    tasks_dir: PurePosixPath,
    cap: int,
) -> int:
    """Start `command` detached and return its process group id, without reading its output."""
    pidf, exitf = _files(tasks_dir, task_id)
    try:
        out = await _run(
            sandbox,
            _START,
            _WRAPPER,
            _CAPPER,
            command,
            log_path,
            pidf,
            exitf,
            str(cap),
            cut_off_line(cap),
        )
    except RuntimeError as e:
        raise RuntimeError(f"Could not start task {task_id}: {e}") from None
    return int(out)


async def wait(
    sandbox: Sandbox, task_id: str, tasks_dir: PurePosixPath, max_wait: int
) -> WaitResult:
    """Wait up to `max_wait` seconds for a task to end.

    `exited` carries the command's own status. `gone` means its group ended without one, so it
    was signalled. `running` means `max_wait` passed. `unknown` means no such task was started.
    """
    pidf, exitf = _files(tasks_dir, task_id)
    out = await _run(sandbox, _WAIT, pidf, exitf, str(max(0, int(max_wait))))
    state, _, status = out.partition(" ")
    if state == "exited":
        return {"state": "exited", "exit_status": int(status)}
    if state in ("gone", "running", "unknown"):
        return {"state": state}
    raise RuntimeError(f"Unexpected answer waiting for task {task_id}: {out!r}")


async def signal(
    sandbox: Sandbox, task_id: str, tasks_dir: PurePosixPath, grace: int
) -> SignalResult:
    """End a task's whole process group: TERM, then KILL after `grace` seconds."""
    pidf, _ = _files(tasks_dir, task_id)
    out = await _run(sandbox, _SIGNAL, pidf, str(max(0, int(grace))))
    if out in ("ended", "killed", "not-running", "unknown"):
        return {"state": out}
    raise RuntimeError(f"Unexpected answer signalling task {task_id}: {out!r}")
