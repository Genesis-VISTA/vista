"""Tests for convert.split_page, on HTML shaped like Sphinx's output (no Sphinx).

Run with the converter's dependencies, from anywhere:

    uv run --no-project --with beautifulsoup4 --with markdownify python -I -m unittest discover -s scripts/olcf_docs -p 'test_convert.py'
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from convert import file_stem, split_page  # noqa: E402

URL = "https://docs.olcf.ornl.gov/"


def page(inner: str) -> str:
    return (
        '<html><body><div role="main" class="document"><div itemprop="articleBody">'
        f"{inner}</div></div></body></html>"
    )


def head(level: int, sid: str, title: str) -> str:
    return (
        f'<span id="x-{sid}"></span><h{level}>{title}'
        f'<a class="headerlink" href="#{sid}" title="Link">¶</a></h{level}>'
    )


CURRENT = page(
    f'<section id="frontier-user-guide">{head(1, "frontier-user-guide", "Frontier User Guide")}'
    '<p>Intro text before any section.</p>'
    f'<section id="system-overview">{head(2, "system-overview", "System Overview")}'
    "<p>Frontier is an HPE Cray EX system.</p></section>"
    f'<section id="running-jobs">{head(2, "running-jobs", "Running Jobs")}'
    "<p>Use <code>sbatch</code>. See "
    '<a class="reference internal" href="../connecting/index.html#connecting">connecting</a>.</p>'
    "</section></section>"
)

LEGACY = page(
    f'<section id="summit-user-guide">{head(1, "summit-user-guide", "Summit User Guide")}'
    '<div class="admonition warning"><p class="admonition-title">Warning</p>'
    "<p>This system was decommissioned on November 15, 2024 and is no longer online.</p></div>"
    '<div class="admonition note"><p class="admonition-title">Note</p><p>Archive only.</p></div>'
    f'<section id="overview">{head(2, "overview", "Overview")}<p>Summit was IBM.</p></section>'
    f'<section id="burst-buffer">{head(2, "burst-buffer", "Burst Buffer")}<p>NVMe.</p>'
    '<div class="admonition note"><p class="admonition-title">Note</p><p>Inner note.</p></div>'
    "</section></section>"
)

FILLER = " ".join(["word"] * 1200)
LONG = page(
    f'<section id="big-guide">{head(1, "big-guide", "Big Guide")}'
    f'<section id="running-jobs">{head(2, "running-jobs", "Running Jobs")}'
    f"<p>Lead text. {FILLER}</p>"
    f'<section id="batch-scripts">{head(3, "batch-scripts", "Batch Scripts")}<p>{FILLER}</p>'
    f'<section id="deep">{head(4, "deep", "Deeper")}<p>Deeper text stays in its subsection.</p></section>'
    "</section>"
    f'<section id="interactive">{head(3, "interactive", "Interactive Jobs")}<p>salloc.</p></section>'
    "</section>"
    f'<section id="short">{head(2, "short", "Short")}<p>Short section</p>'
    f'<section id="short-sub">{head(3, "short-sub", "Sub")}<p>kept whole</p></section></section>'
    "</section>"
)

INDEX = page(
    f'<section id="index-title">{head(1, "index-title", "Index")}'
    f'<section id="contents">{head(2, "contents", "Contents")}<p>Links.</p></section></section>'
)

RICH = page(
    f'<section id="rich">{head(1, "rich", "Rich Page")}'
    f'<section id="tabs">{head(2, "tabs", "Tabs")}'
    '<div class="sd-tab-set docutils">'
    '<input checked="checked" id="sd-tab-item-0" name="sd-tab-set-0" type="radio">'
    '<label class="sd-tab-label" for="sd-tab-item-0">\nPyTorch</label>'
    '<div class="sd-tab-content docutils"><p>Pull the torch image.</p></div>'
    '<input id="sd-tab-item-1" name="sd-tab-set-0" type="radio">'
    '<label class="sd-tab-label" for="sd-tab-item-1">\nJax</label>'
    '<div class="sd-tab-content docutils"><p>Pull the jax image.</p></div></div>'
    '<table class="docutils"><thead><tr><th><p>Queue</p></th><th><p>Nodes</p></th></tr></thead>'
    "<tbody><tr><td><p>batch</p></td><td><p>9408</p></td></tr></tbody></table>"
    '<div class="highlight-bash notranslate"><div class="highlight"><pre><span></span>'
    "ssh <span class=\"w\"> </span>frontier\n</pre></div></div>"
    "</section></section>"
)

NO_OUTER = page(
    "<p>Welcome to accounts.</p>"
    f'<section id="request">{head(1, "request", "Request")}<p>Ask.</p></section>'
    f'<section id="apply">{head(1, "apply", "Apply")}<p>Apply.</p></section>'
)


def by_name(files):
    return dict(files)


class SplitPageTest(unittest.TestCase):
    def test_current_page_two_sections_and_pre_section_file(self):
        files = by_name(split_page(CURRENT, "systems/frontier_user_guide"))
        self.assertEqual(
            sorted(files),
            [
                "systems__frontier_user_guide.md",
                "systems__frontier_user_guide__running-jobs.md",
                "systems__frontier_user_guide__system-overview.md",
            ],
        )
        jobs = files["systems__frontier_user_guide__running-jobs.md"]
        self.assertTrue(
            jobs.startswith(
                "# Frontier User Guide › Running Jobs\n\n"
                f"Source: {URL}systems/frontier_user_guide.html#running-jobs\n"
            ),
            jobs,
        )
        self.assertNotIn("> Notice:", jobs)
        self.assertIn("`sbatch`", jobs)
        self.assertNotIn("¶", jobs)  # no headerlink glyph
        # links are made absolute, so they work out of context
        self.assertIn(f"({URL}connecting/index.html#connecting)", jobs)
        pre = files["systems__frontier_user_guide.md"]
        self.assertTrue(
            pre.startswith(f"# Frontier User Guide\n\nSource: {URL}systems/frontier_user_guide.html\n"),
            pre,
        )
        self.assertIn("Intro text before any section.", pre)
        self.assertNotIn("Running Jobs", pre)

    def test_legacy_notice_on_every_file(self):
        files = split_page(LEGACY, "systems/summit_user_guide")
        self.assertEqual(len(files), 2)  # nothing but notices before the first section: no pre file
        for name, md in files:
            self.assertIn(
                "> Notice: This system was decommissioned on November 15, 2024 and is no longer online.",
                md,
                name,
            )
            self.assertIn("> Notice: Archive only.", md, name)
            self.assertNotIn("> Notice: Warning", md)
        burst = by_name(files)["systems__summit_user_guide__burst-buffer.md"]
        # an admonition inside a section is content, not a page notice
        self.assertNotIn("> Notice: Inner note.", burst)
        self.assertIn("Inner note.", burst)

    def test_legacy_notice_with_pre_text_keeps_pre_file_once(self):
        html = LEGACY.replace(
            '<section id="overview">', "<p>Lead paragraph.</p><section id=\"overview\">", 1
        )
        pre = by_name(split_page(html, "systems/summit_user_guide"))["systems__summit_user_guide.md"]
        self.assertEqual(pre.count("decommissioned"), 1)
        self.assertIn("Lead paragraph.", pre)

    def test_long_section_splits_once_at_subsections(self):
        files = by_name(split_page(LONG, "systems/big"))
        self.assertEqual(
            sorted(files),
            [
                "systems__big__batch-scripts.md",
                "systems__big__interactive.md",
                "systems__big__running-jobs.md",
                "systems__big__short.md",
            ],
        )
        own = files["systems__big__running-jobs.md"]
        self.assertIn("Lead text.", own)
        self.assertIn(f"{URL}systems/big.html#running-jobs", own)
        self.assertIn("# Big Guide › Running Jobs\n", own)
        self.assertNotIn("salloc", own)
        sub = files["systems__big__batch-scripts.md"]
        self.assertIn("# Big Guide › Running Jobs › Batch Scripts\n", sub)
        self.assertIn(f"{URL}systems/big.html#batch-scripts", sub)
        # no deeper splitting: the sub-subsection stays inside its subsection
        self.assertIn("Deeper text stays in its subsection.", sub)
        self.assertNotIn("systems__big__deep.md", files)
        # a short section keeps its subsections inside the one file
        self.assertIn("kept whole", files["systems__big__short.md"])

    def test_two_index_pages_do_not_collide(self):
        a = split_page(INDEX, "systems/index")
        b = split_page(INDEX, "data/index")
        names = [n for n, _ in a + b]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("systems__index__contents.md", names)
        self.assertIn("data__index__contents.md", names)

    def test_tabs_table_and_code_block(self):
        md = by_name(split_page(RICH, "software/rich"))["software__rich__tabs.md"]
        self.assertIn("**PyTorch**", md)
        self.assertIn("Pull the torch image.", md)
        self.assertLess(md.index("**Jax**"), md.index("Pull the jax image."))
        self.assertNotIn("radio", md)
        self.assertIn("| Queue | Nodes |", md)
        self.assertIn("| batch | 9408 |", md)
        self.assertIn("```bash\nssh  frontier\n```", md)

    def test_page_without_an_outer_section(self):
        files = by_name(split_page(NO_OUTER, "accounts/accounts", "Accounts and Projects"))
        self.assertEqual(
            sorted(files),
            [
                "accounts__accounts.md",
                "accounts__accounts__apply.md",
                "accounts__accounts__request.md",
            ],
        )
        self.assertIn("# Accounts and Projects › Apply\n", files["accounts__accounts__apply.md"])
        self.assertIn("Welcome to accounts.", files["accounts__accounts.md"])

    def test_page_with_no_sections_still_yields_a_file(self):
        html = page('<section id="t"><h1>Only Title</h1><p>Just text.</p></section>')
        files = split_page(html, "misc/only")
        self.assertEqual([n for n, _ in files], ["misc__only.md"])

    def test_refuses_a_path_holding_double_underscore(self):
        with self.assertRaises(ValueError):
            split_page(CURRENT, "systems/odd__name")
        with self.assertRaises(ValueError):
            file_stem("a__b/c")

    def test_names_unique_across_all_fixtures(self):
        names = []
        for html, path in [
            (CURRENT, "systems/frontier_user_guide"),
            (LEGACY, "systems/summit_user_guide"),
            (LONG, "systems/big"),
            (INDEX, "systems/index"),
            (INDEX, "data/index"),
            (RICH, "software/rich"),
        ]:
            names += [n for n, _ in split_page(html, path)]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
