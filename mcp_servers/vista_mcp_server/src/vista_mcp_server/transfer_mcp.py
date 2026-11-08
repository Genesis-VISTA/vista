"""
MCP for moving files to and from Odo and Frontier outside a job.

Three tools on the same Globus client the job tools use (`lib/globus.py`), each
acting as the researcher's own Globus identity, so the facility decides what
they may read and write:

- list any folder they can read (Transfer `ls`);
- download any file they can read into the project's output folder, at
  `transfers/<cluster>/<remote path>`, where the sandbox and `display_file`
  see it (HTTPS `GET`);
- upload a project file into `<remote_dir>.<user>.jobs/transfers/` and nowhere
  else (Transfer `mkdir`, HTTPS `PUT`).

Reads are unfenced because the facility already fences them. Uploads and
downloads each ask the researcher first, an Approve/Decline prompt per call that
lists what would move; a prompt that cannot be shown counts as a no. Uploads stay
fenced to `transfers/` as a second guard: an approval is only as careful as the
researcher reading it, and the fence limits what an approved upload can replace
to files in a folder meant for this. It sits in the sources folder their
identity already owns, so a job can read what lands there.
Perlmutter and Lux are not offered: Perlmutter's IRI download is text-only and
Lux moves files over SFTP.
"""
from __future__ import annotations
import logging, os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import globus_sdk
from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation
from mcp.types import ToolAnnotations

from .lib.globus import GlobusFileNotFound, GlobusSessionExpired, create_globus_client
from .lib.user_config import ProjectPaths, UserConfig, get_vista_meta
from .submit_job_mcp import (
    SANDBOX_OUTPUT_DIR, _confined_paths, _layout, _olcf_collection_id,
)


mcp = FastMCP("Transfer")

TransferCluster = Literal["odo", "frontier"]

TRANSFERS = "transfers"
""" The folder name for transfers: in the project's output folder, and in the sources folder on the cluster. """

SANDBOX_UPLOADS_DIR = PurePosixPath("/mnt/data/uploads")
""" Where the researcher's uploaded files appear inside the sandbox. """


Approve = Callable[[str], Awaitable[bool]]
""" Shows the researcher a message and answers whether they agreed. """

NOT_APPROVED = (
    "The researcher did not approve this transfer, so nothing was moved. Do not retry it; "
    "ask the researcher what they want instead. Transfers need the researcher's approval in a chat."
)
""" What the agent is told when the researcher declined, dismissed the prompt, or could not be asked. """


class TransferNotApproved(ToolError):
    def __init__(self):
        super().__init__(NOT_APPROVED)


async def _require_approval(approve: Approve, message: str) -> None:
    if not await approve(message):
        raise TransferNotApproved()


def _elicit_approver(ctx: Context) -> Approve:
    """ An `Approve` that asks the researcher through the chat; only an accepted "Approve" is a yes. """
    async def approve(message: str) -> bool:
        try:
            result = await ctx.elicit(message, response_type=["Approve", "Decline"])
        except Exception:
            logging.warning("Could not ask the researcher to approve a transfer", exc_info=True)
            return False
        return isinstance(result, AcceptedElicitation) and result.data == "Approve"
    return approve


def _remote_path(path: str, cluster: TransferCluster) -> str:
    """
    An absolute path on the cluster, normalised. Relative paths are refused because the
    HTTPS interface and Transfer would each read one against a different starting point,
    and `..` because a path that climbs is never needed and would defeat the local
    mirror's confinement.
    """
    posix = PurePosixPath(path)
    if not path.startswith("/") or "\\" in path or ".." in posix.parts:
        raise ToolError(
            f'"{path}" is not an absolute path on {cluster.title()}. Give the full path, '
            "starting with /, with no .. in it."
        )
    return str(posix)


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def hpc_list_files(
    ctx: Context, cluster: TransferCluster, path: str, recursive: bool = False,
) -> str:
    """
    List a folder on Odo or Frontier: each entry's type, size and absolute path.

    Works on any folder your Globus identity can read, such as a project's
    shared data on Orion, not only VISTA's own folders. Use it to find files and
    their sizes before downloading: large data should stay on the cluster and be
    processed by a job there.

    Args:
        cluster: "odo" or "frontier".
        path: Absolute path of the folder on the cluster.
        recursive: Also list everything below it. Stops after 200 folders.
            Hidden folders (names starting with ".") and __pycache__ are listed
            but not looked inside, marked "not walked"; list one by its own
            path to see what it holds.
    """
    return await _list_files(get_vista_meta(ctx).user, cluster, path, recursive)


