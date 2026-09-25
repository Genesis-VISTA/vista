"""
A failing job must be able to say why.

Two blind spots found while debugging a real Odo failure (2026-09-21): the status
output showed only the FIRST 200 lines of stdout — so a verbose CMake build buried the
error — and the stderr log the JobSpec routes to `log-<id>.err` was never fetched or
displayed at all. Between them, a job that died with a clear Python message reported
nothing but a stdout log that simply stopped.
"""

import pytest

from vista_mcp_server.submit_job_mcp import _head_and_tail

pytestmark = pytest.mark.unit


def test_tail_is_kept_not_just_the_head(tmp_path):
    log = tmp_path / "log-1.out"
    log.write_text(
        "\n".join(
            [f"[{i}%] Building CXX object..." for i in range(500)]
            + ["error: the actual failure"]
        )
    )
    out = _head_and_tail(log)
    assert "error: the actual failure" in out, (
        "the failure message must survive truncation"
    )
    assert "[0%]" in out, "the start is still useful context"
    assert "lines elided" in out


def test_short_logs_pass_through_whole(tmp_path):
    log = tmp_path / "log-1.out"
    body = "\n".join(f"line{i}" for i in range(20))
    log.write_text(body)
    assert _head_and_tail(log) == body
    assert "elided" not in _head_and_tail(log)


def test_middle_is_what_gets_dropped(tmp_path):
    log = tmp_path / "log-1.out"
    log.write_text("\n".join(f"line{i}" for i in range(1000)))
    out = _head_and_tail(log, head=10, tail=10)
    assert "line0" in out and "line999" in out
    assert "line500" not in out


def test_undecodable_bytes_do_not_break_reading(tmp_path):
    """Engine logs can carry stray binary; a status call must not 500 on it."""
    log = tmp_path / "log-1.out"
    log.write_bytes(b"ok\n\xff\xfe binary \n done\n")
    assert "done" in _head_and_tail(log)


def test_status_assembles_a_stderr_section():
    """The status text must have somewhere for stderr to appear, after stdout."""
    import inspect
    from vista_mcp_server import submit_job_mcp as m

    src = inspect.getsource(m)
    assert '"--- STDERR ---"' in src
    assert 'with_suffix(".err")' in src, "stderr log path is never derived"
    # and it must actually be fetched, not just derived
    assert "local_err_path" in src and "remote_err_path" in src
