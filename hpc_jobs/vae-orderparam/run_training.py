#!/usr/bin/env python3
"""
Vista HPC wrapper for the ``vae-orderparam`` job.

Fits a VAE to MC configurations and exports it as TorchScript. The trained encoder is
two things at once: a learned **order parameter** (latent coordinates of a configuration)
and the surrogate DeepThermo's Wang-Landau loop uses to propose global moves.

Pipeline (the repo's docs/vae-workflow.md steps 2-5, plus an optional step 7 check):

    snap_*.xyz  -> create_vae_input.py -> one-hot .npy
                -> dedupe + stratified split  -> train_split / val_split
                -> train_vae.py               -> checkpoints/vae.pt
                -> export_torchscript         -> encoder_<tag>.pt / decoder_<tag>.pt
                -> (optional) op_infer.py     -> order parameter over the same frames

Reads and writes the SAME persistent workspace the ``deepthermo-wl`` collect stage
populated, so the exported model lands where the sampling stage will look for it
(``<workspace>/models``). Only a small results.json and the training curve go to
$VISTA_OUT.

Two couplings that fail SILENTLY if you get them wrong, so both are derived here rather
than passed in:

  - **grid size** comes from the engine's padding rule (VAE_D = 3N-2, then padded), not
    from the coordinate range in the xyz file. Preprocessing prints it; training and
    export must use the same value or the C++ side cannot load the tensors.
  - **element order** indexes the one-hot channel and must match [lattice].elements in
    the engine's config.toml. A different order silently trains on permuted species.
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path


def _run(cmd, **kw):
    print(f"[vae-orderparam] $ {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def _csv(text):
    return [p for p in re.split(r"[,\s]+", text.strip()) if p]


def engine_grid(n_lattice: int) -> tuple[int, int]:
    """(grid, offset) for an N^3 lattice — mirrors vae-modeling/utils/geometry.py.

    Truncating divide, matching the C++ int cast: the grid is NOT always a multiple
    of 16 (N=5 gives 15). Do not 'round up'.
    """
    shift = n_lattice - 1
    raw = 2 * (n_lattice - 1) + shift + 1
    pad = int((math.ceil(raw / 16) * 16 - raw) / 2)
    return raw + 2 * pad, shift + pad


def dedupe_and_split(npy_in: Path, out_train: Path, out_val: Path, *,
                     n_ranks: int, val_fraction: float, seed: int) -> dict:
    """
    Deduplicate frames, then split train/val stratified by PT rank.

    Both halves matter (docs/vae-workflow.md step 3): cold replicas repeat
    configurations because their dynamics freeze, and a repeat straddling the split
    would let validation score frames the model memorised. Stratifying by rank keeps
    validation spanning the whole temperature ladder instead of landing entirely in
    the hottest replica.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    a = np.load(npy_in, mmap_mode="r")
    n = a.shape[0]
    per = max(1, n // max(1, n_ranks))

    seen: dict[bytes, int] = {}
    for i in range(n):
        seen.setdefault(hashlib.md5(np.asarray(a[i]).tobytes()).digest(), i)
    keep = np.array(sorted(seen.values()))

    tr, va = [], []
    for r in range(n_ranks):
        blk = rng.permutation(keep[(keep >= r * per) & (keep < (r + 1) * per)])
        if len(blk) == 0:
            continue
        cut = max(1, int(round(val_fraction * len(blk))))
        va.append(blk[:cut])
        tr.append(blk[cut:])
    # Check the CONCATENATED sizes, not the lists: a list holding one empty array is
    # truthy, which would let an empty training set through to train_vae.py and fail
    # there with something far less legible.
    tr = np.sort(np.concatenate(tr)) if tr else np.array([], dtype=int)
    va = np.sort(np.concatenate(va)) if va else np.array([], dtype=int)
    if len(tr) == 0 or len(va) == 0:
        raise SystemExit(
            f"not enough frames to split ({n} total, {len(keep)} unique, {n_ranks} ranks "
            f"-> train={len(tr)} val={len(va)}). Collect more samples, lower --n-ranks, "
            "or reduce --skip-frames."
        )
    np.save(out_train, np.asarray(a[tr]))
    np.save(out_val, np.asarray(a[va]))
    return {"frames_total": int(n), "frames_unique": int(len(keep)),
            "duplicate_fraction": round(1.0 - len(keep) / n, 4) if n else None,
            "train": int(len(tr)), "val": int(len(va))}


def reset_train_dir(train_dir: Path) -> list[str]:
    """
    Clear the previous training run's intermediates.

    The workspace persists across jobs, so without this a re-run inherits the last
    one's `checkpoints/vae.pt` and splits. The dangerous case is a run that fails
    mid-training: the export step would then pick up the OLD weights and write them out
    as if freshly trained, and the sampling stage would silently use a model that does
    not correspond to this run's data.
    """
    removed: list[str] = []
    targets = list(train_dir.glob("*.npy")) + list(train_dir.glob("checkpoints/*.pt"))
    for f in sorted(targets):
        if f.is_symlink() or not f.is_file():
            continue
        try:
            f.unlink()
            removed.append(str(f.relative_to(train_dir)))
        except OSError:
            continue
    return removed


def parse_training_curve(text: str) -> list[dict]:
    """`epoch  N/M  train=X  val=Y` lines from train_vae.py's stdout."""
    out = []
    for m in re.finditer(r"epoch\s+(\d+)/(\d+)\s+train=\s*([\d.eE+-]+)\s+val=\s*([\d.eE+-]+)", text):
        out.append({"epoch": int(m.group(1)), "train": float(m.group(3)), "val": float(m.group(4))})
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Train + export a VAE order parameter.")
    p.add_argument("--skill-root", required=True, help="Path to the cloned DeepThermo-WL repo.")
    p.add_argument("--output-dir", required=True, help="Where results.json lands ($VISTA_OUT).")
    p.add_argument("--workspace-root", required=True,
                   help="Root under which named workspaces live (set by the cluster script).")
    p.add_argument("--workspace", default="default",
                   help="Workspace NAME written by the collect stage. Part of script_args so "
                        "the agent can pick one per study (submit_hpc_job passes no env).")
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--snapshots-dir", default=None,
                   help="Where snap_*.xyz live. Default: <workspace>/collect.")

    p.add_argument("--n", type=int, default=10, help="Lattice N the snapshots came from.")
    p.add_argument("--elements", default="Mo,Nb,Ta,W",
                   help="MUST match [lattice].elements order in the engine config.")
    p.add_argument("--alloy-tag", default="MoNbTaW", dest="alloy_tag")
    p.add_argument("--skip-frames", type=int, default=50, dest="skip_frames",
                   help="Drop the first N frames per file (unequilibrated).")
    p.add_argument("--n-ranks", type=int, default=8, dest="n_ranks",
                   help="PT ranks that produced the snapshots; used to stratify the split.")
    p.add_argument("--val-fraction", type=float, default=0.1, dest="val_fraction")

    p.add_argument("--epochs", type=int, default=100)
    p.add_argument("--batch-size", type=int, default=32, dest="batch_size")
    p.add_argument("--latent-dim", type=int, default=3, dest="latent_dim")
    p.add_argument("--lr", type=float, default=5e-4)
    p.add_argument("--seed", type=int, default=6)
    p.add_argument("--keep-existing", action="store_true", dest="keep_existing",
                   help="Reuse previous training intermediates instead of clearing them. "
                        "Off by default: a stale checkpoint could otherwise be exported "
                        "as if newly trained.")
    p.add_argument("--order-parameter", action="store_true", dest="order_parameter",
                   help="Also run op_infer.py over the frames with the trained model.")

    args = p.parse_args(argv)

    skill_root = Path(args.skill_root).resolve()
    vae_root = skill_root / "vae-modeling"
    out_dir = Path(args.output_dir).resolve()
    workspace = (Path(args.workspace_root) / args.workspace).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not (vae_root / "vae" / "train_vae.py").is_file():
        raise SystemExit(
            f"{vae_root} is missing the training code. The vae-modeling submodule was not "
            "checked out — clone with --recurse-submodules."
        )

    if not workspace.is_dir():
        raise SystemExit(
            f"no workspace at {workspace}. Run deepthermo-wl --mode collect --workspace "
            f"{args.workspace} first."
        )
    snaps_dir = Path(args.snapshots_dir) if args.snapshots_dir else workspace / "collect"
    snapshots = sorted(snaps_dir.glob("snap_*.xyz"))
    if not snapshots:
        raise SystemExit(
            f"no snap_*.xyz under {snaps_dir}. Run the deepthermo-wl job in --mode collect "
            "against this workspace first."
        )

    elements = _csv(args.elements)
    grid, offset = engine_grid(args.n)
    train_dir = workspace / "train"
    train_dir.mkdir(parents=True, exist_ok=True)
    if args.keep_existing:
        cleared: list[str] = []
        print("[vae-orderparam] --keep-existing: reusing previous training intermediates",
              flush=True)
    else:
        cleared = reset_train_dir(train_dir)
        if cleared:
            print(f"[vae-orderparam] cleared {len(cleared)} stale training artifact(s): "
                  f"{', '.join(cleared[:6])}{'...' if len(cleared) > 6 else ''}", flush=True)
    models = workspace / "models"
    models.mkdir(parents=True, exist_ok=True)

    print(f"[vae-orderparam] {len(snapshots)} snapshot file(s), N={args.n} -> "
          f"grid={grid} offset={offset}, elements={elements}", flush=True)

    # 1. One-hot preprocessing. The grid comes from the engine's padding rule, which
    #    create_vae_input.py derives itself from --lattice_n; we pass it explicitly so
    #    it cannot be inferred from the coordinate range instead.
    stem = train_dir / f"frames_n{args.n}"
    _run([args.python, vae_root / "preprocessing" / "create_vae_input.py",
          "--xyz_file", *[str(s) for s in snapshots],
          "--elements", *elements,
          "--lattice_n", args.n,
          "--skip_frames", args.skip_frames,
          "--save_file", str(stem)], cwd=vae_root)
    npy = Path(f"{stem}.npy")
    if not npy.is_file():
        raise SystemExit(f"preprocessing produced no {npy}")

    # 2. Dedupe + stratified split.
    split_stats = dedupe_and_split(
        npy, train_dir / "train_split.npy", train_dir / "val_split.npy",
        n_ranks=args.n_ranks, val_fraction=args.val_fraction, seed=args.seed,
    )
    print(f"[vae-orderparam] split: {split_stats}", flush=True)

    # 3. Train.
    ckpt = train_dir / "checkpoints"
    proc = subprocess.run(
        [args.python, str(vae_root / "vae" / "train_vae.py"),
         "--train_file", str(train_dir / "train_split.npy"),
         "--val_file", str(train_dir / "val_split.npy"),
         "--epochs", str(args.epochs), "--batch_size", str(args.batch_size),
         "--latent_dim", str(args.latent_dim), "--lr", str(args.lr),
         "--seed", str(args.seed), "--out_dir", str(ckpt)],
        cwd=vae_root, check=True, capture_output=True, text=True,
    )
    print(proc.stdout[-4000:], flush=True)
    curve = parse_training_curve(proc.stdout)
    (out_dir / "training.log").write_text(proc.stdout)

    weights = ckpt / "vae.pt"
    if not weights.is_file():
        raise SystemExit(f"training produced no {weights}")

    # 4. Export TorchScript into the workspace's models/, where the sampling stage looks.
    _run([args.python, "-m", "vae.src.export_torchscript",
          "--weights", str(weights), "--alloy_tag", args.alloy_tag,
          "--grid_size", grid, "--num_elements", len(elements),
          "--latent_dim", args.latent_dim, "--out_dir", str(models)], cwd=vae_root)

    encoder = models / f"encoder_{args.alloy_tag}.pt"
    decoder = models / f"decoder_{args.alloy_tag}.pt"
    for f in (encoder, decoder):
        if not f.is_file():
            raise SystemExit(f"export produced no {f}")

    # 5. Optional: the order parameter itself, over the same frames.
    order_parameter = None
    if args.order_parameter:
        op_out = subprocess.run(
            [args.python, str(vae_root / "orderparameter" / "op_infer.py"),
             "--xyz_files", *[str(s) for s in snapshots],
             "--model", str(weights), "--num_elements", str(len(elements)),
             "--latent_dim", str(args.latent_dim), "--lattice_n", str(args.n),
             "--skip_frames", str(args.skip_frames)],
            cwd=vae_root, check=False, capture_output=True, text=True,
        )
        (out_dir / "order_parameter.log").write_text(op_out.stdout + op_out.stderr)
        order_parameter = {"ok": op_out.returncode == 0,
                           "tail": (op_out.stdout or op_out.stderr)[-1500:]}

    final = curve[-1] if curve else {}
    gap = None
    if final.get("train") and final.get("val"):
        gap = round(abs(final["val"] - final["train"]) / max(final["train"], 1e-9), 4)

    results = {
        "job": "vae-orderparam",
        "workspace": str(workspace),
        "model_dir": str(models),
        "encoder": str(encoder),
        "decoder": str(decoder),
        "lattice": {"N": args.n, "grid_size": grid, "coordinate_offset": offset,
                    "elements": elements},
        "latent_dim": args.latent_dim,
        "data": {**split_stats, "snapshot_files": len(snapshots),
                 "skip_frames": args.skip_frames},
        "training": {"epochs": args.epochs, "final": final,
                     "train_val_gap": gap, "curve": curve[-10:]},
        "order_parameter": order_parameter,
        "cleared_stale_artifacts": cleared,
    }
    (out_dir / "results.json").write_text(json.dumps(results, indent=2))
    print(f"[vae-orderparam] exported {encoder.name} / {decoder.name}; "
          f"final={final} gap={gap}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
