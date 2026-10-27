"""Tests for sync_docs.py. Stdlib unittest (PyYAML is the one third-party import)."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_docs  # noqa: E402
from sync_docs import normalize_lists, rewrite_links  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SHA = "0" * 40
BLOB = f"https://github.com/Genesis-VISTA/vista/blob/{SHA}"
PAGES = {"hpc.md", "hpc-jobs.md"}


def md(text):
    return textwrap.dedent(text).strip("\n")


class RewriteLinks(unittest.TestCase):
    def check(self, link, expected, assets=frozenset()):
        text, found = rewrite_links(f"See {link}.", PAGES, SHA)
        self.assertEqual(text, f"See {expected}.")
        self.assertEqual(found, set(assets))

    def test_a_synced_page_stays_relative_and_keeps_its_anchor(self):
        self.check("[jobs](hpc-jobs.md#self-contained-hpc-skills)",
                   "[jobs](hpc-jobs.md#self-contained-hpc-skills)")

    def test_a_page_that_is_not_synced_goes_to_the_source(self):
        self.check("[releasing](releasing.md#rollback)",
                   f"[releasing]({BLOB}/docs/releasing.md#rollback)")

    def test_a_source_file_goes_to_the_source(self):
        self.check("[agents](../backend/src/vista_backend/agents/agents.py)",
                   f"[agents]({BLOB}/backend/src/vista_backend/agents/agents.py)")

    def test_a_directory_goes_to_the_source(self):
        self.check("[example](../hpc_jobs/example/)", f"[example]({BLOB}/hpc_jobs/example)")

    def test_a_dot_slash_link_resolves_like_any_other(self):
        self.check("[hpc](./hpc.md)", "[hpc](hpc.md)")

    def test_a_figure_is_copied_and_stays_relative(self):
        self.check("![lab](figures/hypothesis-lab.svg)", "![lab](figures/hypothesis-lab.svg)",
                   {"figures/hypothesis-lab.svg"})

    def test_external_links_and_same_page_anchors_are_left_alone(self):
        for link in ("[olcf](https://docs.olcf.ornl.gov/)", "[up](#credentials)",
                     "[mail](mailto:vista@example.org)"):
            self.check(link, link)

    def test_a_link_title_survives(self):
        self.check('[ci](../.gitlab-ci.yml "CI")', f'[ci]({BLOB}/.gitlab-ci.yml "CI")')

    def test_link_text_in_code_is_still_a_link(self):
        self.check("[`lib/globus.py`](../mcp_servers/lib/globus.py)",
                   f"[`lib/globus.py`]({BLOB}/mcp_servers/lib/globus.py)")

    def test_link_text_that_wraps_onto_the_next_line_is_still_a_link(self):
        out, _ = rewrite_links("[the agent build in\nagents.py:95](../backend/agents.py).", PAGES, SHA)
        self.assertEqual(out, f"[the agent build in\nagents.py:95]({BLOB}/backend/agents.py).")

    def test_links_in_code_are_left_alone(self):
        text = md("""
            `[not](../a.md)` and [yes](../a.py)
            ```
            [not](../b.md)
            ```
        """)
        out, _ = rewrite_links(text, PAGES, SHA)
        self.assertEqual(out, md(f"""
            `[not](../a.md)` and [yes]({BLOB}/a.py)
            ```
            [not](../b.md)
            ```
        """))


class NormalizeLists(unittest.TestCase):
    def test_a_two_space_nested_list_is_indented_four(self):
        # The shape of docs/hpc.md's Globus states.
        source = md("""
            - **Globus session expired.** The row shows the card's message. Either way
              for that cluster in Settings.
              - A lapsed login reads "The Globus session for Odo has expired", with
                `<domain>`.
              - A saved connection no longer accepted reads something else.

            After the list.
        """)
        self.assertEqual(normalize_lists(source), md("""
            - **Globus session expired.** The row shows the card's message. Either way
                for that cluster in Settings.
                - A lapsed login reads "The Globus session for Odo has expired", with
                    `<domain>`.
                - A saved connection no longer accepted reads something else.

            After the list.
        """))

    def test_a_list_straight_after_a_paragraph_gets_a_blank_line(self):
        # The shape of docs/splash-planner-playbook.md's plan.
        source = md("""
            **B · Draft the plan**
            - Propose the cyclic plan:
              1. Pick the candidates.
              2. Collect per-candidate TBR and
                 thermophysical properties.
            - Show the job count.
        """)
        self.assertEqual(normalize_lists(source), md("""
            **B · Draft the plan**

            - Propose the cyclic plan:
                1. Pick the candidates.
                2. Collect per-candidate TBR and
                    thermophysical properties.
            - Show the job count.
        """))

    def test_a_list_already_indented_four_is_unchanged(self):
        source = md("""
            1. Fill in the dialog:
                - **Name:** `LLM modeling`
                - **Skills:** check both.
            2. Click **Create**.
        """)
        self.assertEqual(normalize_lists(source), source)

    def test_a_fence_in_a_list_moves_with_its_item(self):
        source = md("""
            - Run:
              ```bash
              uv run vista-backend
                --flag
              ```
            - Then this.
        """)
        self.assertEqual(normalize_lists(source), md("""
            - Run:
                ```bash
                uv run vista-backend
                  --flag
                ```
            - Then this.
        """))

    def test_yaml_in_a_fence_is_not_a_list(self):
        source = md("""
            ```yaml
            variables:
              - {name: temperature, range: [700, 1000]}
            ```
        """)
        self.assertEqual(normalize_lists(source), source)

    def test_text_and_indented_code_outside_lists_are_unchanged(self):
        source = md("""
            A paragraph.

                indented code

            | a | b |
            |---|---|
        """)
        self.assertEqual(normalize_lists(source), source)


MANIFEST = """
exclude = ["releasing.md"]

