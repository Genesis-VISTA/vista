"""
Materialize the SciAgentBench attack corpus to YAML on disk.

The 10 active template generators (``templates/``) are the source of
truth; this module writes their instances to
``sciagentbench/corpus/<template>/<instance_id>.yaml`` so the harness
loads them as files (the work-item acceptance criterion: "all attack-class
instances on disk and loadable by the Phase-9 harness"). The deferred
B3.5 / B1.6 templates and their corpus live under
``sciagentbench/deferred/`` and are not written here.

Run as a module to (re)generate the committed corpus:

    cd backend
    uv run python -m vista_backend.vistaguard.sciagentbench.corpus_builder
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from .instance_loader import instance_to_dict
from .templates import build_all_instances

_PACKAGE_DIR = Path(__file__).resolve().parent
CORPUS_DIR = _PACKAGE_DIR / "corpus"


def write_corpus(directory: Path | str | None = None) -> list[Path]:
    """Write all active instances to ``directory`` (default: bundled corpus).

    Layout: ``<directory>/<template>/<instance_id>.yaml``. Returns the
    list of written paths. Existing files are overwritten so the corpus
    is reproducible from the generators.
    """
    base = Path(directory) if directory is not None else CORPUS_DIR
    written: list[Path] = []
    for instance in build_all_instances():
        template_dir = base / instance.template
        template_dir.mkdir(parents=True, exist_ok=True)
        path = template_dir / f"{instance.instance_id}.yaml"
        doc = instance_to_dict(instance)
        path.write_text(
            yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True),
            encoding="utf-8",
        )
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    target = argv[0] if argv else None
    written = write_corpus(target)
    sys.stdout.write(f"Wrote {len(written)} instances to {target or CORPUS_DIR}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
