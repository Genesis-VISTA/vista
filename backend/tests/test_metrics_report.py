"""
Tests for scripts/metrics_report.py (evaluation plan M8).

The metrics fixture is generated through `MetricsRecorder` itself — not
hand-written JSON — so the writer (M1) and the offline reader (M8) are
pinned to the same schema: a schema change that breaks the join breaks
this test, not the paper deadline.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from vista_backend.metrics import MetricsRecorder, MetricsSettings, run_context

_SPEC = importlib.util.spec_from_file_location(
    "metrics_report", Path(__file__).parents[1] / "scripts" / "metrics_report.py"
)
report = importlib.util.module_from_spec(_SPEC)
sys.modules["metrics_report"] = report
_SPEC.loader.exec_module(report)


class FakeUsage:
    requests = 4
    tool_calls = 6
    input_tokens = 900
    output_tokens = 100
    cache_read_tokens = 0
    cache_write_tokens = 0


@pytest.fixture
def logs(tmp_path):
    """One run's worth of metrics events (via the real recorder) plus one
    provenance gate event in the ProvenanceEmitter's JSONL format."""
    metrics_path = tmp_path / "metrics.jsonl"
    rec = MetricsRecorder(MetricsSettings(level="perf"), default_log_path=metrics_path)
    with run_context(session_id="proj-alice", project_id="proj") as run_id:
        for ms, status in (
            (100.0, "ok"),
            (200.0, "ok"),
            (300.0, "error"),
            (400.0, "ok"),
        ):
            rec.tool_call(tool_name="rag_search", duration_ms=ms, status=status)
        rec.stage(stage="rag.total", duration_ms=42.0)
        rec.agent_run(
            duration_ms=5000.0,
            stop_reason="completed",
            usage=FakeUsage(),
            tool_calls=4,
            human_interventions=2,
            skills_loaded=["salt-analysis"],
        )

    provenance_path = tmp_path / "provenance.jsonl"
    provenance_path.write_text(
        json.dumps(
            {
                "event_type": "gate_decision",
                "timestamp": "2026-06-12T00:00:00.000Z",
                "session_id": "proj-alice",
                "gate": "G2",
                "decision": {"allow": True, "reason": "clean"},
                "duration_ms": 1.5,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return metrics_path, provenance_path, run_id


def test_percentile_linear_interpolation():
    assert report.percentile([100.0, 200.0, 300.0, 400.0], 50) == 250.0
    assert report.percentile([100.0, 200.0, 300.0, 400.0], 95) == pytest.approx(385.0)
    assert report.percentile([7.0], 99) == 7.0


def test_summarize_durations_per_event_and_per_tool(logs):
    metrics_path, _, _ = logs
    stats = report.summarize_durations(report.load_events([metrics_path]))

    s = stats["tool_call.client"]
    assert (s.count, s.mean, s.p50) == (4, 250.0, 250.0)
    assert s.p95 == pytest.approx(385.0)
    assert s.p99 == pytest.approx(397.0)
    assert s.ok_rate == 0.75

    # Per-tool key for the Table III RAG row.
    assert stats["tool_call.client:rag_search"].count == 4
    assert stats["stage.rag.total"].mean == 42.0


def test_summarize_runs(logs):
    metrics_path, _, _ = logs
    runs = report.summarize_runs(report.load_events([metrics_path]))
    assert runs["runs"] == 1
    assert runs["completion_rate"] == 1.0
    assert runs["mean_iterations"] == 4
    assert runs["mean_interventions"] == 2
    assert runs["total_tokens"] == 1000
    assert runs["sessions"] == 1


def test_summarize_gates_and_join(logs):
    metrics_path, provenance_path, run_id = logs
    metrics = report.load_events([metrics_path])
    provenance = report.load_events([provenance_path])

    gates = report.summarize_gates(provenance)
    assert gates["G2"] == {"count": 1, "allowed": 1, "mean_ms": 1.5, "p95_ms": 1.5}

    coverage = report.join_coverage(metrics, provenance)
    assert coverage["shared_sessions"] == 1
    assert coverage["runs"] == 1
    assert {e.get("run_id") for e in metrics} == {run_id}


def test_render_latex_rows(logs):
    metrics_path, _, _ = logs
    stats = report.summarize_durations(report.load_events([metrics_path]))
    latex = report.render_latex(stats)
    assert (
        "Tool-call latency (mean/p95) & instrumented MCP layer & 250 / 385\\,ms \\\\"
        in latex
    )
    assert (
        "RAG retrieval latency (mean/p95) & instrumented & 42 / 42\\,ms \\\\" in latex
    )
    assert "Agent tool-call success rate & trace audit & 75.0\\,\\% \\\\" in latex
    # No HPC events in the fixture -> placeholder, never a silent zero.
    assert (
        "HPC submission latency (mean/p95) & S3M/IRI round-trip & \\todo{} \\\\"
        in latex
    )


# ---------------------------------------------------------------------------
# M10: deployment statistics (--deployment)
# ---------------------------------------------------------------------------


@pytest.fixture
def deployment_db(tmp_path):
    """A file-based SQLite DB created from the real SQLModel schema, so the
    raw SQL in deployment_db_stats() breaks here if the schema drifts."""
    import uuid as uuid_module

    from sqlalchemy import create_engine
    from sqlmodel import SQLModel, Session

    from vista_backend.db.schemas import (
        ProjectMemberTable,
        ProjectTable,
        SkillTable,
        UserTable,
    )

    db_path = tmp_path / "vista.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        alice = UserTable(
            id=uuid_module.uuid4(), email="alice@example.com", is_admin=True
        )
        bob = UserTable(id=uuid_module.uuid4(), email="bob@example.com")
        project = ProjectTable(name="salt", skills=["salt-analysis", "salt-prediction"])
        session.add_all([alice, bob, project])
        session.flush()
        session.add_all(
            [
                ProjectMemberTable(project_id=project.id, user_id=alice.id),
                ProjectMemberTable(project_id=project.id, user_id=bob.id),
                SkillTable(
                    name="salt-analysis", description="d", path="skills/salt-analysis"
                ),
                SkillTable(
                    name="alloy-design",
                    description="d",
                    path="skills/alloy-design",
                    is_public=True,
                ),
            ]
        )
        session.commit()
    engine.dispose()
    return db_path


def test_deployment_db_stats_pins_schema(deployment_db):
    stats = report.deployment_db_stats(deployment_db)
    assert stats == {
        "users": 2,
        "projects": 1,
        "project_memberships": 2,
        "skills": 2,
        "skills_published": 1,
        "knowledge_bases": 0,
        "skill_installs": 2,
    }


def test_deployment_db_stats_missing_db(tmp_path):
    assert report.deployment_db_stats(tmp_path / "nope.db") == {}


def test_deployment_event_stats(tmp_path):
    metrics_path = tmp_path / "metrics.jsonl"
    rec = MetricsRecorder(MetricsSettings(level="perf"), default_log_path=metrics_path)
    with run_context(session_id="proj-alice", project_id="proj"):
        rec.tool_call(tool_name="submit_hpc_job", duration_ms=900.0)
        rec.tool_call(tool_name="submit_hpc_job", duration_ms=900.0)
        rec.tool_call(tool_name="submit_hpc_job", duration_ms=10.0, status="error")
        rec.stage(stage="hpc.submit", duration_ms=850.0, payload={"cluster": "odo"})
        rec.agent_run(duration_ms=2000.0, stop_reason="completed")
    with run_context(session_id="proj2-bob", project_id="proj2"):
        rec.agent_run(duration_ms=500.0, stop_reason="completed")

    usage = report.deployment_event_stats(report.load_events([metrics_path]))
    assert usage["agent_runs"] == 2
    assert usage["active_sessions"] == 2
    assert usage["active_projects"] == 2
    # Client events are the single counting source (the stage event is the
    # same job observed server-side, never tallied twice)...
    assert usage["jobs_submitted"] == 2
    assert usage["jobs_failed"] == 1
    # ...while facility attribution comes from the stage payload.
    assert usage["jobs_per_facility"] == {"odo": 1}
    assert usage["campaigns"] == 1  # only proj-alice submitted
    assert usage["largest_campaign_jobs"] == 2
    assert usage["node_hours"] is None


def test_render_deployment(deployment_db, tmp_path):
    metrics_path = tmp_path / "metrics.jsonl"
    rec = MetricsRecorder(MetricsSettings(level="perf"), default_log_path=metrics_path)
    with run_context(session_id="s", project_id="p"):
        rec.tool_call(tool_name="submit_hpc_job", duration_ms=900.0)
    md = report.render_deployment(
        report.deployment_db_stats(deployment_db),
        report.deployment_event_stats(report.load_events([metrics_path])),
    )
    assert "| users | 2 |" in md
    assert "| jobs_per_facility | unknown: 1 |" in md
    assert "Deployment: 2 users, 1 projects, 2 skills (1 published, 2 installs)" in md


def test_render_markdown_smoke(logs):
    metrics_path, provenance_path, _ = logs
    metrics = report.load_events([metrics_path])
    provenance = report.load_events([provenance_path])
    md = report.render_markdown(
        report.summarize_durations(metrics),
        report.summarize_runs(metrics),
        report.summarize_gates(provenance),
        report.join_coverage(metrics, provenance),
    )
    assert "| tool_call.client | 4 | 75.0% | 250.0 | 250.0 | 385.0 | 397.0 |" in md
    assert "completion rate: 100.0%" in md
    assert "| G2 | 1 | 100.0% | 1.50 | 1.50 |" in md
