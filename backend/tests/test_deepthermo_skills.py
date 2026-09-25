"""
Contracts for the DeepThermo pipeline skills (W5): deepthermo-wl + vae-orderparam.

The two couplings these guard are the ones the upstream workflow doc calls out as
failing SILENTLY: the padded grid size, and element ordering. Both are derived in the
job wrappers, so they are pinned here against the engine's own arithmetic.
"""

import importlib.util
import math
from pathlib import Path

import pytest

import vista_backend
from vista_backend.agents.skills import read_skill

SKILLS = Path(vista_backend.__file__).parent / "db" / "skills"
JOBS = Path(vista_backend.__file__).resolve().parents[3] / "hpc_jobs"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def stage():
    return _load(JOBS / "deepthermo-wl" / "run_stage.py", "dt_stage")


@pytest.fixture(scope="module")
def train():
    return _load(JOBS / "vae-orderparam" / "run_training.py", "dt_train")


# --- skills + jobs exist and parse -----------------------------------------


@pytest.mark.parametrize("name", ["deepthermo-wl", "vae-orderparam"])
def test_skill_parses_and_job_exists(name):
    skill = read_skill(SKILLS / name)
    assert skill.name == name
    assert (JOBS / name / "README.md").is_file()
    assert (JOBS / name / "cluster_defaults.json").is_file()
    # Odo + Frontier. Both run the job script ONCE on the head node, so the wrapper can
    # srun the PT ladder itself. Perlmutter is deliberately absent: its IRI dispatcher
    # runs the script once PER RANK inside shifter (see
    # hpc_jobs/forge-tune/job.perlmutter.slurm), which that design cannot support.
    assert (JOBS / name / "job.odo.slurm").is_file()
    assert (JOBS / name / "job.frontier.slurm").is_file()
    assert not (JOBS / name / "job.perlmutter.slurm").exists()


@pytest.mark.parametrize("name", ["deepthermo-wl", "vae-orderparam"])
def test_odo_script_sources_the_xforge_env_and_threads_the_workspace(name):
    """Odo gets PyTorch from the same xforge env forge-tune uses (sourced, not a module)."""
    text = (JOBS / name / "job.odo.slurm").read_text()
    assert "xforge-env.sh" in text, "Odo must source xforge-env.sh, not module-load it"
    assert "module load xforge" not in text, "that is the Frontier mechanism"
    assert "RUN_DIR_Odo" in text and "RUN_DIR_Frontier" not in text
    # The workspace name travels in script_args; the root comes from the cluster script.
    assert "--workspace-root" in text
    assert '"$@"' in text


# --- the padded-grid coupling ----------------------------------------------


def _reference_grid(n):
    """The engine's rule, transcribed from src/alloy.cc::ini_sys via the upstream doc."""
    shift = n - 1
    raw = 2 * (n - 1) + shift + 1
    pad = int((math.ceil(raw / 16) * 16 - raw) / 2)
    return raw + 2 * pad, shift + pad


@pytest.mark.parametrize("n", list(range(2, 33)))
def test_both_wrappers_agree_with_the_engine_grid_rule(stage, train, n):
    assert stage.engine_grid(n) == _reference_grid(n)
    assert train.engine_grid(n) == _reference_grid(n)


@pytest.mark.parametrize(
    "n,grid", [(4, 16), (5, 15), (6, 16), (10, 32), (16, 48), (20, 64)]
)
def test_grid_matches_the_published_table(stage, n, grid):
    """N=5 -> 15 is the case that breaks a 'round up to 16' implementation."""
    assert stage.engine_grid(n)[0] == grid


# --- config.toml generation -------------------------------------------------