async def _list_files(cfg: UserConfig, cluster: TransferCluster, path: str, recursive: bool) -> str:
    path = _remote_path(path, cluster)
    globus = create_globus_client(tokens=cfg.require_globus_token(cluster), cluster=cluster)
    collection_id = _olcf_collection_id(cluster)
    if recursive and path != "/":
        # The recursive walk reads a missing folder as an empty one, so ask once
        # whether it is there; a plain listing raises for that by itself.
        await globus.operation_stat(endpoint=collection_id, path=path)
    # Hidden folders are mostly what a job's tools leave behind (`.cache/pip`,
    # `.local`), and each folder walked is one more sequential Globus round trip.
    entries = await globus.operation_ls(
        endpoint=collection_id, path=path, recursive=recursive,
        exclude_segments=("__pycache__",), skip_hidden=True,
    )
    if not entries:
        return f"{path} on {cluster.title()} is empty."
    lines = [f"{len(entries)} entries under {path} on {cluster.title()}:"]
    for entry in entries:
        if entry.get("type") == "dir":
            note = "  (not walked)" if entry.get("walked") is False else ""
            lines.append(f"dir   {'':>9}  {entry['path']}{note}")
        else:
            lines.append(f"file  {_human_size(int(entry.get('size') or 0)):>9}  {entry['path']}")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def hpc_download_files(
    ctx: Context, cluster: TransferCluster, remote_paths: list[str],
) -> str:
    """
    Download files from Odo or Frontier into the sandbox.

    Any file your Globus identity can read can be fetched. Each lands at
    /mnt/data/output/transfers/<cluster>/<its remote path>, where the sandbox
    and display_file can open it. The researcher approves each download, so put
    every file you need in one call. A file already there is not fetched again
    unless it changed on the cluster, in which case it is fetched again by
    itself: never delete a local copy to refresh it. There is no size limit, so
    check sizes with hpc_list_files first and leave large data on the cluster
    for a job to process. For a job's own outputs, use get_hpc_job_outputs.

    Args:
        cluster: "odo" or "frontier".
        remote_paths: Absolute paths of the files on the cluster.

    Returns:
        The sandbox path of each file, split into those downloaded, those
        already up to date and not fetched, and any path that is not on the cluster.
    """
    meta = get_vista_meta(ctx)
    output_dir = Path(meta.project_paths.require_output_dir())
    return await _download_files(
        meta.user, output_dir, cluster, remote_paths, _elicit_approver(ctx),
    )


async def _download_files(
    cfg: UserConfig, output_dir: Path, cluster: TransferCluster, remote_paths: list[str],
    approve: Approve,
) -> str:
    if not remote_paths:
        raise ToolError("Name at least one file to download.")
    local_root = output_dir / TRANSFERS / cluster
    sandbox_root = SANDBOX_OUTPUT_DIR / TRANSFERS / cluster
    targets: list[tuple[str, Path, PurePosixPath]] = []
    for path in remote_paths:
        remote = _remote_path(path, cluster)
        try:
            local, sandbox = _confined_paths(local_root, sandbox_root, remote.lstrip("/"))
        except ValueError as e:
            raise ToolError(f'Cannot download "{path}": {e}.') from e
        if all(remote != seen for seen, _, _ in targets):
            targets.append((remote, local, sandbox))

    try:
        tokens = cfg.require_globus_token(cluster)
    except ToolError as e:
        if (offline := _held_locally(targets, cluster, e)) is not None:
            return offline
        raise
    globus = create_globus_client(tokens=tokens, cluster=cluster)
    collection_id = _olcf_collection_id(cluster)
    try:
        remote_entries = await _remote_entries(globus, collection_id, [remote for remote, _, _ in targets])
    except GlobusSessionExpired as e:
        if (offline := _held_locally(targets, cluster, e)) is not None:
            return offline
        raise

    fetched: set[str] = set()
    current: list[str] = []
    missing: list[str] = []
    to_fetch: list[tuple[str, Path, dict[str, Any], bool]] = []
    for remote, local, sandbox in targets:
        entry = remote_entries[remote]
        if entry is None:
            missing.append(remote)
        elif not (local.is_file() and local.stat().st_size > 0):
            to_fetch.append((remote, local, entry, False))
        elif _differs(local, entry):
            to_fetch.append((remote, local, entry, True))
        else:
            current.append(str(sandbox))
    if to_fetch:
        await _require_approval(approve, _download_prompt(cluster, sandbox_root, to_fetch))
    sandbox_paths = {remote: str(sandbox) for remote, _, sandbox in targets}
    for remote, local, entry, _ in to_fetch:
        try:
            await globus.download_file(collection_id=collection_id, remote_path=remote, local_path=local)
        except GlobusFileNotFound:
            # Gone since the listing.
            missing.append(remote)
            continue
        if (remote_time := _remote_time(entry)) is not None:
            os.utime(local, (remote_time, remote_time))
        fetched.add(sandbox_paths[remote])
    logging.info(f"Transfer download from {cluster}: {len(to_fetch)} fetched, {len(missing)} missing")

    lines = []
    if fetched:
        lines += [f"Downloaded from {cluster.title()}:", *(path for path in sandbox_paths.values() if path in fetched)]
    if current:
        lines += ["Already up to date (not fetched):", *current]
    if missing:
        lines += [f"Not on {cluster.title()}:", *missing]
    return "\n".join(lines)


