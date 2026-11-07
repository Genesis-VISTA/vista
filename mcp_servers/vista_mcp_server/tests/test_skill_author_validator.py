"""The `hpc-skill-author` skill's validator (`scripts/validate.py`) against the
rules `submit_hpc_job` applies.

The validator runs in the agent's sandbox, where this package is not
installed, so it re-implements the job contract. These tests keep the two in
step: anything `_load_job` refuses the validator refuses, a job VISTA ships
passes it, and a skill made from the skill's templates validates and then
resolves as `<skill>/<job>`.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from vista_mcp_server.submit_job_mcp import _load_job, resolve_job

REPO_ROOT = Path(__file__).resolve().parents[3]
AUTHOR_SKILL = (
    REPO_ROOT
    / "backend"
    / "src"
    / "vista_backend"
    / "db"
    / "skills"
    / "hpc-skill-author"
)
SHIPPED_SKILLS = AUTHOR_SKILL.parent

pytestmark = pytest.mark.unit


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "hpc_skill_validate", AUTHOR_SKILL / "scripts" / "validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate = _load_validator()


def _check_job(job_dir: Path):
    rep = validate.Report(job_dir)
    validate.check_job(job_dir, rep)
    return rep


def _make_skill(root: Path, skill: str = "demo-skill", job: str = "hello") -> Path:
    """A skill made from the templates, the way SKILL.md tells the agent to."""
    skill_dir = root / skill
    (skill_dir / "hpc_jobs").mkdir(parents=True)
    shutil.copy(
        AUTHOR_SKILL / "templates" / "skill" / "SKILL.md", skill_dir / "SKILL.md"
    )
    shutil.copytree(AUTHOR_SKILL / "templates" / "job", skill_dir / "hpc_jobs" / job)
    for path in skill_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            path.write_text(
                text.replace("__SKILL__", skill).replace("__JOB__", job),
                encoding="utf-8",
            )
    return skill_dir


def _run(argv: list[str], capsys) -> tuple[int, dict]:
    code = validate.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_template_skill_validates_and_resolves(tmp_path, capsys):
    skill_dir = _make_skill(tmp_path)
    code, report = _run([str(skill_dir)], capsys)
    assert code == 0, report
    assert report["errors"] == []
    assert report["warnings"] == []
    assert report["jobs"] == ["demo-skill/hello"]

    info = resolve_job("demo-skill/hello", tmp_path)
    assert info.skill == "demo-skill"
    assert info.job_dir == (skill_dir / "hpc_jobs" / "hello").resolve()
    assert info.cluster_defaults.lux is not None
    assert info.cluster_defaults.lux.iri.partition


def _shipped_jobs() -> list[Path]:
    return sorted(
        p
        for p in [
            *(REPO_ROOT / "hpc_jobs").iterdir(),
            *SHIPPED_SKILLS.glob("*/hpc_jobs/*"),
        ]
        if p.is_dir() and not p.name.startswith(".")
    )


@pytest.mark.parametrize(
    "job_dir", _shipped_jobs(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_every_job_vista_ships_passes(job_dir: Path):
    """Stricter than VISTA is fine; refusing a job VISTA runs is not."""
    assert _check_job(job_dir).errors == []


def _write(job: Path, files: dict[str, str]) -> Path:
    job.mkdir(parents=True)
    for name, text in files.items():
        (job / name).write_text(text, encoding="utf-8")
    return job


LUX_DEFAULTS = json.dumps(
    {"lux": {"resources": {"gpus_per_node": 1}, "iri": {"partition": "batch"}}}
)
SCRIPT = '#!/bin/bash\n: "${VISTA_OUT:?}"\necho hi > "$VISTA_OUT/x"\n'

BROKEN = {
    "wrong-heading": {
        "README.md": "# other\n",
        "job.lux.slurm": SCRIPT,
        "cluster_defaults.json": LUX_DEFAULTS,
    },
    "no-readme": {"job.lux.slurm": SCRIPT, "cluster_defaults.json": LUX_DEFAULTS},
    "no-script": {"README.md": "# no-script\n", "cluster_defaults.json": LUX_DEFAULTS},
    "bad-json": {
        "README.md": "# bad-json\n",
        "job.lux.slurm": SCRIPT,
        "cluster_defaults.json": "{lux:",
    },
    "bad-type": {
        "README.md": "# bad-type\n",
        "job.lux.slurm": SCRIPT,
        "cluster_defaults.json": json.dumps({"lux": {"duration": "an hour"}}),
    },
    "bad-nested-type": {
        "README.md": "# bad-nested-type\n",
        "job.odo.slurm": SCRIPT,
        "cluster_defaults.json": json.dumps(
            {"odo": {"iri": {"environment": {"N": 3}}}}
        ),
    },
    # Setup belongs in setup_<cluster>.sh, which runs in the job; a stale
    # pre_launch would otherwise be dropped without a word.
    "pre-launch": {
        "README.md": "# pre-launch\n",
        "job.odo.slurm": SCRIPT,
        "cluster_defaults.json": json.dumps({"odo": {"iri": {"pre_launch": "module load x"}}}),
    },
}


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_what_load_job_refuses_the_validator_refuses(tmp_path, name):
    job = _write(tmp_path / name, BROKEN[name])
    with pytest.raises(Exception):  # noqa: B017 - ValueError or pydantic's ValidationError
        _load_job(job, name, dir=job)
    assert _check_job(job).errors, name


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (
            {"README.md": "# j\n", "job.lux.slurm": SCRIPT},
            "no cluster_defaults.json",
        ),
        (
            {
                "README.md": "# j\n",
                "job.lux.slurm": SCRIPT,
                "cluster_defaults.json": '{"odo": {}}',
            },
            "no 'lux' section",
        ),
        (
            {
                "README.md": "# j\n",
                "job.lux.slurm": SCRIPT,
                "cluster_defaults.json": '{"lux": {}}',
            },
            "lux.iri.partition is required",
        ),
        (
            {
                "README.md": "# j\n",
                "job.lux.slurm": SCRIPT,
                "cluster_defaults.json": json.dumps(
                    {
                        "lux": {
                            "resources": {"gpus_per_node": 8, "processes_per_node": 3},
                            "iri": {"partition": "batch"},
                        }
                    }
                ),
            },
            "must be a multiple of processes_per_node",
        ),
    ],
)
def test_submission_time_rules_are_errors(tmp_path, files, expected):
    """`_load_job` accepts these; submitting to the cluster then fails, so the
    validator says so first."""
    job = _write(tmp_path / "j", files)
    _load_job(job, "j", dir=job)
    errors = _check_job(job).errors
    assert any(expected in e for e in errors), errors


def test_contract_advice_is_a_warning(tmp_path):
    job = _write(
        tmp_path / "j",
        {
            "README.md": "# j\n",
            "job.lux.slurm": '#!/bin/bash\necho "$1" > "$RUN_DIR_Lux/out"\n',
            "cluster_defaults.json": LUX_DEFAULTS,
        },
    )
    rep = _check_job(job)
    assert rep.errors == []
    text = "\n".join(rep.warnings)
    assert "never uses $VISTA_OUT" in text
    assert "allow-list" in text
    assert "read-only" in text


def test_skill_level_rules(tmp_path, capsys):
    skill_dir = _make_skill(tmp_path, skill="demo-skill")
    renamed = skill_dir.rename(tmp_path / "other-name")
    (renamed / "hpc_jobs" / "hello" / "link").symlink_to("/etc/hosts")
    (renamed / "hpc_jobs" / "second").mkdir()
    shutil.copytree(
        renamed / "hpc_jobs" / "hello",
        renamed / "hpc_jobs" / "second",
        dirs_exist_ok=True,
        symlinks=True,
    )
    code, report = _run([str(renamed)], capsys)
    assert code == 1
    errors = "\n".join(report["errors"])
    assert "but the folder is 'other-name'" in errors
    assert "is a symlink" in errors
    assert "does not name the job 'other-name/second'" in errors
    assert 'must start with the line "# second"' in errors


def test_metadata_must_be_strings(tmp_path, capsys):
    pytest.importorskip("yaml")
    skill_dir = _make_skill(tmp_path)
    md = skill_dir / "SKILL.md"
    md.write_text(
        md.read_text(encoding="utf-8").replace(
            "metadata:\n", "metadata:\n  version: 0.1\n"
        ),
        encoding="utf-8",
    )
    code, report = _run([str(skill_dir)], capsys)
    assert code == 1
    assert any("metadata values must be strings" in e for e in report["errors"])


# --- the job template, filled in per cluster, and the hello skills built from it ---

TEMPLATE_CLUSTERS = ("odo", "frontier", "lux")


def _fill_for(skill_dir: Path, cluster: str, job: str = "hello") -> None:
    """What the template's README tells the agent: delete the clusters the job
    does not target, with their scripts and `cluster_defaults.json` sections."""
    job_dir = skill_dir / "hpc_jobs" / job
    for other in TEMPLATE_CLUSTERS:
        if other != cluster:
            (job_dir / f"job.{other}.slurm").unlink()
            (job_dir / f"setup_{other}.sh").unlink(missing_ok=True)
    path = job_dir / "cluster_defaults.json"
    defaults = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({cluster: defaults[cluster]}), encoding="utf-8")


@pytest.mark.parametrize("cluster", TEMPLATE_CLUSTERS)
def test_template_filled_in_for_one_cluster_passes(tmp_path, capsys, cluster):
    skill_dir = _make_skill(tmp_path)
    _fill_for(skill_dir, cluster)
    code, report = _run([str(skill_dir)], capsys)
    assert (code, report["errors"], report["warnings"]) == (0, [], [])

    job_dir = skill_dir / "hpc_jobs" / "hello"
    assert [c for c in TEMPLATE_CLUSTERS if (job_dir / f"job.{c}.slurm").is_file()] == [
        cluster
    ]
    info = resolve_job("demo-skill/hello", tmp_path)
    assert getattr(info.cluster_defaults, cluster) is not None
    script = (job_dir / f"job.{cluster}.slurm").read_text(encoding="utf-8")
    assert f"RUN_DIR_{cluster.title()}" in script
    assert "run.log" in script and "summary.txt" in script


def test_template_with_all_three_clusters_passes(tmp_path, capsys):
    code, report = _run([str(_make_skill(tmp_path))], capsys)
    assert (code, report["errors"], report["warnings"]) == (0, [], [])
    defaults = json.loads(
        (AUTHOR_SKILL / "templates" / "job" / "cluster_defaults.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(defaults) == set(TEMPLATE_CLUSTERS)


@pytest.mark.parametrize("cluster", TEMPLATE_CLUSTERS)
def test_hello_skill_passes_the_validator(capsys, cluster):
    skill_dir = SHIPPED_SKILLS / f"hello-{cluster}"
    code, report = _run([str(skill_dir)], capsys)
    assert (code, report["errors"], report["warnings"]) == (0, [], [])
    assert report["jobs"] == [f"hello-{cluster}/hello"]

    # One job, for its own cluster only: its script and defaults, the input and a README.
    job_dir = skill_dir / "hpc_jobs" / "hello"
    assert sorted(p.name for p in job_dir.iterdir()) == [
        "README.md",
        "cluster_defaults.json",
        "input.txt",
        f"job.{cluster}.slurm",
    ]
    assert list(
        json.loads((job_dir / "cluster_defaults.json").read_text(encoding="utf-8"))
    ) == [cluster]


@pytest.mark.parametrize("cluster", TEMPLATE_CLUSTERS)
def test_hello_skill_states_the_input_checksum_and_the_report(cluster):
    import hashlib

    skill_dir = SHIPPED_SKILLS / f"hello-{cluster}"
    job_dir = skill_dir / "hpc_jobs" / "hello"
    sha = hashlib.sha256((job_dir / "input.txt").read_bytes()).hexdigest()
    assert sha in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert sha in (job_dir / "README.md").read_text(encoding="utf-8")
    script = (job_dir / f"job.{cluster}.slurm").read_text(encoding="utf-8")
    for needed in (
        f"RUN_DIR_{cluster.title()}",
        "rocm-smi",
        "module list",
        "sha256sum",
        "SLURM_JOB_NODELIST",
        "hello.txt",
        "run.log",
        "summary.txt",
    ):
        assert needed in script, needed
