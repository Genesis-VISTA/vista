"""Tests for check_retrieval: parsing hits and the exit code, with search stubbed.

No store or embedding model is available in CI, so this stands in for running the gate
on a real index: the stub answers in the format `rag_mcp._format_results` writes.
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_retrieval as cr  # noqa: E402

SEP = "\n\n" + cr.SEPARATOR + "\n\n"


def result(*files: str) -> str:
    parts = []
    for i, f in enumerate(files, 1):
        parts.append(
            f"[{i}] {f}, passage 1 (Markdown)\n    Description: d\n"
            f'    Full file: fetch_kb_file(kb_slug="olcf-docs", filename="{f}")\n\n'
            "text that mentions [9] x, and more"
        )
    return SEP.join(parts)


class CheckRetrievalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def toml(self, expected: str) -> Path:
        p = Path(self.dir.name) / "q.toml"
        p.write_text(f'[[question]]\nq = "How do I run jobs?"\nexpected = "{expected}"\n', encoding="utf-8")
        return p

    def run_main(self, expected: str, files: list[str]) -> int:
        return self.quiet_main(["unused", "--questions", str(self.toml(expected))], lambda q: result(*files))

    @staticmethod
    def quiet_main(argv, search) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cr.main(argv, search=search)

    def test_hit_files_in_order_without_repeats(self):
        text = result("a__x.md", "b__y.md", "a__x.md")
        self.assertEqual(cr.hit_files(text), ["a__x.md", "b__y.md"])

    def test_error_text_has_no_hits(self):
        self.assertEqual(cr.hit_files("ERROR: no Knowledge Bases are available"), [])

    def test_passes_when_expected_prefix_is_in_the_hits(self):
        self.assertEqual(self.run_main("systems__frontier_user_guide__", ["x.md", "systems__frontier_user_guide__running-jobs.md"]), 0)

    def test_exits_nonzero_on_a_wrong_expectation(self):
        self.assertEqual(self.run_main("systems__nonexistent__", ["systems__frontier_user_guide__running-jobs.md"]), 1)

    def test_exits_nonzero_when_nothing_is_found(self):
        self.assertEqual(self.quiet_main(["unused", "--questions", str(self.toml("a"))], lambda q: "ERROR: nope"), 1)

    def test_bad_questions_file_is_a_usage_error(self):
        p = Path(self.dir.name) / "bad.toml"
        p.write_text('[[question]]\nq = "x"\n', encoding="utf-8")
        self.assertEqual(self.quiet_main(["unused", "--questions", str(p)], lambda q: ""), 2)

    def test_shipped_questions_file_is_valid(self):
        qs = cr.load_questions(Path(cr.__file__).with_name("questions.toml"))
        self.assertGreaterEqual(len(qs), 20)


if __name__ == "__main__":
    unittest.main()