[[section]]
title = "Reference"
pages = [{ file = "hpc.md", title = "HPC clusters" }]

[[section]]
title = "Developers"
pages = [{ file = "development.md", title = "Developing VISTA" }]
"""

SITE_YML = """
site_name: t
docs_dir: documentation
nav:
  - Home: index.md
  - About: about.md
markdown_extensions:
  - pymdownx.emoji:
      emoji_index: !!python/name:material.extensions.emoji.twemoji
"""


def git_commit(repo):
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "docs", "--allow-empty"], check=True)


class Sync(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.site, self.vista = tmp / "site", tmp / "vista"
        (self.site / "documentation").mkdir(parents=True)
        (self.vista / "docs" / "figures").mkdir(parents=True)

    def write_vista(self, manifest=MANIFEST, pages=("hpc.md", "development.md")):
        docs = self.vista / "docs"
        (docs / "site.toml").write_text(manifest, encoding="utf-8")
        (docs / "hpc.md").write_text(
            "# HPC clusters\n\nSee ![x](figures/x.png) and [s](../backend/s.py).\n",
            encoding="utf-8")
        (docs / "figures" / "x.png").write_bytes(b"png")
        for name in pages:
            if name != "hpc.md":
                (docs / name).write_text(f"# {name}\n\n[h](hpc.md)\n", encoding="utf-8")
        (docs / "releasing.md").write_text("# Releasing\n", encoding="utf-8")
        git_commit(self.vista)

    def write_site(self, text=SITE_YML):
        (self.site / "mkdocs.yml").write_text(text, encoding="utf-8")

    def generated(self):
        return yaml.safe_load((self.site / "mkdocs.generated.yml").read_text(encoding="utf-8"))

    def test_copies_the_listed_pages_into_documentation_reference(self):
        self.write_vista()
        self.write_site()

        self.assertEqual(sync_docs.sync(self.vista, self.site, "v9.9.9"), ["hpc.md", "development.md"])

        out = self.site / "documentation" / "reference"
        self.assertEqual(sorted(p.name for p in out.rglob("*")),
                         ["development.md", "figures", "hpc.md", "x.png"])
        page = (out / "hpc.md").read_text(encoding="utf-8")
        self.assertTrue(page.startswith('# HPC clusters\n\n!!! info "From the VISTA repository"'))
        self.assertIn("as of VISTA v9.9.9", page)
        self.assertIn("![x](figures/x.png)", page)
        self.assertIn("/blob/", page)
        # a link to a page in another section stays relative
        self.assertIn("[h](hpc.md)", (out / "development.md").read_text(encoding="utf-8"))

    def test_the_generated_nav_is_the_sites_then_each_section(self):
        self.write_vista()
        self.write_site()
        sync_docs.sync(self.vista, self.site, "v1")

        config = self.generated()
        self.assertEqual(set(config), {"INHERIT", "nav"})
        self.assertEqual(config["INHERIT"], "mkdocs.yml")
        self.assertEqual(config["nav"], [
            {"Home": "index.md"},
            {"About": "about.md"},
            {"Reference": [{"HPC clusters": "reference/hpc.md"}]},
            {"Developers": [{"Developing VISTA": "reference/development.md"}]},
        ])
        text = (self.site / "mkdocs.generated.yml").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Generated by scripts/docs_site/sync_docs.py"))

    def test_the_sites_mkdocs_yml_with_python_tags_is_read_and_left_untouched(self):
        self.write_vista()
        self.write_site()
        before = (self.site / "mkdocs.yml").read_bytes()
        sync_docs.sync(self.vista, self.site, "v1")
        self.assertEqual((self.site / "mkdocs.yml").read_bytes(), before)
        self.assertNotIn("python/name", (self.site / "mkdocs.generated.yml").read_text(encoding="utf-8"))

    def test_a_site_nav_that_already_lists_reference_fails(self):
        self.write_vista()
        self.write_site("nav:\n  - Home: index.md\n  - Old:\n      - Hpc: reference/hpc.md\n")
        with self.assertRaisesRegex(SystemExit, "reference/"):
            sync_docs.sync(self.vista, self.site, "v1")

    def test_a_listed_page_missing_from_the_release_fails(self):
        self.write_vista(pages=("hpc.md",))  # development.md is listed but absent
        self.write_site()
        with self.assertRaisesRegex(SystemExit, "development.md"):
            sync_docs.sync(self.vista, self.site, "v1")

    def test_main_takes_vista_site_and_ref(self):
        self.write_vista()
        self.write_site()
        sync_docs.main([str(self.vista), str(self.site), "--ref", "v1"])
        self.assertTrue((self.site / "documentation" / "reference" / "hpc.md").is_file())

    def test_the_real_manifest_and_docs_sync_into_a_minimal_site(self):
        self.write_site("site_name: t\ndocs_dir: documentation\nnav: [{Home: index.md}]\n")
        pages = sync_docs.sync(REPO_ROOT, self.site, "v0")
        out = self.site / "documentation" / "reference"
        self.assertEqual(len(pages), 12)
        self.assertEqual(len(list(out.glob("*.md"))), 12)
        self.assertEqual(len(self.generated()["nav"]), 3)


if __name__ == "__main__":
    unittest.main()
