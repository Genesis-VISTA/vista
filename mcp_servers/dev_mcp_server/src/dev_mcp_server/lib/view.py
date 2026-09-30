"""
Utilities for the `view` MCP tool: binary detection, file formatting, and directory trees.
"""

from pathlib import PurePosixPath
from .sandbox import Sandbox

DIRECTORY_LINE_LIMIT = 50
TEXT_LINE_LIMIT = 300
LINE_LENGTH_LIMIT = 300

# Directories whose contents are generated/vendored and should always be collapsed.
NOISY_DIRS = {
    "node_modules",
    "__pycache__",
    ".git",
    "dist",
    "build",
    ".venv",
    "venv",
    ".next",
    ".nuxt",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "target",
    ".cache",
    "vendor",
    ".tox",
}


def _build_tree(paths: list[str], root: str) -> dict:
    """
    Build a nested tree from a flat list of absolute paths returned by `find`.

    Each node is a dict with keys:
        name      - basename of the entry
        path      - absolute path
        is_dir    - True if the entry is a directory
        children  - dict[str, node] (only meaningful when is_dir is True)
        truncated - True when the node's children have been collapsed to "..."
    """
    # Normalise: strip trailing slashes but keep '/' as-is so that
    # relative_to and path-joining work correctly for the filesystem root.
    root = root.rstrip("/") or "/"

    # A path is a directory if any other path is directly under it.
    dir_set: set[str] = {root}
    for p in paths:
        p = p.rstrip("/") or "/"
        parent = str(PurePosixPath(p).parent)
        if parent != p:
            dir_set.add(parent)

    def make_node(path: str, name: str) -> dict:
        return {
            "name": name,
            "path": path,
            "is_dir": path in dir_set,
            "children": {},
            "truncated": False,
        }

    # PurePosixPath('/').name == '' so the root renders as '/' not '//'
    root_node = make_node(root, PurePosixPath(root).name)

    for path in sorted(paths):
        path = path.rstrip("/") or "/"
        if path == root:
            continue
        try:
            rel = PurePosixPath(path).relative_to(root)
        except ValueError:
            continue

        parts = rel.parts
        node = root_node
        for i, part in enumerate(parts):
            if part not in node["children"]:
                # Use PurePosixPath to avoid double-slash when root == '/'
                full_path = str(PurePosixPath(root) / "/".join(parts[: i + 1]))
                node["children"][part] = make_node(full_path, part)
            node = node["children"][part]

    return root_node


def _count_lines(node: dict) -> int:
    """Count how many output lines *node* would produce when rendered."""
    if not node["is_dir"]:
        return 1
    if node["truncated"]:
        return 2  # "dirname/" line + "  ..." line
    return 1 + sum(_count_lines(c) for c in node["children"].values())


def _truncate_noisy(node: dict) -> None:
    """Collapse known generated/vendored directories in-place."""
    for name, child in node["children"].items():
        if child["is_dir"] and name in NOISY_DIRS:
            child["children"] = {}
            child["truncated"] = True
        elif child["is_dir"]:
            _truncate_noisy(child)


def _collect_truncatable_dirs(node: dict) -> list[dict]:
    """Collect all non-truncated subdirectory nodes (excluding root)."""
    result: list[dict] = []
    for child in node["children"].values():
        if child["is_dir"] and not child["truncated"]:
            result.append(child)
            result.extend(_collect_truncatable_dirs(child))
    return result


def _truncate_to_limit(root_node: dict) -> None:
    """Iteratively truncate the largest subdirectory until total <= DIRECTORY_LINE_LIMIT."""
    while _count_lines(root_node) > DIRECTORY_LINE_LIMIT:
        candidates = _collect_truncatable_dirs(root_node)
        if not candidates:
            break
        largest = max(candidates, key=_count_lines)
        largest["children"] = {}
        largest["truncated"] = True


def _render_tree(node: dict, indent: int = 0) -> list[str]:
    """Recursively render a tree node to a list of display lines."""
    prefix = "  " * indent
    name = node["name"]

    if node["is_dir"]:
        lines = [f"{prefix}{name}/"]
        if node["truncated"]:
            lines.append(f"{prefix}  ...")
        else:
            # Directories first, then files; each group sorted alphabetically.
            children = sorted(
                node["children"].items(),
                key=lambda x: (not x[1]["is_dir"], x[0]),
            )
            for _, child in children:
                lines.extend(_render_tree(child, indent + 1))
    else:
        lines = [f"{prefix}{name}"]

    return lines


