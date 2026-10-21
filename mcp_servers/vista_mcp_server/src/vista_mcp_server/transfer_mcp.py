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

Reads are unfenced because the facility already fences them. Writes are fenced
to `transfers/` because nothing asks the researcher before a tool runs, so the
folder is the only thing between an agent and their real files. It sits in the
sources folder their identity already owns, so a job can read what lands there.
Perlmutter and Lux are not offered: Perlmutter's IRI download is text-only and
Lux moves files over SFTP.
"""
from __future__ import annotations
import functools, logging
from pathlib import Path, PurePosixPath
from typing import Literal

from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .lib.globus import GlobusFileNotFound, create_globus_client
from .lib.user_config import ProjectPaths, UserConfig, get_vista_meta
from .submit_job_mcp import (
    SANDBOX_OUTPUT_DIR, _confined_paths, _fetch_once, _layout, _olcf_collection_id,
)


mcp = FastMCP("Transfer")

TransferCluster = Literal["odo", "frontier"]

TRANSFERS = "transfers"
""" The folder name for transfers: in the project's output folder, and in the sources folder on the cluster. """

SANDBOX_UPLOADS_DIR = PurePosixPath("/mnt/data/uploads")
""" Where the researcher's uploaded files appear inside the sandbox. """


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
    entries = await globus.operation_ls(endpoint=collection_id, path=path, recursive=recursive)
    if not entries:
        return f"{path} on {cluster.title()} is empty."
    lines = [f"{len(entries)} entries under {path} on {cluster.title()}:"]
    for entry in entries:
        if entry.get("type") == "dir":
            lines.append(f"dir   {'':>9}  {entry['path']}")
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
    and display_file can open it. A file already downloaded there is not fetched
    again; delete the local copy to fetch a newer version. There is no size
    limit, so check sizes with hpc_list_files first and leave large data on the
    cluster for a job to process. For a job's own outputs, use
    get_hpc_job_outputs.

    Args:
        cluster: "odo" or "frontier".
        remote_paths: Absolute paths of the files on the cluster.

    Returns:
        The sandbox path of each file, and any path that is not on the cluster.
    """
    meta = get_vista_meta(ctx)
    output_dir = Path(meta.project_paths.require_output_dir())
    return await _download_files(meta.user, output_dir, cluster, remote_paths)


async def _download_files(
    cfg: UserConfig, output_dir: Path, cluster: TransferCluster, remote_paths: list[str],
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
        targets.append((remote, local, sandbox))

    globus = functools.cache(
        lambda: create_globus_client(tokens=cfg.require_globus_token(cluster), cluster=cluster)
    )
    saved: list[str] = []
    missing: list[str] = []
    for remote, local, sandbox in targets:
        try:
            await _fetch_once(
                globus, collection_id=_olcf_collection_id(cluster),
                remote_path=remote, local_path=local,
            )
        except GlobusFileNotFound:
            missing.append(remote)
            continue
        saved.append(str(sandbox))
    logging.info(f"Transfer download from {cluster}: {len(saved)} saved, {len(missing)} missing")

    lines = []
    if saved:
        lines += ["Downloaded files:", *saved]
    if missing:
        lines += [f"Not on {cluster.title()}:", *missing]
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
    return await _upload_files(meta.user, meta.project_paths, cluster, local_paths, remote_dir)


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
    local_paths: list[str], remote_dir: str,
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
