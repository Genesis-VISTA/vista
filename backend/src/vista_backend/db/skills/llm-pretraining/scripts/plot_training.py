#!/usr/bin/env python3
"""
Parse a GPT-NeoX (FORGE) training log and plot loss + throughput.

Reads the job log that `get_hpc_job_status` accumulates locally
(`/mnt/data/output/<job_id>/log-<job_id>.out` in the sandbox) -- the whole run,
not just the tail the tool returns. Writes a two-panel PNG and a CSV of the
per-iteration metrics, and prints a JSON summary on stdout.

Usage:
    MPLBACKEND=Agg python3 plot_training.py --log LOG --output PNG [--csv CSV] [--title T]

The lines it understands (printed by rank 0, one per `log-interval`):

    samples/sec: 51.234 | iteration       10/      50 | elapsed time per iteration (ms): 9876.5 |
    learning rate: 1.234E-04 | approx flops per GPU: 150.3TFLOPS | lm_loss: 7.123456E+00 |
    number of skipped iterations:   0 | number of nan iterations:   0 |

    validation results at iteration 100 | lm_loss value: 6.9E+00 | lm_loss_ppl value: 990 |
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from pathlib import Path

_ITER = re.compile(r"iteration\s+(\d+)\s*/\s*(\d+)\s*\|")
_FIELDS = {
    "samples_per_sec": re.compile(r"samples/sec:\s*([\d.]+)"),
    "ms_per_iter": re.compile(r"elapsed time per iteration \(ms\):\s*([\d.]+)"),
    "learning_rate": re.compile(r"learning rate:\s*([\d.Ee+-]+)"),
    "lm_loss": re.compile(r"\blm_loss:\s*([\d.Ee+-]+|nan|inf)"),
    "skipped": re.compile(r"number of skipped iterations:\s*(\d+)"),
    "nan": re.compile(r"number of nan iterations:\s*(\d+)"),
}
_FLOPS = re.compile(r"approx flops per GPU:\s*([\d.]+)\s*([KMGTPEZ]?)FLOPS")
_VAL = re.compile(
    r"validation results at iteration\s+(\d+)\s*\|.*?\blm_loss value:\s*([\d.Ee+-]+)"
)
_SCALE = {
    "": 1e-12,
    "K": 1e-9,
    "M": 1e-6,
    "G": 1e-3,
    "T": 1.0,
    "P": 1e3,
    "E": 1e6,
    "Z": 1e9,
}


def parse_log(text: str) -> dict:
    """
    `{"train": [row, ...], "validation": [{"iteration", "lm_loss"}, ...], "total_iters"}`.
    Each train row has `iteration` plus whichever of `lm_loss`, `ms_per_iter`,
    `samples_per_sec`, `learning_rate`, `tflops_per_gpu`, `skipped`, `nan` the
    line carried. A repeated iteration (a resumed run re-logging it) keeps the
    last occurrence.
    """
    train: dict[int, dict] = {}
    validation: dict[int, float] = {}
    total_iters = None
    for line in text.splitlines():
        v = _VAL.search(line)
        if v:
            validation[int(v.group(1))] = float(v.group(2))
            continue
        it = _ITER.search(line)
        if not it or "samples/sec" not in line:
            continue
        row: dict = {"iteration": int(it.group(1))}
        total_iters = int(it.group(2))
        for name, rx in _FIELDS.items():
            m = rx.search(line)
            if m:
                row[name] = (
                    int(m.group(1)) if name in ("skipped", "nan") else float(m.group(1))
                )
        f = _FLOPS.search(line)
        if f:
            row["tflops_per_gpu"] = float(f.group(1)) * _SCALE[f.group(2)]
        train[row["iteration"]] = row
    return {
        "train": [train[k] for k in sorted(train)],
        "validation": [
            {"iteration": k, "lm_loss": validation[k]} for k in sorted(validation)
        ],
        "total_iters": total_iters,
    }


def summarize(parsed: dict) -> dict:
    rows = parsed["train"]
    if not rows:
        return {"iterations_logged": 0, "total_iters": parsed["total_iters"]}
    last = rows[-1]
    losses = [r["lm_loss"] for r in rows if "lm_loss" in r]
    # The first logged iteration includes warm-up (kernel builds, first
    # all-reduce); leave it out of the steady-state numbers when there is more.
    steady = rows[1:] if len(rows) > 1 else rows
    ms = [r["ms_per_iter"] for r in steady if "ms_per_iter" in r]
    tflops = [r["tflops_per_gpu"] for r in steady if "tflops_per_gpu" in r]
    sps = [r["samples_per_sec"] for r in steady if "samples_per_sec" in r]
    out = {
        "iterations_logged": len(rows),
        "last_iteration": last["iteration"],
        "total_iters": parsed["total_iters"],
        "first_lm_loss": losses[0] if losses else None,
        "last_lm_loss": losses[-1] if losses else None,
        "median_ms_per_iter": statistics.median(ms) if ms else None,
        "median_tflops_per_gpu": statistics.median(tflops) if tflops else None,
        "median_samples_per_sec": statistics.median(sps) if sps else None,
        "skipped_iterations": sum(r.get("skipped", 0) for r in rows),
        "nan_iterations": sum(r.get("nan", 0) for r in rows),
        "last_validation": parsed["validation"][-1] if parsed["validation"] else None,
    }
    if ms and parsed["total_iters"]:
        remaining = max(0, parsed["total_iters"] - last["iteration"])
        out["eta_seconds"] = round(remaining * statistics.median(ms) / 1000.0)
    return out


def write_csv(parsed: dict, path: Path) -> None:
    cols = [
        "iteration",
        "lm_loss",
        "ms_per_iter",
        "samples_per_sec",
        "tflops_per_gpu",
        "learning_rate",
        "skipped",
        "nan",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(parsed["train"])


def plot(parsed: dict, path: Path, title: str) -> None:
    import matplotlib.pyplot as plt

    rows = parsed["train"]
    fig, (ax_loss, ax_tput) = plt.subplots(1, 2, figsize=(11, 4))

    pts = [(r["iteration"], r["lm_loss"]) for r in rows if "lm_loss" in r]
    if pts:
        ax_loss.plot(*zip(*pts), marker="o", markersize=3, label="train lm_loss")
    if parsed["validation"]:
        ax_loss.plot(
            [v["iteration"] for v in parsed["validation"]],
            [v["lm_loss"] for v in parsed["validation"]],
            marker="s",
            linestyle="--",
            label="validation lm_loss",
        )
    ax_loss.set_xlabel("Iteration")
    ax_loss.set_ylabel("LM loss")
    ax_loss.set_title("Loss")
    ax_loss.grid(True, alpha=0.3)
    if pts or parsed["validation"]:
        ax_loss.legend()

    tf = [(r["iteration"], r["tflops_per_gpu"]) for r in rows if "tflops_per_gpu" in r]
    if tf:
        ax_tput.plot(*zip(*tf), marker="o", markersize=3, color="tab:green")
    ax_tput.set_xlabel("Iteration")
    ax_tput.set_ylabel("TFLOPS per GPU (approx.)")
    ax_tput.set_title("Throughput")
    ax_tput.grid(True, alpha=0.3)
    sp = [
        (r["iteration"], r["samples_per_sec"]) for r in rows if "samples_per_sec" in r
    ]
    if sp:
        ax_sps = ax_tput.twinx()
        ax_sps.plot(*zip(*sp), linestyle=":", color="tab:gray")
        ax_sps.set_ylabel("samples/sec (dotted)")

    fig.suptitle(title)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Plot FORGE / GPT-NeoX training progress.")
    p.add_argument("--log", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path, help="PNG path")
    p.add_argument(
        "--csv", type=Path, default=None, help="CSV path (default: next to the PNG)"
    )
    p.add_argument("--title", default="FORGE pre-training progress")
    args = p.parse_args(argv)

    if not args.log.exists():
        print(json.dumps({"error": f"log not found: {args.log}"}))
        return 1
    parsed = parse_log(args.log.read_text(encoding="utf-8", errors="replace"))
    write_csv(parsed, args.csv or args.output.with_suffix(".csv"))
    if parsed["train"] or parsed["validation"]:
        plot(parsed, args.output, args.title)
    summary = summarize(parsed)
    summary["plot"] = (
        str(args.output) if (parsed["train"] or parsed["validation"]) else None
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
