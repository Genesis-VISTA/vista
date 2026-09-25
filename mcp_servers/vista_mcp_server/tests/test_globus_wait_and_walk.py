"""
Globus wait-loop and recursive-walk behavior.

Both guard the same symptom: VISTA appearing to hang on a transfer whose Globus task
has already finished (reported 2026-09-21).

  - A task Globus puts in INACTIVE never advances on its own (expired credentials or
    consent). The old loop polled it for the full 3600s timeout, which is an hour of
    silence indistinguishable from a hang.
  - The recursive `operation_ls` costs one sequential API round-trip per directory.
    Callers filtered venv/.git noise out of the RESULTS, which does nothing for cost —
    the walk still descended into them.
"""

import time

import pytest

from vista_mcp_server.lib.globus import GlobusClient

pytestmark = pytest.mark.unit


class _FakeTransferClient:
    """Minimal stand-in for globus_sdk.TransferClient."""

    def __init__(self, *, task_states=None, tree=None):
        self._task_states = list(task_states or [])
        self._tree = tree or {}
        self.ls_calls: list[str] = []

    def get_task(self, task_id):
        state = (
            self._task_states.pop(0) if self._task_states else {"status": "SUCCEEDED"}
        )

        class _Resp(dict):
            @property
            def data(self):
                return dict(self)

        return _Resp(state)

    def operation_ls(self, endpoint, path):
        self.ls_calls.append(path)
        return {"DATA": self._tree.get(path, [])}


def _client(tc) -> GlobusClient:
    client = GlobusClient.__new__(GlobusClient)  # bypass auth in __init__
    client._tc = tc
    return client


def _d(name):
    return {"name": name, "type": "dir"}


def _f(name):
    return {"name": name, "type": "file"}


# --- wait loop -------------------------------------------------------------


def test_inactive_task_fails_fast_instead_of_polling_for_an_hour():
    tc = _FakeTransferClient(
        task_states=[{"status": "INACTIVE", "nice_status": "EXPIRED_CREDENTIALS"}]
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="INACTIVE"):
        _client(tc)._wait_for_task("task-1", poll_seconds=10, timeout_seconds=3600)
    # Returned immediately rather than sleeping through the poll interval.
    assert time.monotonic() - started < 1.0


def test_inactive_error_names_the_actual_reason():
    tc = _FakeTransferClient(
        task_states=[
            {
                "status": "INACTIVE",
                "nice_status": "EXPIRED_CREDENTIALS",
                "nice_status_short_description": "Credentials have expired",
            }
        ]
    )
    with pytest.raises(RuntimeError) as exc:
        _client(tc)._wait_for_task("task-1", poll_seconds=10, timeout_seconds=3600)
    assert "EXPIRED_CREDENTIALS" in str(exc.value)
    assert "Credentials have expired" in str(exc.value)


def test_succeeded_returns_the_task_body():
    tc = _FakeTransferClient(task_states=[{"status": "SUCCEEDED", "task_id": "t"}])
    got = _client(tc)._wait_for_task("t", poll_seconds=10, timeout_seconds=60)
    assert got["status"] == "SUCCEEDED"


def test_failed_is_terminal_and_returned_not_raised():
    tc = _FakeTransferClient(task_states=[{"status": "FAILED"}])
    got = _client(tc)._wait_for_task("t", poll_seconds=10, timeout_seconds=60)
    assert got["status"] == "FAILED"  # transfer_and_wait turns this into RuntimeError


def test_timeout_does_not_sleep_past_the_deadline():
    """An already-expired budget must raise now, not one poll interval from now."""
    tc = _FakeTransferClient(task_states=[{"status": "ACTIVE"}, {"status": "ACTIVE"}])
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _client(tc)._wait_for_task("t", poll_seconds=30, timeout_seconds=0)
    assert time.monotonic() - started < 1.0


# --- recursive walk --------------------------------------------------------


