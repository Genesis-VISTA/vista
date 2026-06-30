#!/usr/bin/env python3
"""
Vista HPC wrapper for the ``vit-train`` job.

Turns ONE ViT search point (the campaign candidate, as a single JSON token) into one
training run of the cloned ``climate-vit`` repo (``train_mp.py``), writing ``results.json``
— the validation loss + training throughput the planner scores — into the output dir.

This file is the only non-metadata file in ``hpc_jobs/vit-train/``, so vista Globus-stages
it to ``$RUN_DIR_<Cluster>``. ``job.<cluster>.slurm`` invokes it (on the head node, NOT
under srun) as::

    python run_state_point.py --skill-root <clone> --output-dir $VISTA_OUT '<candidate-json>'

The candidate is the campaign wire format (see ``campaign.planner.encode_candidate_args``),
e.g. ``{"embed_dim":1024,"depth":12,"num_heads":8,"patch_size":8,"lr":5e-4,
"global_batch_size":16,"tensor_parallel":1,"context_parallel":1}``. ViT architecture + lr +
batch are NOT command-line flags in climate-vit — they live in ``config/ViT.yaml`` — so this
wrapper renders a self-contained config block from the candidate and launches
``train_mp.py --yaml_config <rendered> --config nas`` across the allocation's GPUs, passing
the parallelism degrees as flags. It then scrapes the training log for the two metrics and
writes ``results.json``.

Generic: the default config trains climate-vit on ERA5, but pointing ``CLIMATEVIT_REPO_URL``
/ ``CLIMATEVIT_DATA_ROOT`` at another ViT training repo reuses this job for other ViT tasks,
as long as that repo logs ``Avg val loss=`` and ``avg N samples/sec`` lines.
"""
import argparse
import glob
import json
import os
import re
import shlex
import subprocess


# climate-vit's `base` config (config/ViT.yaml). The candidate overrides the search fields;
# everything else is a sane default the model needs to construct + train. Data paths default
# to OLCF world-shared ERA5 and can be redirected with --data-root / CLIMATEVIT_DATA_ROOT.
_DATA_ROOT_DEFAULT = "/lustre/orion/world-shared/stf218/junqi/data/EAR5"
BASE_CONFIG = {
    "embed_dim": 384,
    "depth": 12,
    "dropout": 0.0,
    "patch_size": 8,
    "num_heads": 8,
    "img_size": [360, 720],
    "dt": 1,
    "global_batch_size": 16,
    "num_iters": 30000,
    "amp_mode": "none",
    "enable_fused": False,
    "enable_jit": False,
    "expdir": "./logs",
    "lr_schedule": "cosine",
    "lr": 5.0e-4,
    "warmup": 0,
    "optimizer": "Adam",
    "data_loader_config": "pytorch",
    "num_data_workers": 0,
    "n_in_channels": 20,
    "n_out_channels": 20,
    "wireup_info": "env",
    "wireup_store": "tcp",
}

# Candidate keys that map onto ViT.yaml fields (the rest — tensor/context_parallel — are
# launcher flags, handled separately).
_INT_CONFIG_KEYS = {"embed_dim", "depth", "num_heads", "patch_size", "global_batch_size"}
_FLOAT_CONFIG_KEYS = {"lr"}

CONFIG_NAME = "nas"


def data_paths(data_root: str) -> dict:
    """The climate-vit data/stats paths derived from a dataset root."""
    return {
        "train_data_path": f"{data_root}/train",
        "valid_data_path": f"{data_root}/valid",
        "inf_data_path": f"{data_root}/test",
        "time_means_path": f"{data_root}/stats/time_means.npy",
        "global_means_path": f"{data_root}/stats/global_means.npy",
        "global_stds_path": f"{data_root}/stats/global_stds.npy",
    }


