#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["beautifulsoup4>=4.12", "markdownify>=0.13"]
# ///
"""Convert the OLCF user documentation into VISTA's `olcf-docs` knowledge-base corpus.

    uv run scripts/olcf_docs/convert.py <docs-checkout> <out-dir>

Builds a checkout of `olcf/olcf-user-docs` with `sphinx-build -b html` (its own
`conf.py` and extensions, in a throwaway environment), splits every page's HTML at
its top-level sections, and writes one Markdown file per section to <out-dir>,
plus `SOURCE`, which records the checkout's commit. A maintainer runs it when the
corpus is refreshed; nothing in a package build or on a researcher's machine does.

The splitting itself, `split_page`, is a pure function from one page's HTML to its
files, so the tests need no Sphinx. See openspec/changes/hello-olcf-default-project
(design.md, Decisions 1 to 3) for the format.
"""

from __future__ import annotations

import argparse
import copy
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

BASE_URL = "https://docs.olcf.ornl.gov/"
LONG_SECTION_WORDS = 2000
SEP = " › "  # the single right-pointing angle quotation mark in the header title


def file_stem(page_path: str) -> str:
    """The page path with `/` as `__`; refuses a path that already holds `__`."""
    if "__" in page_path:
        raise ValueError(
            f"source path {page_path!r} contains '__', so file names built from it could collide"
        )
    return page_path.replace("/", "__")


def _words(node: Tag) -> int:
    return len(node.get_text(" ").split())


def _text(node: Tag) -> str:
    return " ".join(node.get_text(" ").split())


def _heading(section: Tag) -> Tag | None:
    return section.find(["h1", "h2", "h3", "h4", "h5", "h6"], recursive=False)


def _title_of(section: Tag, fallback: str) -> str:
    h = _heading(section)
    if h is None:
        return fallback
    h = copy.copy(h)
    for link in h.select("a.headerlink"):
        link.decompose()
    return _text(h) or fallback


def _clean(node: Tag, page_url: str) -> None:
    """Strip what is no use as Markdown, and make links absolute."""
    for el in node.select("a.headerlink, button, input, script, style"):
        el.decompose()
    for a in node.find_all("a", href=True):
        a["href"] = urljoin(page_url, a["href"])
    # sphinx_design tab sets: a label, then its content, for each tab.
    for tabset in node.select("div.sd-tab-set"):
        out = BeautifulSoup("<div></div>", "html.parser")
        wrapper = out.div
        for label in tabset.find_all("label", class_="sd-tab-label", recursive=False):
            p = out.new_tag("p")
            strong = out.new_tag("strong")
            strong.string = _text(label)
            p.append(strong)
            wrapper.append(p)
            content = label.find_next_sibling("div", class_="sd-tab-content")
            if content is not None:
                wrapper.append(content.extract())
        tabset.replace_with(wrapper)


def _code_language(pre: Tag) -> str:
    for parent in pre.parents:
        for cls in parent.get("class") or []:
            if cls.startswith("highlight-"):
                lang = cls.removeprefix("highlight-")
                return "" if lang in ("default", "none", "text") else lang
    return ""


def _md(nodes: list[Tag | str], page_url: str) -> str:
    """Markdown for a run of sibling nodes."""
    holder = BeautifulSoup("<div></div>", "html.parser")
    for n in nodes:
        holder.div.append(copy.copy(n))
    _clean(holder.div, page_url)
    md = markdownify(
        str(holder.div),
        heading_style="ATX",
        bullets="-",
        strip=["img", "div"],
        code_language_callback=_code_language,
    )
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def _is_tag(n: object, name: str | None = None) -> bool:
    return isinstance(n, Tag) and (name is None or n.name == name)


def _is_admonition(n: object) -> bool:
    return _is_tag(n, "div") and "admonition" in (n.get("class") or [])


def _notice(admonition: Tag) -> str:
    """An admonition's text without its title ("Warning"), on one line."""
    box = copy.copy(admonition)
    for t in box.select(".admonition-title"):
        t.decompose()
    return _text(box)


