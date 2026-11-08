"""Tests for check_manifest.py. Stdlib unittest: the lint lane has no third-party packages."""

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_manifest  # noqa: E402

BASE = """
exclude = ["old.md"]

[[section]]
title = "Reference"
pages = [
  { file = "hpc.md", title = "HPC clusters" },
  { file = "installing.md", title = "Installing" },
]
"""

ALL = ["hpc.md", "installing.md", "old.md"]


class CheckManifest(unittest.TestCase):
    def run_check(self, files, manifest_text=BASE):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            for name in files:
                (docs / name).write_text("# x\n", encoding="utf-8")
            return check_manifest.check(docs, tomllib.loads(manifest_text))

    def assert_names(self, problems, *needles):
        for needle in needles:
            self.assertTrue(any(needle in p for p in problems), f"{needle!r} not in {problems}")

    def test_passes(self):
        self.assertEqual(self.run_check(ALL), [])

    def test_unlisted_new_doc(self):
        problems = self.run_check(ALL + ["new-guide.md"])
        self.assertEqual(len(problems), 1)
        self.assertIn("docs/new-guide.md is neither listed nor excluded", problems[0])

    def test_renamed_published_doc(self):
        problems = self.run_check(["clusters.md", "installing.md", "old.md"])
        self.assertEqual(len(problems), 2)
        self.assert_names(
            problems,
            "lists hpc.md, which is not in docs/",
            "clusters.md is neither listed nor excluded",
        )

    def test_listed_and_excluded(self):
        text = BASE.replace('exclude = ["old.md"]', 'exclude = ["old.md", "hpc.md"]')
        problems = self.run_check(ALL, text)
        self.assertEqual(len(problems), 1)
        self.assertIn("hpc.md is both listed and excluded", problems[0])

    def test_listed_twice_same_section(self):
        text = BASE.replace(
            '  { file = "installing.md", title = "Installing" },',
            '  { file = "installing.md", title = "Installing" },\n  { file = "hpc.md", title = "Again" },',
        )
        self.assert_names(self.run_check(ALL, text), "hpc.md is listed 2 times")

    def test_listed_twice_different_sections(self):
        text = BASE + '\n[[section]]\ntitle = "Dev"\npages = [{ file = "hpc.md", title = "Again" }]\n'
        self.assert_names(self.run_check(ALL, text), "hpc.md is listed 2 times")

    def test_page_missing_title(self):
        text = BASE.replace('{ file = "hpc.md", title = "HPC clusters" }', '{ file = "hpc.md" }')
        self.assert_names(self.run_check(ALL, text), "page hpc.md has no title")

    def test_page_missing_file(self):
        text = BASE.replace('{ file = "hpc.md", title = "HPC clusters" }', '{ title = "HPC clusters" }')
        self.assert_names(self.run_check(ALL, text), "has no file", "docs/hpc.md is neither listed")

    def test_section_missing_title(self):
        text = BASE.replace('title = "Reference"\n', "")
        self.assert_names(self.run_check(ALL, text), "[[section]] #1 has no title")

    def test_file_with_slash(self):
        text = BASE.replace('"hpc.md", title', '"sub/hpc.md", title')
        self.assert_names(self.run_check(ALL, text), "'sub/hpc.md' must be a bare name.md")

    def test_not_recursive(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            (docs / "figures").mkdir()
            (docs / "figures" / "deep.md").write_text("# x\n", encoding="utf-8")
            for name in ALL:
                (docs / name).write_text("# x\n", encoding="utf-8")
            self.assertEqual(check_manifest.check(docs, tomllib.loads(BASE)), [])


if __name__ == "__main__":
    unittest.main()