def render_config(
    candidate: dict, *, expdir: str, num_iters: int | None = None, data_root: str = _DATA_ROOT_DEFAULT
) -> dict:
    """Build a self-contained ViT.yaml config block from the candidate (search fields override base)."""
    cfg = dict(BASE_CONFIG)
    for key, value in candidate.items():
        if value is None:
            continue
        if key in _INT_CONFIG_KEYS:
            cfg[key] = int(value)
        elif key in _FLOAT_CONFIG_KEYS:
            cfg[key] = float(value)
        # tensor_parallel / context_parallel and any unknown keys are not config fields.
    cfg.update(data_paths(data_root))
    cfg["expdir"] = expdir
    if num_iters is not None:
        cfg["num_iters"] = int(num_iters)
    if cfg["embed_dim"] % cfg["num_heads"] != 0:
        raise ValueError(
            f"invalid candidate: embed_dim={cfg['embed_dim']} not divisible by num_heads={cfg['num_heads']}"
        )
    return cfg


def write_config_yaml(cfg: dict, path: str, *, config_name: str = CONFIG_NAME) -> None:
    """Write {config_name: cfg} as YAML that climate-vit's YParams can load."""
    import yaml

    with open(path, "w") as f:
        yaml.safe_dump({config_name: cfg}, f, default_flow_style=False, sort_keys=False)


def compute_ntasks(tp: int, cp: int, *, nnodes: int, gpus_per_node: int) -> int:
    """Total ranks (= GPUs) for the run; tensor_parallel*context_parallel must divide it."""
    ntasks = nnodes * gpus_per_node
    if tp < 1 or cp < 1 or ntasks % (tp * cp) != 0:
        raise ValueError(
            f"tensor_parallel*context_parallel ({tp}*{cp}) must divide total GPUs "
            f"({ntasks} = {nnodes} nodes * {gpus_per_node} gpus/node)"
        )
    return ntasks


def build_train_cmd(
    *, skill_root: str, yaml_path: str, config_name: str, tp: int, cp: int, nnodes: int, ntasks: int
) -> list[str]:
    """The srun command that launches train_mp.py across the allocation's GPUs.

    Mirrors climate-vit's own submit scripts: one rank per GPU, DDP wireup sourced from the
    repo's export_DDP_vars.sh, parallelism degrees passed as flags (model/lr/batch come from
    the rendered --yaml_config). cwd is the clone so train_mp.py + export_DDP_vars.sh resolve.
    """
    inner = (
        f"source export_DDP_vars.sh; "
        f"python -u train_mp.py --yaml_config {shlex.quote(yaml_path)} --config {shlex.quote(config_name)} "
        f"--tensor_parallel={tp} --context_parallel={cp} --parallel_order=tp-cp-dp"
    )
    return [
        "srun", "--nodes", str(nnodes), "--ntasks", str(ntasks),
        "--gpu-bind=closest", "-c7", "bash", "-c", inner,
    ]


_VAL_LOSS_RE = re.compile(r"Avg val loss=\s*([0-9][0-9.eE+-]*)")
_THROUGHPUT_RE = re.compile(r"avg\s+([0-9][0-9.eE+-]*)\s+samples/sec")


def parse_metrics(text: str) -> dict:
    """Scrape val loss + throughput from a climate-vit training log (last occurrence wins)."""
    val = _VAL_LOSS_RE.findall(text or "")
    tps = _THROUGHPUT_RE.findall(text or "")
    return {
        "val_loss": float(val[-1]) if val else None,
        "throughput_samples_s": float(tps[-1]) if tps else None,
    }


