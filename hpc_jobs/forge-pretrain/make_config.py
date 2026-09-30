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

The output is plain JSON (under a .yml name, which NeoX requires). NeoX hands the
raw text of every config file to the DeepSpeed launcher, and DeeperSpeed's Slurm
launcher `json.loads` each one ("SLURM is picky and needs you to use plain json
for your configs"). forge's own configs are written as JSON for that reason;
YAML output fails there with `JSONDecodeError: Expecting value`.

NeoX itself reads the file with PyYAML, so the text must also mean the same
thing as YAML. JSON nearly is YAML, with one trap: PyYAML (YAML 1.1) reads a
float with no dot in its mantissa, like Python's `1e-08`, as the STRING
"1e-08". `dumps_json_yaml` therefore writes every float with a dot (`1.0e-08`),
as forge's configs do.
"""
from __future__ import annotations

import argparse
import json
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


def _float_text(x: float) -> str:
    """A float both JSON and PyYAML read back as the same float: `1e-08` -> `1.0e-08`."""
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError(f"{x!r} has no JSON form")
    text = repr(x)
    mantissa, e, exponent = text.partition("e")
    if e and "." not in mantissa:
        text = f"{mantissa}.0e{exponent}"
    return text


def dumps_json_yaml(value, indent: int = 2, _level: int = 0) -> str:
    """
    Serialize to plain JSON that PyYAML also parses to the same value. Only
    floats need care (see `_float_text`); everything else is `json.dumps`.
    """
    pad, inner = " " * (indent * _level), " " * (indent * (_level + 1))
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [
            f"{inner}{json.dumps(str(k))}: {dumps_json_yaml(v, indent, _level + 1)}"
            for k, v in value.items()
        ]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        items = [f"{inner}{dumps_json_yaml(v, indent, _level + 1)}" for v in value]
        return "[\n" + ",\n".join(items) + f"\n{pad}]"
    if isinstance(value, float):
        return _float_text(value)
    return json.dumps(value)


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
        f.write(dumps_json_yaml(merged) + "\n")
    print(f"[make_config] wrote {args.out_yml}")
    for k in ("train-iters", "checkpoint-factor", "save", "load", "data-path"):
        print(f"[make_config]   {k}: {merged.get(k)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
