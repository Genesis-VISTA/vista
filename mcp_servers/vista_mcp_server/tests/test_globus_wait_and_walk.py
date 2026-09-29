"""
Recursive-walk cost control for `operation_ls`.

The HTTPS interface has no directory listing, so the recursive walk stays on the
Transfer API and costs ONE round-trip per directory, sequentially. Callers filtered
venv/.git noise out of the RESULTS, which does nothing for cost — the walk still
descended into them, turning a status check into minutes of silent waiting.
"""

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
    """A GlobusClient with only the Transfer half stubbed (no auth, no HTTPS)."""
    client = GlobusClient.__new__(GlobusClient)
    client._tc = tc
    client._transfer = lambda: tc  # upstream routes ls through _transfer()
    return client


def _d(name):
    return {"name": name, "type": "dir"}


def _f(name):
    return {"name": name, "type": "file"}


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
