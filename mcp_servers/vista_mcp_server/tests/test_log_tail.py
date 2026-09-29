"""Tailing an OLCF job log, and what a status query says when it cannot.

The old design fetched the whole log across a Globus transfer task and read its
first 200 lines. Both halves were wrong once ranged reads became available: the
head of a running or failed job's log is module loads and startup noise, and
re-fetching a growing file to read the same opening lines gets more expensive
the longer the job runs.

What replaces it is two requests -- `HEAD` for the size, one explicit range for
what is new -- with the local copy doubling as the offset. The tests below are
mostly about that offset being right, because an offset that drifts either
re-reads the whole log every poll or silently loses output.

The other half is D5: a status query must distinguish "the log is not there
yet" from "your credential lapsed". Those were the same answer once, and that
is the bug this whole change exists to make unreproducible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import vista_mcp_server.submit_job_mcp as submit_job_mcp
from vista_mcp_server.config import settings
from vista_mcp_server.lib.globus import GlobusSessionExpired
from vista_mcp_server.lib.user_config import UserConfig
from vista_mcp_server.submit_job_mcp import (
    SubmittedJob,
    _get_olcf_job_status,
    _read_log_tail,
    _record_submitted_job,
    _submitted_jobs,
    _tail_remote_log,
)
from fakes import FakeGlobusClient, FakeIriClient

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

REMOTE_LOG = "/lustre/orion/chm243/proj-shared/vista/out/log-1.out"
COLLECTION = "frontier-collection"


@pytest.fixture
def globus() -> FakeGlobusClient:
    return FakeGlobusClient(cluster="frontier")


async def tail(globus: FakeGlobusClient, local: Path) -> str:
    return await _tail_remote_log(
        globus, collection_id=COLLECTION, remote_path=REMOTE_LOG, local_path=local
    )


class TestTheIncrementalFetch:
    async def test_the_first_poll_reads_the_whole_file(self, globus, tmp_path):
        globus.files[REMOTE_LOG] = b"one\ntwo\n"
        local = tmp_path / "log-1.out"

        assert await tail(globus, local) == "one\ntwo"
        assert globus.range_reads == [(REMOTE_LOG, 0, 7)]

    async def test_a_later_poll_reads_only_what_is_new(self, globus, tmp_path):
        """The point of the design. A job that writes a megabyte of log over an
        hour costs each poll the lines since the previous one, not the megabyte."""
        globus.files[REMOTE_LOG] = b"one\ntwo\n"
        local = tmp_path / "log-1.out"
        await tail(globus, local)

        globus.files[REMOTE_LOG] += b"three\n"
        assert await tail(globus, local) == "one\ntwo\nthree"
        assert globus.range_reads[-1] == (REMOTE_LOG, 8, 13)

    async def test_a_poll_that_finds_nothing_new_asks_for_nothing(
        self, globus, tmp_path
    ):
        """Back-to-back status calls in one chat turn were what the 30-second
        log cache existed to make cheap. A `HEAD` that reports the size we
        already hold is cheaper than the cache was, and never stale."""
        globus.files[REMOTE_LOG] = b"one\ntwo\n"
        local = tmp_path / "log-1.out"
        await tail(globus, local)
        globus.range_reads.clear()

        assert await tail(globus, local) == "one\ntwo"
        assert globus.range_reads == []
        assert globus.stats == [REMOTE_LOG, REMOTE_LOG]

    async def test_a_shorter_remote_file_is_read_again_from_the_start(
        self, globus, tmp_path
    ):
        """A rerun writing to the same path, or a truncation. Appending to what
        is already local would splice two different logs into one file that
        never existed."""
        globus.files[REMOTE_LOG] = b"old and long\n"
        local = tmp_path / "log-1.out"
        await tail(globus, local)

        globus.files[REMOTE_LOG] = b"new\n"
        assert await tail(globus, local) == "new"
        assert globus.range_reads[-1] == (REMOTE_LOG, 0, 3)
        assert local.read_bytes() == b"new\n"

    async def test_an_empty_log_is_not_a_failure(self, globus, tmp_path):
        """Slurm opens the file before anything writes to it."""
        globus.files[REMOTE_LOG] = b""

        assert await tail(globus, tmp_path / "log-1.out") == ""
        assert globus.range_reads == []


class TestWhichEndIsReturned:
    def test_the_last_lines_are_what_come_back(self, tmp_path):
        """A traceback is at the end. The 200 lines of module loading that a
        head read spent itself on are not what anyone is looking for."""
        local = tmp_path / "log.out"
        local.write_text("".join(f"line {i}\n" for i in range(500)), encoding="utf-8")

        lines = _read_log_tail(local).splitlines()

        assert len(lines) == submit_job_mcp._LOG_TAIL_LINES
        assert lines[-1] == "line 499"

    def test_a_short_log_comes_back_whole(self, tmp_path):
        local = tmp_path / "log.out"
        local.write_text("only\nthese\n", encoding="utf-8")

        assert _read_log_tail(local) == "only\nthese"

    def test_a_huge_log_is_not_read_off_disk_in_full(self, tmp_path):
        """The accumulated file keeps everything; this is a ceiling on the read,
        not on what was fetched."""
        local = tmp_path / "log.out"
        local.write_bytes(
            b"x" * (submit_job_mcp._LOG_TAIL_BYTES * 2) + b"\nlast line\n"
        )

        tail_text = _read_log_tail(local)

        assert tail_text.endswith("last line")
        assert len(tail_text) <= submit_job_mcp._LOG_TAIL_BYTES

    def test_a_partial_first_line_is_dropped(self, tmp_path):
        """The read window opens mid-line. Shown as-is it reads as corrupt
        output rather than as a window onto a larger file."""
        local = tmp_path / "log.out"
        filler = b"a" * submit_job_mcp._LOG_TAIL_BYTES
        local.write_bytes(filler + b"\ncomplete line\n")

        assert _read_log_tail(local) == "complete line"


class TestWhatAStatusQuerySays:
    @pytest.fixture(autouse=True)
    def _olcf(self, monkeypatch, globus):
        monkeypatch.setattr(settings, "frontier_globus_collection_id", COLLECTION)

        async def _noop_access(cfg, cluster):
            return None

        async def _iri(cluster, cfg):
            return FakeIriClient(status={"state": "RUNNING"})

        monkeypatch.setattr(submit_job_mcp, "_require_olcf_access", _noop_access)
        monkeypatch.setattr(submit_job_mcp, "_create_olcf_iri_for", _iri)
        monkeypatch.setattr(
            submit_job_mcp, "create_globus_client", lambda **kwargs: globus
        )
        _submitted_jobs.clear()
        _record_submitted_job(
            "1",
            SubmittedJob(
                cluster="frontier",
                log_path=REMOTE_LOG,
                output_dir="/lustre/orion/chm243/proj-shared/vista/out/1",
            ),
        )
        yield
        _submitted_jobs.clear()

    async def status(self, tmp_path: Path) -> str:
        return await _get_olcf_job_status(
            UserConfig(
                frontier_s3m_token="s3m",
                frontier_globus_token="t",
                frontier_globus_https_token="h",
            ),
            tmp_path,
            "1",
            cluster="frontier",
        )

    async def test_a_log_that_does_not_exist_yet_is_no_logs_yet(self, tmp_path):
        """The job is running but Slurm has not opened the file. Genuinely
        nothing to show, and the correct thing to say."""
        text = await self.status(tmp_path)

        assert "(no logs yet)" in text
        assert "STATE=RUNNING" in text

    async def test_the_tail_appears_under_the_logs_heading(self, globus, tmp_path):
        globus.files[REMOTE_LOG] = b"Traceback\nValueError: no\n"

        text = await self.status(tmp_path)

        assert "--- LOGS ---" in text
        assert "ValueError: no" in text

    async def test_an_expired_session_is_never_reported_as_an_empty_job(
        self, globus, monkeypatch, tmp_path
    ):
        """The bug, exactly. A job that queued past the 3-day High Assurance
        timeout, ran, and succeeded used to come back as "no output files yet",
        which reads as a job that failed. It has to name the credential."""

        async def _expired(**kwargs):
            raise GlobusSessionExpired(
                "Your Globus session for Frontier has expired. Reconnect Globus "
                "for Frontier in the VISTA user settings."
            )

        monkeypatch.setattr(globus, "stat", _expired)

        with pytest.raises(GlobusSessionExpired) as refusal:
            await self.status(tmp_path)

        message = str(refusal.value)
        assert "Frontier" in message
        assert "settings" in message.lower()
        assert "no output files yet" not in message

    async def test_an_unconnected_globus_does_not_cost_the_job_state(
        self, monkeypatch, tmp_path
    ):
        """Whether the job succeeded is knowable from IRI alone. Refusing the
        whole query because the credential is missing would hide that behind a
        credential problem -- the same confusion this change exists to remove,
        pointing the other way.

        The case that makes it real: a researcher who connected Globus before
        VISTA moved to the HTTPS interface has half a credential, which is not
        one. They should still be able to see that their job finished."""
        for field in (
            "frontier_globus_refresh_token",
            "frontier_globus_https_refresh_token",
        ):
            monkeypatch.setattr(settings, field, None)

        text = await _get_olcf_job_status(
            UserConfig(frontier_s3m_token="s3m", frontier_globus_token="stale"),
            tmp_path,
            "1",
            cluster="frontier",
        )

        assert "STATE=RUNNING" in text
        assert "settings" in text.lower()
        # And it says so under BOTH headings. The output listing needs the same
        # credential the log tail does, so leaving it at "(no output files
        # yet)" would print the original bug's sentence for a job that is fine.
        assert "no output files yet" not in text
        assert "output files unavailable" in text

    async def test_a_listing_that_failed_is_not_reported_as_an_empty_dir(
        self, globus, monkeypatch, tmp_path
    ):
        """A recursive walk answers a directory that does not exist with an
        empty list, so anything raised here is a real failure -- a 403, a
        collection that is down -- and reads as a job that produced nothing
        unless it is passed on."""

        async def _refused(**kwargs):
            raise RuntimeError("permission denied on /lustre/orion/chm243")

        monkeypatch.setattr(globus, "operation_ls", _refused)

        text = await self.status(tmp_path)

        assert "STATE=RUNNING" in text
        assert "permission denied" in text
        assert "no output files yet" not in text

    async def test_a_job_that_wrote_nothing_yet_is_still_no_output_files_yet(
        self, globus, tmp_path
    ):
        """The other side of it: an output dir that is genuinely empty, or not
        created yet, must keep its plain answer. Turning every empty listing
        into a warning would be the same noise pointing the other way."""
        text = await self.status(tmp_path)

        assert "(no output files yet)" in text

    async def test_the_listing_drops_excluded_dirs_but_not_lookalike_names(
        self, globus, tmp_path
    ):
        """`.git` and `__pycache__` are noise as directories, but a file that
        merely contains the string -- `.gitignore`, `run.github.log` -- is output
        the researcher asked for, and must not vanish from the listing."""
        out = "/lustre/orion/chm243/proj-shared/vista/out/1"
        globus.ls_entries[out] = [
            {"type": "file", "path": f"{out}/{rel}"}
            for rel in (
                "results.json",
                ".gitignore",
                "run.github.log",
                ".git/HEAD",
                "src/__pycache__/a.cpython-312.pyc",
            )
        ]

        text = await self.status(tmp_path)

        assert "results.json" in text
        assert ".gitignore" in text
        assert "run.github.log" in text
        assert ".git/HEAD" not in text
        assert "a.cpython-312.pyc" not in text

    async def test_a_job_with_no_output_dir_says_that_much(self, globus, tmp_path):
        """Nothing was ever recorded to list. Distinct from an empty one."""
        _submitted_jobs.clear()
        _record_submitted_job(
            "1", SubmittedJob(cluster="frontier", log_path=REMOTE_LOG, output_dir=None)
        )

        text = await self.status(tmp_path)

        assert "(no output directory recorded for this job)" in text

    async def test_an_unreachable_collection_still_reports_the_job_state(
        self, globus, monkeypatch, tmp_path
    ):
        """Not every failure is the credential. A log VISTA could not fetch for
        some other reason should not cost the researcher the job's state too."""

        async def _boom(**kwargs):
            raise RuntimeError("collection is down")

        monkeypatch.setattr(globus, "stat", _boom)

        text = await self.status(tmp_path)

        assert "STATE=RUNNING" in text
        assert "collection is down" in text