def _held_locally(
    targets: list[tuple[str, Path, PurePosixPath]], cluster: TransferCluster, reason: ToolError,
) -> str | None:
    """ The local copies with a warning, when Globus cannot be asked and every file is already held. """
    if not all(local.is_file() and local.stat().st_size > 0 for _, local, _ in targets):
        return None
    return "\n".join([
        f"Could not check {cluster.title()} for newer versions: {reason}",
        "Already up to date (not fetched):", *(str(sandbox) for _, _, sandbox in targets),
    ])


async def _remote_entries(globus, collection_id: str, remotes: list[str]) -> dict[str, dict[str, Any] | None]:
    """
    Each remote path's file entry (size, last_modified), or None when it is not a file there.
    One listing per folder; a folder that may not be listed has its files `stat`ed one by one.
    """
    by_folder: dict[str, list[str]] = {}
    for remote in remotes:
        by_folder.setdefault(str(PurePosixPath(remote).parent), []).append(remote)
    entries: dict[str, dict[str, Any] | None] = {}
    for folder, paths in by_folder.items():
        found: dict[str, dict[str, Any]] = {}
        try:
            # Hidden files are not skipped: a requested `.env` must still be found.
            found = {
                entry["name"]: entry
                for entry in await globus.operation_ls(endpoint=collection_id, path=folder)
            }
        except GlobusFileNotFound:
            pass
        except globus_sdk.TransferAPIError as e:
            if e.http_status != 403:
                raise
            for path in paths:
                try:
                    found[PurePosixPath(path).name] = await globus.operation_stat(
                        endpoint=collection_id, path=path,
                    )
                except GlobusFileNotFound:
                    pass
        for path in paths:
            entry = found.get(PurePosixPath(path).name)
            entries[path] = entry if entry and entry.get("type") == "file" else None
    return entries


def _remote_time(entry: dict[str, Any]) -> int | None:
    """ The entry's `last_modified` as whole epoch seconds, or None when Transfer gave none. """
    if not entry.get("last_modified"):
        return None
    return int(datetime.fromisoformat(entry["last_modified"]).timestamp())


def _differs(local: Path, entry: dict[str, Any]) -> bool:
    """ Whether the local copy is not the remote file: another size, or another time when the time is known. """
    stat = local.stat()
    if stat.st_size != int(entry.get("size") or 0):
        return True
    remote_time = _remote_time(entry)
    return remote_time is not None and int(stat.st_mtime) != remote_time


def _download_prompt(
    cluster: TransferCluster, sandbox_root: PurePosixPath,
    to_fetch: list[tuple[str, Path, dict[str, Any], bool]],
) -> str:
    sizes = [int(entry.get("size") or 0) for _, _, entry, _ in to_fetch]
    noun = "file" if len(to_fetch) == 1 else "files"
    lines = [
        f"Download {len(to_fetch)} {noun} ({_human_size(sum(sizes))}) from {cluster.title()} into {sandbox_root}/:"
    ]
    path_width = max(len(remote) for remote, _, _, _ in to_fetch)
    size_width = max(len(_human_size(size)) for size in sizes)
    for (remote, _, _, changed), size in zip(to_fetch, sizes):
        line = f"  {remote:<{path_width}}  {_human_size(size):>{size_width}}"
        if changed:
            line += f"   changed on {cluster.title()}, replaces your local copy"
        lines.append(line)
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True,
))
async def hpc_upload_files(
    ctx: Context, cluster: TransferCluster, local_paths: list[str], remote_dir: str = "",
) -> str:
    """
    Upload files from the sandbox to Odo or Frontier, for a job to read.

    Files can come only from /mnt/data/output or /mnt/data/uploads, and go only
    into your transfers folder on the cluster, <remote folder>.<user>.jobs/transfers,
    in the subfolder `remote_dir`. A file of the same name there is replaced.
    The researcher approves each upload, so put every file you need in one call.
    Pass the reported remote paths to the job that should read them.

    Args:
        cluster: "odo" or "frontier".
        local_paths: Sandbox paths of the files to upload.
        remote_dir: Subfolder of the transfers folder to put them in, such as
            "inputs". Relative, without "..". Empty for the transfers folder itself.

    Returns:
        The absolute path on the cluster of each uploaded file.
    """
    meta = get_vista_meta(ctx)
    return await _upload_files(
        meta.user, meta.project_paths, cluster, local_paths, remote_dir, _elicit_approver(ctx),
    )


