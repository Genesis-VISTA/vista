"""The `hpc-skill-author` skill's "wrap existing code" workflow: unpacking an
upload (`scripts/get_source.py`; repositories are fetched by the backend's
`fetch_repository` tool, tested in the backend), summarizing the code
(`scripts/inventory.py`), and the
`wrap-repo` job template, which clones the repository on the cluster at a pinned
commit. A skill wrapped this way must validate and resolve as `<skill>/<job>`.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from vista_mcp_server.submit_job_mcp import resolve_job

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
SCRIPTS = AUTHOR_SKILL / "scripts"

pytestmark = pytest.mark.unit


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"hpc_skill_{name}", SCRIPTS / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


get_source = _load("get_source")
inventory = _load("inventory")
validate = _load("validate")


def _tar_gz(members: dict[str, str], top: str | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in members.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"{top}/{name}" if top else name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


SOURCE_TREE = {
    "README.md": "# melt\n\nRun `sbatch run.sbatch`.\n",
    "requirements.txt": "numpy\n",
    "run.sbatch": "#!/bin/bash\n#SBATCH -N 2\n#SBATCH -t 01:00:00\n#SBATCH --gpus-per-node=8\n"
    "python melt.py --steps 100 --out /scratch/me/out\n",
    "melt.py": "import argparse, os\n"
    "p = argparse.ArgumentParser()\np.add_argument('--steps')\np.add_argument(\"--out\")\n"
    "SEED = os.environ.get('MELT_SEED')\nif __name__ == '__main__':\n    p.parse_args()\n",
}


class TestGetSource:
    def test_archive_with_one_top_folder(self, tmp_path):
        archive = tmp_path / "code.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            for name, text in SOURCE_TREE.items():
                zf.writestr(f"melt/{name}", text)
            zf.writestr("__MACOSX/melt/._melt.py", "junk")
        record = get_source.fetch(str(archive), tmp_path / "src")
        assert record["kind"] == "archive"
        assert sorted(p.name for p in (tmp_path / "src").iterdir()) == sorted(
            [*SOURCE_TREE, "SOURCE.json"]
        )

    @pytest.mark.parametrize("bad", ["../evil.sh", "/etc/evil", "a/../../evil"])
    def test_archive_paths_cannot_escape(self, tmp_path, bad):
        archive = tmp_path / "code.tar.gz"
        archive.write_bytes(_tar_gz({"ok.txt": "x", bad: "x"}))
        with pytest.raises(get_source.SourceError, match="outside the destination"):
            get_source.fetch(str(archive), tmp_path / "src")
        assert not (tmp_path / "evil.sh").exists()

    def test_archive_links_are_not_unpacked(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            link = tarfile.TarInfo("passwd")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            tar.addfile(link)
            data = b"print(1)\n"
            f = tarfile.TarInfo("run.py")
            f.size = len(data)
            tar.addfile(f, io.BytesIO(data))
        archive = tmp_path / "code.tgz"
        archive.write_bytes(buf.getvalue())
        get_source.fetch(str(archive), tmp_path / "src")
        assert not (tmp_path / "src" / "passwd").exists()
        assert (tmp_path / "src" / "run.py").is_file()

    def test_folder_copy_skips_links_and_clutter(self, tmp_path):
        src = tmp_path / "upload"
        (src / ".git").mkdir(parents=True)
        (src / ".git" / "HEAD").write_text("ref", encoding="utf-8")
        (src / "run.py").write_text("print(1)\n", encoding="utf-8")
        (src / "secret").symlink_to(tmp_path / "elsewhere")
        get_source.fetch(str(src), tmp_path / "src")
        assert sorted(p.name for p in (tmp_path / "src").iterdir()) == [
            "SOURCE.json",
            "run.py",
        ]

    def test_urls_and_non_empty_dest_are_refused(self, tmp_path):
        for url in (
            "https://github.com/o/r",
            "https://gitlab.com/g/r",
            "https://example.org/x",
        ):
            with pytest.raises(get_source.SourceError, match="fetch_repository tool"):
                get_source.fetch(url, tmp_path / "a")
        assert not (tmp_path / "a").exists()
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "x").write_text("x", encoding="utf-8")
        with pytest.raises(get_source.SourceError, match="not empty"):
            get_source.fetch(str(tmp_path), tmp_path / "b")


def test_inventory_finds_how_the_code_runs(tmp_path):
    for name, text in SOURCE_TREE.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    inv = inventory.inventory(tmp_path)
    assert inv["readmes"] == ["README.md"]
    assert inv["build"] == ["requirements.txt"]
    assert inv["batch_scripts"] == [
        {
            "path": "run.sbatch",
            "directives": ["-N 2", "-t 01:00:00", "--gpus-per-node=8"],
        }
    ]
    entries = {e["path"]: e["flags"] for e in inv["entry_points"]}
    assert entries["melt.py"] == ["--out", "--steps"]
    assert "MELT_SEED" in inv["env_vars"]
    assert "batch script: run.sbatch" in inventory.render(inv)


def _wrap_repo(
    root: Path, url: str, commit: str, skill="melt-on-lux", job="melt"
) -> Path:
    """What SKILL.md's "Ship it or clone it" step does with the wrap-repo template."""
    skill_dir = root / skill
    (skill_dir / "hpc_jobs").mkdir(parents=True)
    shutil.copy(
        AUTHOR_SKILL / "templates" / "skill" / "SKILL.md", skill_dir / "SKILL.md"
    )
    shutil.copytree(
        AUTHOR_SKILL / "templates" / "wrap-repo", skill_dir / "hpc_jobs" / job
    )
    for path in skill_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            for k, v in {
                "__SKILL__": skill,
                "__JOB__": job,
                "__REPO_URL__": url,
                "__REPO_COMMIT__": commit,
            }.items():
                text = text.replace(k, v)
            path.write_text(text, encoding="utf-8")
    return skill_dir


