"""
A failing job must be able to say why.

The blind spot this guards (found debugging a real Odo failure): a job with a verbose
prologue — a CMake build, a pip install — pushes its error message far down the log, so
any window anchored at the START of the file reports build chatter and nothing about why
the job died. `_read_log_tail` anchors at the END instead.
"""

import pytest

from vista_mcp_server.submit_job_mcp import (
    _LOG_TAIL_BYTES,
    _LOG_TAIL_LINES,
    _read_log_tail,
)

pytestmark = pytest.mark.unit


def test_the_failure_message_at_the_end_survives(tmp_path):
    log = tmp_path / "log-1.out"
    log.write_text(
        "\n".join(
            [f"[{i}%] Building CXX object..." for i in range(5000)]
            + ["error: the actual failure"]
        ),
        encoding="utf-8",
    )
    out = _read_log_tail(log)
    assert "error: the actual failure" in out


def test_output_is_capped_to_the_line_budget(tmp_path):
    log = tmp_path / "log-1.out"
    log.write_text(
        "\n".join(f"line{i}" for i in range(_LOG_TAIL_LINES * 3)), encoding="utf-8"
    )
    assert len(_read_log_tail(log).splitlines()) <= _LOG_TAIL_LINES


def test_short_logs_come_back_whole(tmp_path):
    log = tmp_path / "log-1.out"
    body = "\n".join(f"line{i}" for i in range(20))
    log.write_text(body, encoding="utf-8")
    assert _read_log_tail(log) == body


def test_a_window_opening_mid_line_drops_the_partial_line(tmp_path):
    """A truncated first line reads as corrupt output rather than as a window."""
    log = tmp_path / "log-1.out"
    filler = "x" * (_LOG_TAIL_BYTES + 5000)
    log.write_text(filler + "\nCLEAN LINE\n", encoding="utf-8")
    out = _read_log_tail(log)
    assert "CLEAN LINE" in out
    assert not out.startswith("x"), "partial first line should have been dropped"


def test_undecodable_bytes_do_not_break_reading(tmp_path):
    """Engine logs can carry stray binary; a status call must not fail on it."""
    log = tmp_path / "log-1.out"
    log.write_bytes(b"ok\n\xff\xfe binary \n done\n")
    assert "done" in _read_log_tail(log)


def test_missing_log_is_not_an_error(tmp_path):
    assert _read_log_tail(tmp_path / "nope.out") == ""