def _transfer_subdir(remote_dir: str) -> str:
    """ `remote_dir` as a clean relative path under `transfers/`, or "" for the folder itself. """
    posix = PurePosixPath(remote_dir)
    if remote_dir.startswith("/") or "\\" in remote_dir or ".." in posix.parts:
        raise ToolError(
            f'Uploads go only under your transfers folder; "{remote_dir}" is not a '
            "subfolder of it. Give a relative folder name such as \"inputs\", without \"..\"."
        )
    return "" if str(posix) == "." else str(posix)


def _local_source(paths: ProjectPaths, path: str) -> Path:
    """ The host file behind a sandbox path in the project's output or uploads folder. """
    posix = PurePosixPath(path)
    for sandbox_root, host_root in (
        (SANDBOX_OUTPUT_DIR, paths.output_dir), (SANDBOX_UPLOADS_DIR, paths.uploads_dir),
    ):
        if host_root and posix.is_relative_to(sandbox_root):
            try:
                local, _ = _confined_paths(
                    Path(host_root), sandbox_root, str(posix.relative_to(sandbox_root)),
                )
            except ValueError as e:
                raise ToolError(f'Cannot upload "{path}": {e}.') from e
            if not local.is_file():
                raise ToolError(f'Cannot upload "{path}": it is not a file.')
            return local
    raise ToolError(
        f'Cannot upload "{path}": only files under {SANDBOX_OUTPUT_DIR} or '
        f"{SANDBOX_UPLOADS_DIR} can be uploaded."
    )


async def _upload_files(
    cfg: UserConfig, paths: ProjectPaths, cluster: TransferCluster,
    local_paths: list[str], remote_dir: str, approve: Approve,
) -> str:
    if not local_paths:
        raise ToolError("Name at least one file to upload.")
    subdir = _transfer_subdir(remote_dir)
    sources = [_local_source(paths, path) for path in local_paths]
    names = [source.name for source in sources]
    if len(set(names)) != len(names):
        raise ToolError(
            "Two of the files have the same name, so one would replace the other. "
            "Upload them to different subfolders."
        )
    layout = _layout(cfg, cluster)

    globus = create_globus_client(tokens=cfg.require_globus_token(cluster), cluster=cluster)
    collection_id = _olcf_collection_id(cluster)
    layout = layout.with_user(await globus.home_owner(collection_id=collection_id))
    target = f"{layout.jobs}/{TRANSFERS}" + (f"/{subdir}" if subdir else "")
    # Listing `target` shows what the upload would replace; a folder that is not there yet
    # replaces nothing. Hidden files are listed too, since `.env` being replaced must be named.
    try:
        existing = {
            entry["name"] for entry in await globus.operation_ls(endpoint=collection_id, path=target)
            if entry.get("type") == "file"
        }
    except GlobusFileNotFound:
        existing = set()
    await _require_approval(approve, _upload_prompt(cluster, target, sources, existing))
    # From the folder holding the remote folder, as the job sources do: `.jobs`
    # itself is missing until the researcher's first job or upload.
    await globus.operation_mkdir_p(endpoint=collection_id, path=target, parents_below=layout.parent)
    uploaded = []
    for source in sources:
        remote = f"{target}/{source.name}"
        await globus.upload_file(collection_id=collection_id, local_path=source, remote_path=remote)
        uploaded.append(remote)
    logging.info(f"Transfer upload to {cluster}: {len(uploaded)} file(s) into {target}")
    return f"Uploaded to {cluster.title()}:\n" + "\n".join(uploaded)


def _upload_prompt(cluster: TransferCluster, target: str, sources: list[Path], existing: set[str]) -> str:
    sizes = [source.stat().st_size for source in sources]
    noun = "file" if len(sources) == 1 else "files"
    lines = [f"Upload {len(sources)} {noun} ({_human_size(sum(sizes))}) to {cluster.title()}, into {target}/:"]
    name_width = max(len(source.name) for source in sources)
    size_width = max(len(_human_size(size)) for size in sizes)
    for source, size in zip(sources, sizes):
        line = f"  {source.name:<{name_width}}  {_human_size(size):>{size_width}}"
        if source.name in existing:
            line += "   replaces the file there"
        lines.append(line)
    return "\n".join(lines)
