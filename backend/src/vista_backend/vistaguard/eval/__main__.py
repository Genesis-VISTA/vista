"""
CLI entry point for the VISTAGuard evaluation harnesses.

"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .corpus import generate_corpus
from .g1_report import format_g1_report
from .g1_runner import run_g1_evaluation
from .g2_report import format_g2_report
from .g2_runner import run_g2_evaluation
from .g4_report import format_g4_report
from .g4_runner import run_g4_evaluation
from .g5_report import format_g5_report
from .g5_runner import run_g5_evaluation
from .report import format_report
from .runner import run_evaluation
from .sciagentbench_report import format_sciagentbench_report
from .sciagentbench_runner import run_sciagentbench_evaluation


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="vista_backend.vistaguard.eval",
        description=(
            "Run a VISTAGuard synthetic evaluation and write the "
            "markdown report to stdout."
        ),
    )
    p.add_argument(
        "--gate",
        choices=("g1", "g2", "g3", "g4", "g5", "sciagentbench"),
        default="g3",
        help=(
            "Which gate's evaluation to run. g3 (default) measures "
            "the Phase-2 G3 PoisonedRAG/AgentPoison/MemoryGraft "
            "defenses; g2 measures the Phase-1 G2 AgentDojo + "
            "SciAgentBench A3 defenses; g1 measures the Phase-3 G1 "
            "jailbreak-corpus defenses; g4 measures the Phase-3 G4 "
            "malicious-code-corpus defenses; g5 measures the Phase-4 "
            "G5 SciAgentBench B5.x HPC-job defenses."
        ),
    )
    p.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for scenario/corpus generation (default: 42)",
    )
    # G3-specific knobs (no-ops when --gate != g3).
    p.add_argument(
        "--n-chunks",
        type=int,
        default=100,
        help="[g3 only] Synthetic corpus size (default: 100)",
    )
    p.add_argument(
        "--n-queries",
        type=int,
        default=20,
        help="[g3 only] Benign query workload size (default: 20)",
    )
    # G1/G2/G4-shared knobs.
    p.add_argument(
        "--n-attacks-per-class",
        type=int,
        default=20,
        help="[g1/g2/g4] Scenarios per attack class (default: 20)",
    )
    p.add_argument(
        "--n-benign",
        type=int,
        default=None,
        help=(
            "[g1/g2/g4/g5] Benign workload size. Defaults: g1=200 (matches "
            "AC), g2=20, g4=50, g5=50."
        ),
    )
    # SciAgentBench-specific knobs (no-ops when --gate != sciagentbench).
    p.add_argument(
        "--instances-dir",
        type=str,
        default=None,
        help=(
            "[sciagentbench] Directory of instance YAML/JSON files. "
            "Defaults to the bundled Phase-9 fixtures."
        ),
    )
    return p.parse_args(argv)


def _run_g3(args: argparse.Namespace) -> str:
    corpus = generate_corpus(
        seed=args.seed,
        n_chunks=args.n_chunks,
        n_queries=args.n_queries,
    )
    result = run_evaluation(corpus, rng_seed=args.seed)
    return format_report(result)


def _run_g2(args: argparse.Namespace) -> str:
    """G2 evaluation is async (the gate's check methods are
    coroutines)."""
    n_benign = args.n_benign if args.n_benign is not None else 20
    result = asyncio.run(
        run_g2_evaluation(
            rng_seed=args.seed,
            n_attacks_per_class=args.n_attacks_per_class,
            n_benign=n_benign,
        )
    )
    return format_g2_report(result)


def _run_g1(args: argparse.Namespace) -> str:
    """G1 evaluation. Default benign workload is 200 to match the AC."""
    n_benign = args.n_benign if args.n_benign is not None else 200
    result = asyncio.run(
        run_g1_evaluation(
            rng_seed=args.seed,
            n_attacks_per_class=args.n_attacks_per_class,
            n_benign=n_benign,
        )
    )
    return format_g1_report(result)


def _run_g4(args: argparse.Namespace) -> str:
    """G4 evaluation. Default benign workload is 50."""
    n_benign = args.n_benign if args.n_benign is not None else 50
    result = asyncio.run(
        run_g4_evaluation(
            rng_seed=args.seed,
            n_attacks_per_class=args.n_attacks_per_class,
            n_benign=n_benign,
        )
    )
    return format_g4_report(result)


def _run_g5(args: argparse.Namespace) -> str:
    """G5 evaluation. Defaults: 15 attacks/class, 50 benign scripts."""
    n_benign = args.n_benign if args.n_benign is not None else 50
    n_attacks = (
        args.n_attacks_per_class
        if args.n_attacks_per_class != 20
        else 15
    )
    result = asyncio.run(
        run_g5_evaluation(
            rng_seed=args.seed,
            n_attacks_per_class=n_attacks,
            n_benign=n_benign,
        )
    )
    return format_g5_report(result)


def _run_sciagentbench(args: argparse.Namespace) -> str:
    """SciAgentBench ablation (async -- gate checks are coroutines)."""
    result = asyncio.run(
        run_sciagentbench_evaluation(
            instances_dir=args.instances_dir,
            seed=args.seed,
        )
    )
    return format_sciagentbench_report(result)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    runners = {
        "g1": _run_g1,
        "g2": _run_g2,
        "g3": _run_g3,
        "g4": _run_g4,
        "g5": _run_g5,
        "sciagentbench": _run_sciagentbench,
    }
    report = runners[args.gate](args)
    sys.stdout.write(report)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
