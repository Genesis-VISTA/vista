#!/usr/bin/env python
"""
Merge a FORGE model config and a cluster config into the single NeoX config a
vista run trains with, applying the run's overrides.

Why one merged file: NeoX's `from_ymls` refuses a key that appears in two of
the config files it is given, so a separate overlay yml cannot override
`data-path`, `train-iters`, etc. -- they already appear in the model config.

Usage:
    make_config.py MODEL_YML CLUSTER_YML OUT_YML --out-dir DIR --data-dir DIR
        [--train-iters N] [--save-interval N] [--log-interval N]
        [--lr-decay-iters N] [--load-dir DIR]

Everything the run writes goes under --out-dir, so concurrent runs from the one
shared checkout never touch each other's hostfile, checkpoints, or logs.
"""
from __future__ import annotations

import argparse
import os
import sys

import yaml


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        # FullLoader, as NeoX itself uses: the FORGE configs are JSON-ish YAML
        # with Python-style True/False.
        data = yaml.load(f, Loader=yaml.FullLoader) or {}
    if not isinstance(data, dict):
        sys.exit(f"{path}: expected a mapping at the top level")
    return data


def _norm(key: str) -> str:
    return key.replace("-", "_")


def merge(model: dict, cluster: dict, overrides: dict) -> dict:
    """
    `model` then `cluster` (which must not collide, as NeoX would insist), then
    `overrides`, which win. Keys compare dash/underscore-insensitively, as NeoX
    does, and an override replaces an existing key under its original spelling
    so the output never holds both `train-iters` and `train_iters`.
    """
    clash = {_norm(k) for k in model} & {_norm(k) for k in cluster}
    if clash:
        sys.exit(f"model and cluster configs both set: {', '.join(sorted(clash))}")
    merged = {**model, **cluster}
    spelling = {_norm(k): k for k in merged}
    for key, value in overrides.items():
        merged[spelling.get(_norm(key), key)] = value
    return merged


def overrides_from(args: argparse.Namespace) -> dict:
    out = os.path.abspath(args.out_dir)
    save_dir = os.path.join(out, "checkpoints")
    o: dict = {
        "hostfile": os.path.join(out, "hostfile"),
        "data-path": os.path.join(args.data_dir, "all_text_document"),
        "vocab-file": os.path.join(args.data_dir, "all_vocab.json"),
        "log-dir": os.path.join(out, "logs"),
        "tensorboard-dir": os.path.join(out, "tensorboard"),
        "use_wandb": False,
        "train-iters": args.train_iters,
        "log-interval": args.log_interval,
        "steps_per_print": args.log_interval,
    }
    save_interval = args.train_iters if args.save_interval is None else args.save_interval
    if save_interval > 0:
        o["save"] = save_dir
        o["checkpoint-factor"] = save_interval
    else:
        o["save"] = None
    # Resume from an explicit dir; otherwise "load" points at this run's own
    # (empty) save dir, which NeoX treats as a fresh start.
    o["load"] = args.load_dir or (save_dir if save_interval > 0 else None)
    if args.lr_decay_iters is not None:
        o["lr-decay-iters"] = args.lr_decay_iters
    return o


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("model_yml")
    p.add_argument("cluster_yml")
    p.add_argument("out_yml")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--train-iters", type=int, default=50)
    p.add_argument("--save-interval", type=int, default=None)
    p.add_argument("--log-interval", type=int, default=1)
    p.add_argument("--lr-decay-iters", type=int, default=None)
    p.add_argument("--load-dir", default=None)
    args = p.parse_args(argv)
    if args.train_iters <= 0 or args.log_interval <= 0:
        sys.exit("--train-iters and --log-interval must be positive")
    if args.save_interval is not None and args.save_interval < 0:
        sys.exit("--save-interval must be >= 0")

    merged = merge(load(args.model_yml), load(args.cluster_yml), overrides_from(args))

    os.makedirs(os.path.dirname(os.path.abspath(args.out_yml)), exist_ok=True)
    with open(args.out_yml, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, sort_keys=False, default_flow_style=False)
    print(f"[make_config] wrote {args.out_yml}")
    for k in ("train-iters", "checkpoint-factor", "save", "load", "data-path"):
        print(f"[make_config]   {k}: {merged.get(k)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
