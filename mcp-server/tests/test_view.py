"""
Tests for vista_mcp_server.lib.view utility functions.
"""
from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath

import pytest

from vista_mcp_server.lib.sandbox import DockerSandbox
from vista_mcp_server.lib.view import (
    DIRECTORY_LINE_LIMIT,
    LINE_LENGTH_LIMIT,
    TEXT_LINE_LIMIT,
    format_directory_listing,
    format_file_content,
    view_path,
)
from vista_mcp_server.config import settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_find_output(root: str, rel_paths: list[str]) -> str:
    """Return fake `find` output for the given relative paths under *root*.

    Intermediate directories are included automatically, mirroring real
    ``find`` output.
    """
    paths: list[str] = [root]
    seen: set[str] = {root}
    for rel in rel_paths:
        parts = rel.split('/')
        for i in range(len(parts)):
            full = str(PurePosixPath(root) / '/'.join(parts[:i + 1]))
            if full not in seen:
                paths.append(full)
                seen.add(full)
    return '\n'.join(paths)


class TestFormatFileContent:
    # --- basic line numbering ---

    def test_single_line(self):
        result = format_file_content(b'hello')
        assert result == '1\thello'

    def test_three_lines_numbered(self):
        result = format_file_content(b'a\nb\nc')
        lines = result.splitlines()
        assert lines[0] == '1\ta'
        assert lines[1] == '2\tb'
        assert lines[2] == '3\tc'

    def test_empty_content_returns_empty(self):
        assert format_file_content(b'') == ''

    def test_trailing_newline_not_duplicated(self):
        # 'a\nb\n'.splitlines() == ['a', 'b'] – no spurious blank line.
        result = format_file_content(b'a\nb\n')
        assert len(result.splitlines()) == 2

    # --- fixed-width padding ---

    def test_padding_for_ten_lines(self):
        content = '\n'.join(f'line {i}' for i in range(1, 11))
        lines = format_file_content(content).splitlines()
        # Width 2: line 1 → ' 1\t...', line 10 → '10\t...'
        assert lines[0].startswith(' 1\t')
        assert lines[9].startswith('10\t')

    def test_padding_width_based_on_total_file_length(self):
        # 100-line file: width 3 (numbers 001–100).
        content = '\n'.join(str(i) for i in range(1, 101))
        result = format_file_content(content, (1, 3))
        lines = result.splitlines()
        assert lines[0].startswith('  1\t')  # width=3, line 1

    # --- range selection ---

    def test_range_selects_correct_lines(self):
        content = '\n'.join(f'line {i}' for i in range(1, 11))
        lines = format_file_content(content, (3, 5)).splitlines()
        assert len(lines) == 3
        assert 'line 3' in lines[0]
        assert 'line 5' in lines[2]

    def test_range_line_numbers_reflect_position(self):
        content = '\n'.join(str(i) for i in range(1, 11))
        lines = format_file_content(content, (3, 5)).splitlines()
        # Numbers should be 3, 4, 5 (not 1, 2, 3).
        assert lines[0].split('\t')[0].strip() == '3'
        assert lines[2].split('\t')[0].strip() == '5'

    def test_range_clamps_end_to_file_length(self):
        content = 'a\nb\nc'
        lines = format_file_content(content, (2, 100)).splitlines()
        assert len(lines) == 2
        assert 'b' in lines[0]
        assert 'c' in lines[1]

    # --- negative indexing ---

    def test_negative_range_last_line(self):
        content = 'a\nb\nc\nd\ne'
        lines = format_file_content(content, (-1, -1)).splitlines()
        assert len(lines) == 1
        assert 'e' in lines[0]

    def test_negative_range_last_two_lines(self):
        content = 'a\nb\nc\nd\ne'
        lines = format_file_content(content, (-2, -1)).splitlines()
        assert len(lines) == 2
        assert 'd' in lines[0]
        assert 'e' in lines[1]

    def test_mixed_positive_negative_range(self):
        content = '\n'.join(str(i) for i in range(1, 6))  # '1\n2\n3\n4\n5'
        lines = format_file_content(content, (2, -2)).splitlines()
        # Lines 2–4
        assert len(lines) == 3
        assert '2' in lines[0]
        assert '4' in lines[2]

    # --- invalid range errors ---

    def test_invalid_range_start_greater_than_end(self):
        with pytest.raises(ValueError):
            format_file_content(b'a\nb\nc', (5, 3))

    def test_invalid_range_start_beyond_file(self):
        with pytest.raises(ValueError):
            format_file_content(b'a\nb\nc', (10, 15))

    def test_invalid_range_zero_start(self):
        with pytest.raises(ValueError):
            format_file_content(b'a\nb\nc', (0, 2))

    def test_invalid_negative_range_resolves_past_start(self):
        # -10 on a 3-line file → resolves to -6, which is < 1.
        with pytest.raises(ValueError):
            format_file_content(b'a\nb\nc', (-10, -1))

    # --- truncation ---

    def test_truncates_long_file(self):
        content = '\n'.join(f'line {i}' for i in range(1, TEXT_LINE_LIMIT + 50))
        lines = format_file_content(content).splitlines()
        assert len(lines) == TEXT_LINE_LIMIT + 1  # content lines + notice
        assert '...' in lines[-1]
        assert str(TEXT_LINE_LIMIT) in lines[-1]

    def test_truncation_notice_shows_original_count(self):
        extra = 25
        content = '\n'.join(str(i) for i in range(1, TEXT_LINE_LIMIT + extra + 1))
        last_line = format_file_content(content).splitlines()[-1]
        assert str(TEXT_LINE_LIMIT + extra) in last_line

    def test_truncates_long_lines(self):
        long_line = 'x' * (LINE_LENGTH_LIMIT + 50)
        lines = format_file_content(long_line).splitlines()
        content_part = lines[0].split('\t', 1)[1]
        assert content_part.endswith('...')
        assert len(content_part) == LINE_LENGTH_LIMIT + 3  # 300 chars + '...'

    def test_truncation_applied_even_with_range(self):
        content = '\n'.join(str(i) for i in range(1, TEXT_LINE_LIMIT + 100))
        lines = format_file_content(content, (1, TEXT_LINE_LIMIT + 50)).splitlines()
        assert len(lines) == TEXT_LINE_LIMIT + 1
        assert '...' in lines[-1]

    def test_no_truncation_notice_when_under_limit(self):
        content = '\n'.join(str(i) for i in range(1, TEXT_LINE_LIMIT))
        result = format_file_content(content)
        assert '... (' not in result