def _args(stage_mod, **kw):
    import types

    base = dict(
        mode="collect",
        n=10,
        sh=6,
        nb_interaction=1,
        coupling_file="coupling.input",
        intercept=-1.27,
        z_r=0.1,
        bin_width=0.0055,
        e_min=-1.2808,
        e_max=-1.2770,
        flatness=0.6,
        mod_factor_init=1.0,
        iteration_factor=2.0,
        mod_factor_final=1e-6,
        production_bin_samps=10,
        t_init=10.0,
        t_final=2000.0,
        dt=0.1,
        samples=400,
        sep=10,
        drop=100,
        snapshot_stride=1,
        snapshot_lowe=False,
        thermo_t_init=100,
        thermo_t_final=3000,
        thermo_dt=2,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_collect_mode_short_circuits_the_wl_loop(stage):
    """initWL()'s relax loop must exit immediately so all the work is the PT warm-up."""
    cfg = stage.build_config(
        _args(stage, mode="collect"), ["Mo", "Nb", "Ta", "W"], [0.25] * 4
    )
    init = float(cfg.split("mod_factor_init      = ")[1].split("\n")[0])
    final = float(cfg.split("mod_factor_final     = ")[1].split("\n")[0])
    assert init < final, "collect mode must have mod_factor_init < mod_factor_final"
    assert "[output]" in cfg and "snapshot_stride" in cfg


def test_sample_mode_enables_the_wl_loop_and_captures_nothing(stage):
    cfg = stage.build_config(
        _args(stage, mode="sample"), ["Mo", "Nb", "Ta", "W"], [0.25] * 4
    )
    init = float(cfg.split("mod_factor_init      = ")[1].split("\n")[0])
    final = float(cfg.split("mod_factor_final     = ")[1].split("\n")[0])
    assert init > final, "sample mode must have mod_factor_init > mod_factor_final"
    assert "[output]" not in cfg, "production runs should not pay snapshot I/O"


def test_element_order_is_preserved_into_the_config(stage):
    """The one-hot channel is the index into this list; order is load-bearing."""
    cfg = stage.build_config(_args(stage), ["Ta", "W", "Mo", "Nb"], [0.25] * 4)
    assert 'elements       = ["Ta", "W", "Mo", "Nb"]' in cfg


# --- vae.dat parsing --------------------------------------------------------


def test_vae_dat_last_row_becomes_the_acceptance_metric(stage, tmp_path):
    f = tmp_path / "vae.dat"
    f.write_text(
        "# sweeps lnwlf att acc ratio\n100 1.0 1000 600 0.6\n200 0.5 29146 17275 0.593\n"
    )
    got = stage.parse_vae_dat(f)
    assert got["vae_attempts"] == 29146
    assert got["vae_accepts"] == 17275
    assert got["vae_acceptance"] == pytest.approx(17275 / 29146)


def test_missing_vae_dat_is_not_fatal(stage, tmp_path):
    assert stage.parse_vae_dat(tmp_path / "nope.dat") == {}


# --- training split ---------------------------------------------------------


def test_dedupe_removes_cold_replica_repeats_without_leaking(train, tmp_path):
    np = pytest.importorskip("numpy")
    frames = []
    for r in range(4):
        if r == 0:  # a frozen cold replica repeating one configuration
            base = np.ones((2, 2, 2, 2), dtype="int16")
            frames += [base] * 90 + [
                np.full((2, 2, 2, 2), i, dtype="int16") for i in range(10)
            ]
        else:
            frames += [
                np.full((2, 2, 2, 2), 100 * r + i, dtype="int16") for i in range(100)
            ]
    np.save(tmp_path / "in.npy", np.stack(frames))

    stats = train.dedupe_and_split(
        tmp_path / "in.npy",
        tmp_path / "tr.npy",
        tmp_path / "va.npy",
        n_ranks=4,
        val_fraction=0.1,
        seed=6,
    )
    assert stats["frames_total"] == 400
    assert stats["frames_unique"] < 400, (
        "duplicate cold-replica frames were not removed"
    )

    import hashlib

    tr = {hashlib.md5(x.tobytes()).digest() for x in np.load(tmp_path / "tr.npy")}
    va = np.load(tmp_path / "va.npy")
    assert not any(hashlib.md5(x.tobytes()).digest() in tr for x in va), (
        "val leaked into train"
    )


def test_unsplittable_dataset_is_rejected(train, tmp_path):
    """A list holding one empty array is truthy — the guard must check lengths."""
    np = pytest.importorskip("numpy")
    np.save(tmp_path / "tiny.npy", np.zeros((2, 2, 2, 2, 2), dtype="int8"))
    with pytest.raises(SystemExit, match="not enough frames"):
        train.dedupe_and_split(
            tmp_path / "tiny.npy",
            tmp_path / "a.npy",
            tmp_path / "b.npy",
            n_ranks=8,
            val_fraction=0.1,
            seed=1,
        )


def test_training_curve_parsing(train):
    log = "epoch   1/100  train=3338835.8074  val=5029.6387\nepoch 100/100  train=   1076.2     val=1088.9\n"
    curve = train.parse_training_curve(log)
    assert [c["epoch"] for c in curve] == [1, 100]
    assert curve[-1]["val"] == 1088.9


# --- the Wang-Landau window comes from data, not a borrowed constant ------------


def test_window_is_derived_from_the_engines_observed_energies(stage):
    """ptEmin/ptEmax are TOTAL energies; the [wang_landau] window is per-site."""
    out = "chatter\nptEmin = -1281.0321  ptEmax = -1277.6024\nmore\n"
    w = stage.observed_energy_window(out, 10)  # N=10 -> 1000 sites
    assert w["pt_e_total_DO_NOT_USE_AS_WINDOW"] == [-1281.0321, -1277.6024]
    assert w["pt_e_per_site"][0] == pytest.approx(-1.2810321)
    assert w["pt_e_per_site"][1] == pytest.approx(-1.2776024)


def test_suggested_window_sits_inside_the_observed_range(stage):
    """A window below the reachable ground state never converges and spins silently."""
    w = stage.observed_energy_window("ptEmin = -1281.0321  ptEmax = -1277.6024\n", 10)
    lo, hi = w["pt_e_per_site"]
    sw = w["suggested_window"]
    assert lo < sw["e_min"] < sw["e_max"] < hi


def test_bin_width_is_in_total_energy_units(stage):
    """
    wanglandau.cc: bins = (WLD1max - WLD1min) / (bin_width * invN), invN = 1/N^3.
    So bin_width is a TOTAL-energy quantity. Deriving it per-site instead would be
    wrong by a factor of N^3 and silently produce an unflattenable histogram.
    """
    w = stage.observed_energy_window("ptEmin = -1281.0321  ptEmax = -1277.6024\n", 10)
    sw = w["suggested_window"]
    sites = 1000
    bins = (sw["e_max"] - sw["e_min"]) / (sw["bin_width"] / sites)
    assert bins == pytest.approx(sw["approx_bins"], rel=0.02)
    # Same order as upstream's hand-picked 0.0055 for this system.
    assert 0.001 < sw["bin_width"] < 0.02


def test_no_energy_line_yields_no_window(stage):
    assert stage.observed_energy_window("nothing useful here", 10) is None
    assert stage.observed_energy_window("", 10) is None


def test_sample_mode_has_no_hardcoded_energy_window(stage):
    """The repo's example values are MoNbTaW@N=10 only; they must not be defaults."""
    src = (JOBS / "deepthermo-wl" / "run_stage.py").read_text()
    assert "default=-1.2808" not in src and "default=-1.2770" not in src


# --- total-vs-per-site: the failure reported from a real Odo run ----------------


@pytest.fixture
def recorded(stage):
    """What the collect stage records for MoNbTaW @ N=10 (upstream's own energies)."""
    return stage.observed_energy_window(
        "ptEmin = -1281.0321  ptEmax = -1277.6024\n", 10
    )


def test_total_energies_passed_as_a_window_are_rejected(stage, recorded):
    """
    The reported stage-3 failure: the engine prints TOTAL energies, but
    [wang_landau] e_min/e_max are PER SITE. Passing the totals yields a window ~N^3
    too wide that can never flatten, and the run spins until walltime instead of
    failing. This must be caught at submit time.
    """
    with pytest.raises(SystemExit) as exc:
        stage.validate_window(-1281.0321, -1277.6024, recorded)
    msg = str(exc.value)
    assert "TOTAL energies" in msg and "PER SITE" in msg
    # The error must hand back the numbers that would have worked.
    assert str(recorded["suggested_window"]["e_min"]) in msg


def test_the_derived_per_site_window_is_accepted(stage, recorded):
    sw = recorded["suggested_window"]
    stage.validate_window(sw["e_min"], sw["e_max"], recorded)  # must not raise


def test_window_outside_the_sampled_range_is_rejected(stage, recorded):
    """Every masked bin must be visited, so a non-overlapping window never converges."""
    with pytest.raises(SystemExit, match="does not overlap"):
        stage.validate_window(-1.35, -1.30, recorded)


def test_inverted_window_is_rejected(stage, recorded):
    with pytest.raises(SystemExit, match="must be below"):
        stage.validate_window(-1.277, -1.281, recorded)


def test_no_recorded_window_does_not_false_positive(stage):
    """With nothing to compare against, a sane window must still pass."""
    stage.validate_window(-1.2808, -1.2770, None)


def test_recorded_totals_are_named_so_they_cannot_be_mistaken(stage, recorded):
    """The field an agent might grab must be self-warning."""
    assert "pt_e_total_DO_NOT_USE_AS_WINDOW" in recorded
    assert "pt_e_total" not in recorded  # no innocuous-looking alias
    assert "PER SITE" in recorded["units_note"]


def test_skill_example_does_not_teach_passing_a_window():
    """The worked example taught the agent to supply e_min/e_max — that caused the bug."""
    text = (SKILLS / "deepthermo-wl" / "SKILL.md").read_text()
    sample_example = [ln for ln in text.splitlines() if "--mode sample" in ln]
    assert sample_example, "no sample-mode example found"
    for ln in sample_example:
        assert "--e-min" not in ln, f"example still passes an explicit window: {ln}"


# --- live progress: DOS files must be visible DURING a long run -----------------


def test_progress_mirror_copies_dos_and_diagnostics(stage, tmp_path):
    run, out = tmp_path / "run", tmp_path / "out"
    run.mkdir()
    out.mkdir()
    for n in ("DOS_H_iter001.dat", "DOS_H_iter002.dat", "vae.dat", "misc0.dat"):
        (run / n).write_text("x\n" * 10)
    copied = stage.sync_progress(run, out)
    assert set(copied) >= {"DOS_H_iter001.dat", "DOS_H_iter002.dat", "vae.dat"}


def test_progress_mirror_never_copies_snapshots(stage, tmp_path):
    """Snapshots stay in the workspace: $VISTA_OUT is recursively listed over Globus."""
    run, out = tmp_path / "run", tmp_path / "out"
    run.mkdir()
    out.mkdir()
    (run / "snap_0_0.xyz").write_text("big\n" * 1000)
    (run / "DOS_H_iter001.dat").write_text("ok\n")
    stage.sync_progress(run, out)
    assert not (out / "snap_0_0.xyz").exists()
    assert (out / "DOS_H_iter001.dat").exists()


def test_progress_mirror_is_idempotent(stage, tmp_path):
    """An unchanged file must not be re-copied every pass."""
    run, out = tmp_path / "run", tmp_path / "out"
    run.mkdir()
    out.mkdir()
    (run / "DOS_H_iter001.dat").write_text("x\n")
    assert stage.sync_progress(run, out) == ["DOS_H_iter001.dat"]
    assert stage.sync_progress(run, out) == []


def test_progress_mirror_refreshes_an_updated_file(stage, tmp_path):
    """DOS_H_iter<N>.dat is rewritten in place every 100 sweeps — updates must land."""
    import os
    import time

    run, out = tmp_path / "run", tmp_path / "out"
    run.mkdir()
    out.mkdir()
    f = run / "DOS_H_iter002.dat"
    f.write_text("first\n")
    stage.sync_progress(run, out)
    f.write_text("second\n")
    os.utime(f, (time.time() + 5, time.time() + 5))  # unambiguously newer
    assert stage.sync_progress(run, out) == ["DOS_H_iter002.dat"]
    assert (out / "DOS_H_iter002.dat").read_text() == "second\n"


def test_progress_mirror_is_bounded(stage, tmp_path):
    """Unbounded mirroring would make get_hpc_job_status' recursive Globus ls crawl."""
    run, out = tmp_path / "run", tmp_path / "out"
    run.mkdir()
    out.mkdir()
    for i in range(300):
        (run / f"DOS_H_iter{i:03d}.dat").write_text("y\n")
    assert len(stage.sync_progress(run, out)) <= stage.PROGRESS_MAX_FILES

    big = run / "DOS_H_iter999.dat"
    big.write_bytes(b"0" * (stage.PROGRESS_MAX_BYTES + 1))
    stage.sync_progress(run, out)
    assert not (out / "DOS_H_iter999.dat").exists()


# --- the workspace is unreachable from outside the job, so summarize it ---------


def test_workspace_inventory_reports_what_each_stage_left(stage, tmp_path):
    """
    get_hpc_job_outputs rejects ".." and absolute paths, so it can only reach
    $VISTA_OUT/<job_id>/. The workspace is a sibling and cannot be fetched at all —
    results.json carrying an inventory is the only way to answer "is there a trained
    model?" or "how many snapshots did collect make?" without running another job.
    """
    ws = tmp_path / "tc-n10"
    for sub, files in (
        ("collect", [f"snap_0_{i}.xyz" for i in range(8)] + ["misc0.dat"]),
        ("models", ["encoder_MoNbTaW.pt", "decoder_MoNbTaW.pt"]),
        ("sample", ["DOS_H_iter001.dat", "vae.dat"]),
    ):
        (ws / sub).mkdir(parents=True)
        for f in files:
            (ws / sub / f).write_text("x")

    inv = stage.workspace_inventory(ws)
    assert inv["exists"] is True
    assert inv["collect"]["n_files"] == 9
    assert inv["collect"]["truncated"] is True  # names are capped
    assert inv["models_present"] is True
    assert inv["encoders"] == ["encoder_MoNbTaW.pt"]
    assert inv["sample"]["n_files"] == 2


def test_inventory_flags_a_bootstrap_only_model_dir(stage, tmp_path):
    """bootstrap.pt beside the encoder means the pair may still be random-weight."""
    ws = tmp_path / "ws"
    (ws / "models").mkdir(parents=True)
    for f in ("encoder_X.pt", "decoder_X.pt", "bootstrap.pt"):
        (ws / "models" / f).write_text("x")
    assert stage.workspace_inventory(ws)["bootstrap_marker_present"] is True

    ws2 = tmp_path / "ws2"
    (ws2 / "models").mkdir(parents=True)
    (ws2 / "models" / "encoder_X.pt").write_text("x")
    assert stage.workspace_inventory(ws2)["bootstrap_marker_present"] is False


def test_inventory_on_a_missing_workspace_is_not_fatal(stage, tmp_path):
    inv = stage.workspace_inventory(tmp_path / "never-created")
    assert inv["exists"] is False


# --- a reused run dir must not present the previous run's output as this run's ---


def test_reset_clears_previous_run_artifacts(stage, tmp_path):
    """
    The workspace persists and each stage reuses its run dir, so without clearing,
    the first progress mirror copies the LAST run's DOS files and reports them as
    this run's progress.
    """
    run = tmp_path / "sample"
    run.mkdir()
    stale = [
        "DOS_H_iter001.dat",
        "DOS_H_iter020.dat",
        "vae.dat",
        "misc0.dat",
        "stat0.dat",
        "snap_0_0.xyz",
        "engine.log",
    ]
    for f in stale:
        (run / f).write_text("old")
    removed = stage.reset_run_dir(run)
    assert set(removed) == set(stale)
    assert not list(run.glob("DOS_H_iter*.dat"))


def test_reset_clears_checkpoints_so_a_run_cannot_silently_resume(stage, tmp_path):
    """state<N>.input / mc<N>.input are restart files; a stale one would resume a run
    whose configuration no longer matches config.toml."""
    run = tmp_path / "sample"
    run.mkdir()
    (run / "state0.input").write_text("ckpt")
    (run / "mc0.input").write_text("ckpt")
    stage.reset_run_dir(run)
    assert not (run / "state0.input").exists()
    assert not (run / "mc0.input").exists()


def test_reset_never_touches_the_models_symlink_or_the_trained_vae(stage, tmp_path):
    """`models` is a symlink into the workspace — deleting it would discard training."""
    ws = tmp_path / "ws"
    models = ws / "models"
    models.mkdir(parents=True)
    (models / "encoder_X.pt").write_text("trained")
    run = ws / "sample"
    run.mkdir()
    (run / "models").symlink_to(models, target_is_directory=True)
    (run / "vae.dat").write_text("old")

    stage.reset_run_dir(run)
    assert (run / "models").is_symlink()
    assert (models / "encoder_X.pt").read_text() == "trained"
    assert not (run / "vae.dat").exists()


def test_reset_leaves_regenerated_inputs_alone(stage, tmp_path):
    run = tmp_path / "sample"
    run.mkdir()
    (run / "config.toml").write_text("cfg")
    (run / "coupling.input").write_text("J")
    (run / "vae.dat").write_text("old")
    stage.reset_run_dir(run)
    assert (run / "config.toml").exists() and (run / "coupling.input").exists()


def test_training_reset_clears_a_stale_checkpoint(train, tmp_path):
    """A failed run must not let the export step ship the PREVIOUS run's weights."""
    t = tmp_path / "train"
    (t / "checkpoints").mkdir(parents=True)
    (t / "checkpoints" / "vae.pt").write_text("stale weights")
    (t / "train_split.npy").write_text("old")
    removed = train.reset_train_dir(t)
    assert "checkpoints/vae.pt" in removed
    assert not (t / "checkpoints" / "vae.pt").exists()