def test_excluded_dirs_are_never_descended_into():
    tree = {
        "/out": [_f("results.json"), _d(".venv"), _d("data")],
        "/out/.venv": [_d("lib")],
        "/out/.venv/lib": [_f("junk.py")],
        "/out/data": [_f("a.csv")],
    }
    tc = _FakeTransferClient(tree=tree)
    entries = _client(tc)._operation_ls(
        "ep", "/out", True, (".venv", "__pycache__"), 200
    )
    assert "/out/.venv" not in tc.ls_calls, "paid for an API call into an excluded dir"
    assert "/out/.venv/lib" not in tc.ls_calls
    assert "/out/data" in tc.ls_calls
    names = {e["path"] for e in entries}
    assert "/out/data/a.csv" in names
    assert "/out/.venv/lib/junk.py" not in names


def test_walk_without_excludes_still_descends():
    """Control: the pruning must be the exclude list, not an accident of the walk."""
    tree = {"/out": [_d(".venv")], "/out/.venv": [_f("x")]}
    tc = _FakeTransferClient(tree=tree)
    _client(tc)._operation_ls("ep", "/out", True, (), 200)
    assert "/out/.venv" in tc.ls_calls


def test_max_dirs_bounds_a_runaway_tree():
    """A deep tree must yield a partial listing, never an unbounded sequential walk."""
    tree = {f"/out{'/d' * i}": [_d("d")] for i in range(50)}
    tc = _FakeTransferClient(tree=tree)
    _client(tc)._operation_ls("ep", "/out", True, (), 5)
    assert len(tc.ls_calls) == 5


def test_non_recursive_ls_makes_exactly_one_call():
    tree = {"/out": [_f("results.json"), _d("sub")], "/out/sub": [_f("y")]}
    tc = _FakeTransferClient(tree=tree)
    entries = _client(tc)._operation_ls("ep", "/out", False, (), 200)
    assert tc.ls_calls == ["/out"]
    assert len(entries) == 2


# --- ACTIVE + a fatal nice_status must not be waited out -----------------------


def test_file_not_found_fails_fast_instead_of_retrying_for_an_hour():
    """
    Observed live (2026-09-21): an output fetch for a job that was still RUNNING sat
    `ACTIVE / nice_status=FILE_NOT_FOUND`. Globus retries a missing source file until
    its own deadline rather than failing, so the wait loop polled silently and the tool
    call looked hung.
    """
    tc = _FakeTransferClient(
        task_states=[{"status": "ACTIVE", "nice_status": "FILE_NOT_FOUND"}]
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError) as exc:
        _client(tc)._wait_for_task("t", poll_seconds=10, timeout_seconds=3600)
    assert "FILE_NOT_FOUND" in str(exc.value)
    assert "still running" in str(exc.value)  # names the usual cause
    assert time.monotonic() - started < 1.0


@pytest.mark.parametrize(
    "nice",
    ["PERMISSION_DENIED", "NO_CREDENTIALS", "EXPIRED_CREDENTIALS", "PATH_NOT_ALLOWED"],
)
def test_other_unrecoverable_nice_statuses_also_fail_fast(nice):
    tc = _FakeTransferClient(task_states=[{"status": "ACTIVE", "nice_status": nice}])
    with pytest.raises(RuntimeError, match=nice):
        _client(tc)._wait_for_task("t", poll_seconds=10, timeout_seconds=3600)


def test_ordinary_active_still_waits():
    """A genuinely-progressing transfer must not be aborted."""
    tc = _FakeTransferClient(
        task_states=[
            {"status": "ACTIVE", "nice_status": None},
            {"status": "ACTIVE", "nice_status": "QUEUED"},
            {"status": "SUCCEEDED"},
        ]
    )
    got = _client(tc)._wait_for_task("t", poll_seconds=0, timeout_seconds=60)
    assert got["status"] == "SUCCEEDED"


def test_transient_connect_failure_is_not_treated_as_fatal():
    tc = _FakeTransferClient(
        task_states=[
            {"status": "ACTIVE", "nice_status": "CONNECT_FAILED"},
            {"status": "SUCCEEDED"},
        ]
    )
    assert (
        _client(tc)._wait_for_task("t", poll_seconds=0, timeout_seconds=60)["status"]
        == "SUCCEEDED"
    )