def test_wrapped_repo_validates_and_resolves(tmp_path, capsys):
    sha = "0123456789abcdef0123456789abcdef01234567"
    skill_dir = _wrap_repo(tmp_path, "https://github.com/o/melt.git", sha)
    assert validate.main([str(skill_dir), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["errors"] == [] and report["warnings"] == []

    info = resolve_job("melt-on-lux/melt", tmp_path)
    env = info.cluster_defaults.lux.iri.environment
    assert env == {"REPO_URL": "https://github.com/o/melt.git", "REPO_COMMIT": sha}
    # Only the wrapper is uploaded; the code itself is cloned on the cluster.
    assert sorted(p.name for p in info.job_dir.iterdir()) == [
        "README.md",
        "cluster_defaults.json",
        "job.lux.slurm",
        "setup_lux.sh",
    ]


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    ).stdout.strip()


@pytest.mark.skipif(
    not (shutil.which("git") and shutil.which("flock")),
    reason="needs git and flock, as on the Lux login node",
)
def test_setup_checks_out_the_pinned_commit_once(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git("init", "-q", cwd=upstream)
    (upstream / "melt.py").write_text("v1\n", encoding="utf-8")
    _git("add", ".", cwd=upstream)
    _git("commit", "-qm", "v1", cwd=upstream)
    pinned = _git("rev-parse", "HEAD", cwd=upstream)
    (upstream / "melt.py").write_text("v2\n", encoding="utf-8")
    _git("commit", "-qam", "v2", cwd=upstream)

    skill_dir = _wrap_repo(tmp_path / "skills", str(upstream), pinned)
    job = skill_dir / "hpc_jobs" / "melt"
    env = {
        **os.environ,
        "VISTA_JOB_DIR": str(tmp_path / "jobdir"),
        "REPO_URL": str(upstream),
        "REPO_COMMIT": pinned,
    }
    for _ in range(2):  # the second submission reuses the checkout
        subprocess.run(
            ["bash", str(job / "setup_lux.sh")],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    src = tmp_path / "jobdir" / "src" / pinned
    assert (src / "melt.py").read_text(encoding="utf-8") == "v1\n"
    assert not (tmp_path / "jobdir" / "src" / f"{pinned}.partial").exists()


def test_wrapped_job_script_runs_and_refuses_unknown_args(tmp_path):
    sha = "abc123"
    skill_dir = _wrap_repo(tmp_path / "skills", "https://github.com/o/melt.git", sha)
    script = skill_dir / "hpc_jobs" / "melt" / "job.lux.slurm"
    (tmp_path / "jobdir" / "src" / sha).mkdir(parents=True)
    (tmp_path / "out").mkdir()
    env = {
        **os.environ,
        "VISTA_OUT": str(tmp_path / "out"),
        "VISTA_JOB_DIR": str(tmp_path / "jobdir"),
        "REPO_COMMIT": sha,
        "REPO_URL": "https://github.com/o/melt.git",
    }
    ok = subprocess.run(
        ["bash", str(script), "STEPS=3"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert ok.returncode == 0, ok.stderr
    log = (tmp_path / "out" / "run.log").read_text(encoding="utf-8")
    assert f"source: https://github.com/o/melt.git @ {sha}" in log
    assert "steps: 3" in log
    bad = subprocess.run(
        ["bash", str(script), "--steps=3"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert bad.returncode == 2
    assert "unknown argument" in bad.stderr


def test_get_source_cli_prints_the_inventory(tmp_path):
    archive = tmp_path / "code.tar.gz"
    archive.write_bytes(_tar_gz(SOURCE_TREE))
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "get_source.py"),
            str(archive),
            "--dest",
            str(tmp_path / "src"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.startswith('SOURCE {"kind": "archive"')
    assert "entry point: melt.py flags: --out --steps" in run.stdout


def _fake_bin(tmp_path: Path) -> tuple[Path, Path]:
    """`conda`, `lfs` and `flock` stand-ins that log how they were called."""
    bin_dir, log = tmp_path / "bin", tmp_path / "calls.log"
    bin_dir.mkdir()
    (bin_dir / "conda").write_text(
        "#!/bin/bash\n"
        f'echo "conda $* | pkgs=$CONDA_PKGS_DIRS tmp=$TMPDIR proxy=$https_proxy" >> "{log}"\n'
        'prefix=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = --prefix ] && prefix=$a; prev=$a; done\n'
        'case "$1 $2" in\n'
        '  "create "*|"env create") mkdir -p "$prefix/bin" ;;\n'
        '  "env export") echo "name: x" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    (bin_dir / "lfs").write_text(
        f'#!/bin/bash\necho "lfs $*" >> "{log}"\n', encoding="utf-8"
    )
    (bin_dir / "flock").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    return bin_dir, log


@pytest.mark.parametrize("spec", [False, True])
def test_conda_env_is_built_once_off_lustre(tmp_path, spec):
    """The template follows the skill's steps: proxy first, package cache and
    pip builds under /tmp, stripe count 1 on the env's folder, and a second
    run reuses the env instead of rebuilding it."""
    bin_dir, log = _fake_bin(tmp_path)
    script = AUTHOR_SKILL / "templates" / "conda-env" / "build_conda_env.sh"
    env = {
        **{
            k: v
            for k, v in os.environ.items()
            if k not in ("https_proxy", "http_proxy")
        },
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "USER": "tester",
        "ENV_PARENT": str(tmp_path / "jobdir" / "envs"),
        "ENV_NAME": "melt-py311",
    }
    if spec:
        (tmp_path / "environment.yml").write_text("name: melt\n", encoding="utf-8")
        env["ENV_SPEC"] = str(tmp_path / "environment.yml")
    runs = [
        subprocess.run(
            ["bash", str(script)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        for _ in range(2)
    ]
    assert [r.returncode for r in runs] == [0, 0], runs[0].stderr
    assert runs[0].stdout.strip().endswith("(built)")
    assert runs[1].stdout.strip().endswith("(already built)")

    calls = log.read_text(encoding="utf-8").splitlines()
    creates = [c for c in calls if c.startswith(("conda create", "conda env create"))]
    assert len(creates) == 1
    assert creates[0].startswith("conda env create" if spec else "conda create")
    assert "pkgs=/tmp/tester/conda_pkgs tmp=/tmp/tester/tmp" in creates[0]
    assert "proxy=http://proxy.ccs.ornl.gov:3128" in creates[0]
    assert f"lfs setstripe -c 1 {tmp_path / 'jobdir' / 'envs'}" in calls
    prefix = tmp_path / "jobdir" / "envs" / "melt-py311"
    assert (prefix / ".vista-env-ok").is_file()
    assert (prefix / "environment.lock.yml").read_text(encoding="utf-8") == "name: x\n"


def test_conda_build_on_lustre_is_a_warning(tmp_path):
    defaults = json.dumps(
        {"lux": {"resources": {"gpus_per_node": 1}, "iri": {"partition": "batch"}}}
    )
    body = '#!/bin/bash\n: "${VISTA_OUT:?}"\nconda create --yes --prefix "$VISTA_JOB_DIR/env" python\n'
    for name, script, warned in (
        ("plain", body, True),
        (
            "fixed",
            body.replace(
                "conda create", "export CONDA_PKGS_DIRS=/tmp/$USER/p\nconda create"
            ),
            False,
        ),
    ):
        job = tmp_path / name
        job.mkdir()
        (job / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        (job / "cluster_defaults.json").write_text(defaults, encoding="utf-8")
        (job / "job.lux.slurm").write_text(script, encoding="utf-8")
        rep = validate.Report(job)
        validate.check_job(job, rep)
        assert rep.errors == []
        assert any("off Lustre" in w for w in rep.warnings) is warned, rep.warnings