def format_directory_listing(find_output: str, root: str) -> str:
    """Format the output of ``find <root>`` as an indented directory tree.

    Truncation strategy (applied in order):
    1. Noisy directories (node_modules, .git, etc.) are always collapsed.
    2. Remaining directories are collapsed largest-first until the total
       line count falls below ``DIRECTORY_LINE_LIMIT``.
    3. A hard cap clips any remaining overflow (e.g. a root with many files).

    Args:
        find_output: Newline-separated absolute paths produced by ``find``.
        root: The root directory that was passed to ``find``.

    Returns:
        A multi-line string representing the directory tree.
    """
    paths = [p for p in find_output.strip().splitlines() if p]
    tree = _build_tree(paths, root)
    _truncate_noisy(tree)
    _truncate_to_limit(tree)

    lines = _render_tree(tree)

    # Hard cap for the rare case where the root contains many flat files.
    if len(lines) > DIRECTORY_LINE_LIMIT:
        lines = lines[:DIRECTORY_LINE_LIMIT]
        lines.append("...")

    return "\n".join(lines)


def format_file_content(
    content: bytes | str, line_range: tuple[int, int] | None = None
) -> str:
    """Format file content with line numbers, optional range selection, and truncation.

    Lines are 1-indexed.  Negative indices count from the end of the file
    (e.g. ``-1`` refers to the last line).  Truncation to ``TEXT_LINE_LIMIT``
    lines and ``LINE_LENGTH_LIMIT`` characters per line is always applied,
    even when a range is explicitly given.

    Args:
        content: Raw file text.
        line_range: Optional ``(start, end)`` range (inclusive, 1-indexed).

    Returns:
        A string where each line is formatted as ``{line_num}\\t{content}``,
        with line numbers right-aligned to a fixed width.

    Raises:
        ValueError: If the range is invalid (start > end, or out of bounds).
    """
    if not isinstance(content, str):
        if not content:
            return ""
        # Is this printable text? Two checks, because each covers the other's
        # blind spot. A NUL byte is valid UTF-8 -- it encodes U+0000 -- so the
        # decode alone accepts binary padding. And some binary files hold no
        # NUL at all (matplotlib ships PDF icons like that), so the NUL scan
        # alone would spill them into the agent's context.
        #
        # This replaced `magic.from_buffer(content, mime=True)`, which needed a
        # system libmagic the package cannot ship, and which called every JSON
        # and SVG file binary because their MIME types are not under `text/`.
        # The cost is that text in a non-UTF-8 encoding now reads as binary.
        if b"\x00" in content[:8192]:
            return "[binary file]"
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            return "[binary file]"

    lines = content.splitlines()
    total = len(lines)

    if line_range is not None:
        start, end = line_range

        # Resolve negative indices (1-indexed: -1 == last line).
        if start < 0:
            start = total + start + 1
        if end < 0:
            end = total + end + 1

        if start < 1 or end < start:
            raise ValueError(
                f"Invalid range [{line_range[0]}, {line_range[1]}]: "
                f"resolves to [{start}, {end}]"
            )
        if start > total:
            raise ValueError(
                f"Start line {line_range[0]} is beyond end of file ({total} lines)"
            )

        end = min(end, total)
        selected = lines[start - 1 : end]
        offset = start
    else:
        selected = lines
        offset = 1

    if not selected:
        return ""

    original_count = len(selected)
    was_truncated = original_count > TEXT_LINE_LIMIT
    if was_truncated:
        selected = selected[:TEXT_LINE_LIMIT]

    # Pad line numbers to the width of the largest line number in the whole file.
    width = len(str(total))

    result: list[str] = []
    for i, line in enumerate(selected):
        line_num = offset + i
        if len(line) > LINE_LENGTH_LIMIT:
            line = line[:LINE_LENGTH_LIMIT] + "..."
        result.append(f"{line_num:{width}d}\t{line}")

    if was_truncated:
        result.append(f"... ({TEXT_LINE_LIMIT} of {original_count} lines shown)")

    return "\n".join(result)


async def view_path(
    sandbox: "Sandbox",
    path: str,
    line_range: tuple[int, int] | None = None,
) -> str:
    """View a file or directory inside *sandbox*.

    Dispatches to the appropriate formatter based on the file type reported
    by ``stat``.  Symlinks are followed (``stat -L``).

    Args:
        sandbox: Sandbox instance used to execute shell commands.
        path: Absolute path inside the sandbox.
        line_range: Optional ``(start, end)`` range for text files.

    Returns:
        - Directory: indented tree string.
        - Text file: line-numbered content.
        - Binary file: ``"[binary file]"``.
        - Error: a string starting with ``"Error:"``.
    """
    proc = await sandbox.exec("stat", args=["-L", "-c", "%F", path])
    stat_out, stat_err = await proc.communicate()

    if proc.returncode != 0:
        return f"Error: {stat_err.decode().strip() or 'path not found'}"

    file_type = stat_out.decode().strip()

    if file_type == "directory":
        proc = await sandbox.exec("find", args=[path, "-maxdepth", "3"])
        stdout, _ = await proc.communicate()
        return format_directory_listing(stdout.decode(), path)
    elif file_type == "regular file":
        proc = await sandbox.exec("cat", args=[path])
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"Error reading file: {stderr.decode().strip()}"
        try:
            return format_file_content(stdout, line_range)
        except ValueError as e:
            return f"Error: {e}"
    else:
        return f"Cannot view {file_type}"