def _header(title: str, url: str, notices: list[str]) -> str:
    head = f"# {title}\n\nSource: {url}\n"
    for n in notices:
        head += f"\n> Notice: {n}\n"
    return head


def _file(title: str, url: str, notices: list[str], body: str) -> str:
    out = _header(title, url, notices)
    if body:
        out += "\n" + body + "\n"
    return out


def split_page(html: str, page_path: str, page_title: str | None = None) -> list[tuple[str, str]]:
    """One page's HTML in, its section files out, as (file name, Markdown) pairs.

    `page_path` is the page's path in the repository without its suffix
    (`systems/frontier_user_guide`). `page_title` is used only when the page has
    no single `<h1>` section of its own to name it.

    What counts as a top-level section. Sphinx wraps a page in one outer
    `<section>` holding the `<h1>`, and the page's own sections are that
    section's child `<section>`s, so those are the top-level ones. A few pages
    have no single outer section (`accounts/accounts_and_projects` has an
    introduction and then four h1 sections; `training/olcf_gpu_hackathons` has
    two): there the body's own sections are the top-level ones.
    """
    stem = file_stem(page_path)
    page_url = f"{BASE_URL}{page_path}.html"
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find(attrs={"itemprop": "articleBody"}) or soup.body or soup
    body_children = [c for c in body.children if isinstance(c, Tag) or str(c).strip()]
    body_sections = [c for c in body_children if _is_tag(c, "section")]

    lead: list[Tag | str] = []  # body-level content before the outer section
    if len(body_sections) == 1:
        container = body_sections[0]
        title = _title_of(container, page_title or page_path)
        lead = body_children[: body_children.index(container)]
        children = [c for c in container.children if isinstance(c, Tag) or str(c).strip()]
        children = [c for c in children if c is not _heading(container)]
    else:
        container = body
        title = page_title or page_path
        children = body_children

    first = next((i for i, c in enumerate(children) if _is_tag(c, "section")), len(children))
    pre_nodes = lead + children[:first]
    notices = [_notice(n) for n in pre_nodes if _is_admonition(n)]
    pre_nodes = [
        n for n in pre_nodes if not _is_admonition(n)
    ]

    # Top-level sections, each with any stray non-section nodes that follow it.
    sections: list[tuple[Tag, list[Tag | str]]] = []
    for c in children[first:]:
        if _is_tag(c, "section"):
            sections.append((c, []))
        else:
            sections[-1][1].append(c)

    files: list[tuple[str, str]] = []

    pre_md = _md(pre_nodes, page_url)
    if pre_md or not sections:
        files.append((f"{stem}.md", _file(title, page_url, notices, pre_md)))

    def emit(name_id: str, sect_title: str, nodes: list[Tag | str]) -> None:
        md = _md(nodes, page_url)
        if md:
            files.append(
                (f"{stem}__{name_id}.md", _file(sect_title, f"{page_url}#{name_id}", notices, md))
            )

    for section, trailing in sections:
        sid = section.get("id")
        if not sid:
            raise ValueError(f"{page_path}: a top-level section has no id")
        stitle = f"{title}{SEP}{_title_of(section, sid)}"
        content = [c for c in section.children if c is not _heading(section)]
        content = [c for c in content if isinstance(c, Tag) or str(c).strip()]
        subs = [c for c in content if _is_tag(c, "section")]
        if _words(section) > LONG_SECTION_WORDS and subs:
            at = content.index(subs[0])
            # Text between or after the subsections is not a subsection's own, so
            # it stays with the text before the first one, in the section's file.
            strays = [c for c in content[at:] if not _is_tag(c, "section")]
            emit(sid, stitle, content[:at] + strays + trailing)
            for sub in subs:
                subid = sub.get("id")
                if not subid:
                    raise ValueError(f"{page_path}: a subsection has no id")
                subcontent = [c for c in sub.children if c is not _heading(sub)]
                emit(subid, f"{stitle}{SEP}{_title_of(sub, subid)}", subcontent)
        else:
            emit(sid, stitle, content + trailing)

    names = [n for n, _ in files]
    if len(set(names)) != len(names):
        raise ValueError(f"{page_path}: two sections produce the same file name")
    return files