class TestFormatDirectoryListing:
    # --- basic structure ---

    def test_root_displayed_with_slash(self):
        output = make_find_output('/proj', ['a.py'])
        result = format_directory_listing(output, '/proj')
        assert result.startswith('proj/')

    def test_file_appears_in_output(self):
        output = make_find_output('/proj', ['a.py', 'b.py'])
        result = format_directory_listing(output, '/proj')
        assert 'a.py' in result
        assert 'b.py' in result

    def test_subdirectory_shown_with_slash(self):
        output = make_find_output('/proj', ['src/main.py'])
        result = format_directory_listing(output, '/proj')
        assert 'src/' in result

    def test_nested_file_indented(self):
        output = make_find_output('/proj', ['src/main.py'])
        result = format_directory_listing(output, '/proj')
        lines = result.splitlines()
        root_indent = len(lines[0]) - len(lines[0].lstrip())
        src_line = next(l for l in lines if 'src/' in l)
        src_indent = len(src_line) - len(src_line.lstrip())
        main_line = next(l for l in lines if 'main.py' in l)
        main_indent = len(main_line) - len(main_line.lstrip())
        assert src_indent > root_indent
        assert main_indent > src_indent

    def test_empty_directory(self):
        result = format_directory_listing('/proj', '/proj')
        assert 'proj/' in result

    # --- noisy directory truncation ---

    def test_node_modules_truncated(self):
        files = [f'node_modules/pkg{i}/index.js' for i in range(10)]
        files += ['src/main.py']
        output = make_find_output('/proj', files)
        result = format_directory_listing(output, '/proj')
        assert 'node_modules/' in result
        assert '...' in result
        # Individual packages should not be listed.
        assert 'pkg0' not in result

    def test_git_dir_truncated(self):
        files = ['src/main.py', '.git/HEAD', '.git/objects/ab/cd']
        output = make_find_output('/proj', files)
        result = format_directory_listing(output, '/proj')
        assert '.git/' in result
        assert '...' in result
        assert 'HEAD' not in result

    def test_pycache_truncated(self):
        files = ['src/__pycache__/foo.pyc', 'src/app.py']
        output = make_find_output('/proj', files)
        result = format_directory_listing(output, '/proj')
        assert '__pycache__/' in result
        assert 'foo.pyc' not in result

    def test_normal_dir_not_truncated(self):
        files = ['src/main.py', 'src/utils.py']
        output = make_find_output('/proj', files)
        result = format_directory_listing(output, '/proj')
        assert 'main.py' in result
        assert 'utils.py' in result

    # --- size-based truncation ---

    def test_large_output_capped(self):
        # 80 top-level files – must be capped at DIRECTORY_LINE_LIMIT.
        files = [f'file{i:03d}.py' for i in range(80)]
        output = make_find_output('/proj', files)
        result = format_directory_listing(output, '/proj')
        lines = result.splitlines()
        assert len(lines) <= DIRECTORY_LINE_LIMIT + 1

    def test_large_subdir_truncated_before_small_one(self):
        # 'big' has enough children to push the total over DIRECTORY_LINE_LIMIT;
        # 'small' has only a few, so it should survive.
        # Total without truncation: 1 (root) + 1 (big/) + 50 + 1 (small/) + 2 = 55 > 50.
        big_files = [f'big/file{i}.py' for i in range(50)]
        small_files = ['small/a.py', 'small/b.py']
        output = make_find_output('/proj', big_files + small_files)
        result = format_directory_listing(output, '/proj')
        # big should be truncated; small should remain visible.
        assert '...' in result
        assert 'a.py' in result

    def test_output_within_limit_not_modified(self):
        files = ['a.py', 'b.py', 'c.py']
        output = make_find_output('/proj', files)
        result = format_directory_listing(output, '/proj')
        lines = result.splitlines()
        assert len(lines) <= DIRECTORY_LINE_LIMIT
        # No truncation ellipsis expected.
        assert '...' not in result

    # --- sorting ---

    def test_directories_before_files(self):
        files = ['z_file.py', 'a_dir/x.py']
        output = make_find_output('/proj', files)
        result = format_directory_listing(output, '/proj')
        lines = result.splitlines()
        # Skip root line; next should be the directory.
        non_root = [l for l in lines if l.strip() and 'proj/' not in l]
        assert non_root[0].strip().endswith('/')

    def test_root_filesystem_listing(self):
        """Listing '/' must show root as '/' and stay within the line limit."""
        top_dirs = [
            'bin', 'boot', 'dev', 'etc', 'home', 'lib', 'lib64',
            'media', 'mnt', 'opt', 'proc', 'run', 'sbin',
            'srv', 'sys', 'tmp', 'usr', 'var',
        ]
        files = list(top_dirs)
        # Enough nested entries to push the count over DIRECTORY_LINE_LIMIT.
        files += [f'usr/bin/tool{i}' for i in range(30)]
        files += [f'etc/app{i}.conf' for i in range(20)]
        output = make_find_output('/', files)
        result = format_directory_listing(output, '/')
        lines = result.splitlines()
        assert lines[0] == '/'
        assert len(lines) <= DIRECTORY_LINE_LIMIT + 1