def _collect_log_text(stdout_log: str, expdir: str) -> str:
    """Combine captured stdout with any log files climate-vit wrote under expdir."""
    parts = []
    if os.path.exists(stdout_log):
        parts.append(open(stdout_log, errors="replace").read())
    for pattern in ("**/*.log", "**/log*", "**/out*"):
        for path in glob.glob(os.path.join(expdir, pattern), recursive=True):
            if os.path.isfile(path):
                parts.append(open(path, errors="replace").read())
    return "\n".join(parts)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Train one climate-vit search point and report metrics.")
    p.add_argument("--skill-root", required=True, help="Path to the cloned climate-vit repo.")
    p.add_argument("--output-dir", required=True, help="Where results.json + the run dir land ($VISTA_OUT).")
    p.add_argument("--num-iters", type=int, default=int(os.environ.get("CLIMATEVIT_NUM_ITERS", "2000")),
                   help="Training iterations per candidate (a screening budget; default 2000).")
    p.add_argument("--gpus-per-node", type=int, default=int(os.environ.get("GPUS_PER_NODE", "8")),
                   help="GPUs per node (Frontier 8 / Perlmutter 4).")
    p.add_argument("--data-root", default=os.environ.get("CLIMATEVIT_DATA_ROOT", _DATA_ROOT_DEFAULT),
                   help="ERA5 dataset root (contains train/ valid/ test/ stats/).")
    p.add_argument("--dry-run", action="store_true", help="Render the config + print the launch command, don't train.")
    p.add_argument("candidate", help="ViT search point as one JSON object.")

    args = p.parse_args(argv)

    try:
        candidate = json.loads(args.candidate)
    except json.JSONDecodeError as exc:
        p.error(f"candidate is not valid JSON: {exc}")
    if not isinstance(candidate, dict):
        p.error("candidate JSON must be an object")

    skill_root = os.path.abspath(args.skill_root)
    out = os.path.abspath(args.output_dir)
    os.makedirs(out, exist_ok=True)
    # Durable run/checkpoint dir under $VISTA_OUT so a requeued training can resume and the
    # campaign keeps its artifacts across the long HPC wait.
    expdir = os.path.join(out, "expdir")
    os.makedirs(expdir, exist_ok=True)

    tp = int(candidate.get("tensor_parallel", 1))
    cp = int(candidate.get("context_parallel", 1))
    nnodes = int(os.environ.get("SLURM_NNODES", "1"))
    ntasks = compute_ntasks(tp, cp, nnodes=nnodes, gpus_per_node=args.gpus_per_node)

    cfg = render_config(candidate, expdir=expdir, num_iters=args.num_iters, data_root=args.data_root)
    yaml_path = os.path.join(out, "ViT.nas.yaml")
    write_config_yaml(cfg, yaml_path)

    cmd = build_train_cmd(
        skill_root=skill_root, yaml_path=yaml_path, config_name=CONFIG_NAME,
        tp=tp, cp=cp, nnodes=nnodes, ntasks=ntasks,
    )
    print(f"[vit-train] candidate={candidate}", flush=True)
    print(f"[vit-train] geometry: {nnodes} node(s) x {args.gpus_per_node} gpus = {ntasks} ranks "
          f"(tp={tp}, cp={cp}); num_iters={args.num_iters}", flush=True)
    print(f"[vit-train] launch: {' '.join(cmd)}", flush=True)

    if args.dry_run:
        print(f"[vit-train] dry-run; rendered config at {yaml_path}", flush=True)
        return 0

    stdout_log = os.path.join(out, "train.log")
    with open(stdout_log, "w") as log:
        subprocess.run(cmd, cwd=skill_root, check=True, stdout=log, stderr=subprocess.STDOUT)

    metrics = parse_metrics(_collect_log_text(stdout_log, expdir))
    results = {
        "params": candidate,
        "metrics": metrics,
        "geometry": {"nnodes": nnodes, "gpus_per_node": args.gpus_per_node,
                     "ntasks": ntasks, "tensor_parallel": tp, "context_parallel": cp},
        "config": CONFIG_NAME,
        "num_iters": args.num_iters,
        "expdir": expdir,
    }
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(results, f, indent=2)

    if metrics["val_loss"] is None or metrics["throughput_samples_s"] is None:
        print("[vit-train] WARNING: could not parse val_loss / throughput from the log; "
              "results.json metrics are incomplete (the planner will treat this candidate as infeasible).",
              flush=True)
    print(f"[vit-train] done — results.json in {out}: {metrics}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