# ─── command line ───────────────────────────────────────────────────────────

_PIP_LINE = re.compile(r"pip3?\s+install\s+(.+)")


def docs_requirements(checkout: Path) -> list[str]:
    """The checkout's own requirements: requirements.txt when it has one.

    olcf-user-docs has none; its contributing guide gives the pinned pip line, so
    that is read instead of repeating the pins here.
    """
    req = checkout / "requirements.txt"
    if req.is_file():
        return ["-r", str(req)]
    guide = checkout / "contributing" / "index.rst"
    if guide.is_file():
        m = _PIP_LINE.search(guide.read_text(encoding="utf-8"))
        if m:
            return m.group(1).split()
    raise SystemExit(f"no requirements.txt and no `pip install` line in {guide}")


def source_pages(checkout: Path) -> list[str]:
    pages = []
    for rst in sorted(checkout.rglob("*.rst")):
        rel = rst.relative_to(checkout)
        if any(p.startswith((".", "_")) for p in rel.parts):
            continue
        pages.append(rel.with_suffix("").as_posix())
    return pages


def build_html(checkout: Path, work: Path) -> tuple[Path, list[str]]:
    venv = work / "venv"
    subprocess.run(["uv", "venv", "--quiet", "--python", "3.12", str(venv)], check=True)
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        ["uv", "pip", "install", "--quiet", "--python", str(py), *docs_requirements(checkout)],
        check=True,
    )
    html = work / "html"
    # cwd is the checkout: a few pages `.. raw::` include files relative to it.
    proc = subprocess.run(
        [str(py), "-m", "sphinx", "-b", "html", "-q", "-d", str(work / "doctrees"), ".", str(html)],
        cwd=checkout,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    warnings = [ln for ln in proc.stderr.splitlines() if "WARNING" in ln or "ERROR" in ln or "CRITICAL" in ln]
    if proc.returncode != 0:
        print(proc.stderr[-3000:], file=sys.stderr)
        raise SystemExit(f"sphinx-build failed ({proc.returncode})")
    return html, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("checkout", type=Path, help="checkout of olcf/olcf-user-docs")
    ap.add_argument("out_dir", type=Path, help="where the section files go (empty or new)")
    args = ap.parse_args(argv)
    checkout, out = args.checkout.resolve(), args.out_dir.resolve()

    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty")
    pages = source_pages(checkout)
    if not pages:
        raise SystemExit(f"no .rst pages under {checkout}")
    for p in pages:
        file_stem(p)  # refuse a path holding `__` before building anything
    commit = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    if dirty:
        print("warning: the checkout has uncommitted changes; SOURCE names its HEAD anyway", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="olcf-docs-") as tmp:
        html_dir, warnings = build_html(checkout, Path(tmp))
        written: dict[str, str] = {}
        empty: list[str] = []
        for page in pages:
            f = html_dir / f"{page}.html"
            if not f.is_file():
                empty.append(page)
                continue
            raw = f.read_text(encoding="utf-8")
            t = re.search(r"<title>(.*?)</title>", raw, re.S)
            title = re.sub(r"\s+—\s+OLCF User Documentation\s*$", "", " ".join(t.group(1).split())) if t else None
            files = split_page(raw, page, title)
            if not files:
                empty.append(page)
            for name, md in files:
                if name in written:
                    raise SystemExit(f"{name} is produced twice (second time by {page})")
                written[name] = md

    out.mkdir(parents=True, exist_ok=True)
    for name, md in written.items():
        (out / name).write_text(md, encoding="utf-8", newline="\n")
    (out / "SOURCE").write_text(f"https://github.com/olcf/olcf-user-docs {commit}\n", encoding="utf-8", newline="\n")

    size = sum(len(m.encode("utf-8")) for m in written.values())
    print(f"pages: {len(pages)}  files: {len(written)}  bytes: {size}  commit: {commit}")
    print(f"sphinx warnings/errors: {len(warnings)}")
    for w in warnings:
        print("  " + w, file=sys.stderr)
    if empty:
        print("pages with no file: " + ", ".join(empty), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