# ---------------------------------------------------------------------------
# view_path integration tests (use UnSandbox + tmp_path)
# ---------------------------------------------------------------------------

class TestViewPath:
    @pytest.fixture
    def anyio_backend(self):
        return 'asyncio'

    @pytest.fixture()
    async def sb(self, tmp_path):
        sandbox = await DockerSandbox.spawn(
            volumes=[(tmp_path, "/test", "r")],
            dockerfile=settings.dockerfile, image=settings.image,
        )
        try:
            yield sandbox, tmp_path
        finally:
            sandbox.close()

    @pytest.mark.anyio
    async def test_text_file(self, sb):
        sandbox, tmp_path = sb
        f = tmp_path / "hello.txt"
        f.write_text("line 1\nline 2\nline 3\n")
        result = await view_path(sandbox, "/test/hello.txt")
        assert "line 1" in result
        assert "line 2" in result
        assert "line 3" in result

    @pytest.mark.anyio
    async def test_text_file_line_numbers(self, sb):
        sandbox, tmp_path = sb
        f = tmp_path / "nums.txt"
        f.write_text("\n".join(f"line {i}" for i in range(1, 6)))
        lines = (await view_path(sandbox, "/test/nums.txt")).splitlines()
        assert lines[0].split('\t')[0].strip() == '1'
        assert lines[4].split('\t')[0].strip() == '5'

    @pytest.mark.anyio
    async def test_text_file_with_range(self, sb):
        sandbox, tmp_path = sb
        f = tmp_path / "range.txt"
        f.write_text("\n".join(f"line {i}" for i in range(1, 11)))
        result = await view_path(sandbox, "/test/range.txt", (3, 5))
        assert "line 3" in result
        assert "line 5" in result
        assert "line 1" not in result
        assert "line 6" not in result

    @pytest.mark.anyio
    async def test_text_file_negative_range(self, sb):
        sandbox, tmp_path = sb
        f = tmp_path / "neg.txt"
        f.write_text("a\nb\nc\nd\ne")
        result = await view_path(sandbox, "/test/neg.txt", (-2, -1))
        assert "d" in result
        assert "e" in result
        assert "a" not in result

    @pytest.mark.anyio
    async def test_binary_file(self, sb):
        sandbox, tmp_path = sb
        f = tmp_path / "data.bin"
        f.write_bytes(b'\x00\x01\x02\x03' * 256)
        result = await view_path(sandbox, "/test/data.bin")
        assert result == "[binary file]"

    @pytest.mark.anyio
    async def test_directory(self, sb):
        sandbox, tmp_path = sb
        (tmp_path / "dir/src").mkdir(parents = True)
        (tmp_path / "dir/src" / "main.py").write_text("print('hello')")
        (tmp_path / "dir/README.md").write_text("# Readme")
        result = await view_path(sandbox, "/test/dir")
        assert "src/" in result
        assert "README.md" in result

    @pytest.mark.anyio
    async def test_nonexistent_path(self, sb):
        sandbox, tmp_path = sb
        result = await view_path(sandbox, "/test/does_not_exist")
        assert result.startswith("Error:")

    @pytest.mark.anyio
    async def test_invalid_range_returns_error(self, sb):
        sandbox, tmp_path = sb
        f = tmp_path / "f.txt"
        f.write_text("a\nb\nc")
        result = await view_path(sandbox, "/test/f.txt", (10, 20))
        assert result.startswith("Error:")

    @pytest.mark.anyio
    async def test_directory_depth_limited(self, sb):
        sandbox, tmp_path = sb
        # depth from /test: a=1, b=2, c=3, d=4 — 'd' and its contents are beyond -maxdepth 3
        (tmp_path / "a" / "b" / "c" / "d").mkdir(parents=True)
        (tmp_path / "a" / "b" / "c" / "d" / "deep.txt").write_text("deep")
        result = await view_path(sandbox, "/test")
        assert "deep.txt" not in result
        assert "a/" in result
