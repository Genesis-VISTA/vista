"""
VISTA's IRI client: job submission, status and cancel on every cluster, and
NERSC's filesystem tasks for Perlmutter.

One client for every cluster, each named by a configured compute resource id
at a configured API version (`IriTarget`, from `iri_target`): S3M's IRI v2 for
Odo, Frontier and Lux, NERSC's IRI v1 for Perlmutter. Nothing is discovered: the
moderate enclave lists Lux before Frontier, and picking from a listing is how
Frontier jobs once went to Lux.

Compute calls differ between versions only in the path prefix. Filesystem calls
differ in method and argument placement (`_FS_OPS`). Both facilities' responses
are recorded under `tests/fixtures/iri/`, whose README says what they look like.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import httpx
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from ..config import settings

ApiVersion = Literal["v1", "v2"]
IriCluster = Literal["odo", "frontier", "lux", "perlmutter"]


class IriResourceSpec(BaseModel):
    node_count: int | None = 1
    process_count: int | None = None
    processes_per_node: int | None = None
    cpu_cores_per_process: int | None = None
    exclusive_node_use: bool = True
    gpus_per_node: int | None = None
    """ Lux only: rendered as `#SBATCH --gpus-per-node`. The IRI clusters bind GPUs themselves. """


class IriAttributes(BaseModel):
    """IRI-specific JobSpec attributes that vary per job (image, queue, constraint, ...)."""
    queue_name: str = "regular"
    constraint: str | None = None
    """ Slurm constraint, e.g. "gpu" on Perlmutter. Omitted on Frontier. """
    partition: str | None = None
    """
    Lux only: rendered as `#SBATCH -p`. Lux refuses a job that names no
    partition. `queue_name` there is the QOS (`-q`); the IRI clusters choose
    the partition themselves.
    """
    image: str | None = None
    module: str | None = None
    pre_launch: str | None = None
    """ Inline shell text run on the compute node before the main executable. """
    environment: dict[str, str] = {}


class IriDefaults(BaseModel):
    """ Per-job IRI resource and attribute defaults, loaded from cluster_defaults.json. """
    duration: int = 1800
    """ Seconds """
    resources: IriResourceSpec = IriResourceSpec()
    iri: IriAttributes = IriAttributes()


# ---------------------------------------------------------------------------- targets

@dataclass(frozen=True)
class IriTarget:
    """ Where one cluster's IRI service is, at which version, and which resources are its. """
    cluster: IriCluster
    base_url: str
    api_version: ApiVersion
    compute_resource_id: str
    storage_resource_id: str | None = None
    """ Only Perlmutter's: the OLCF clusters move files through Globus. """

    def url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/api/{self.api_version}/{path}"


_TOKEN_NAMES: dict[str, str] = {
    "odo": "Odo S3M token",
    "frontier": "Frontier S3M token",
    "lux": "Lux S3M token",
    "perlmutter": "NERSC IRI token",
}


def iri_target(cluster: IriCluster) -> IriTarget:
    """ The configured IRI target for `cluster` (the `<cluster>_iri_*` settings). """
    return IriTarget(
        cluster=cluster,
        base_url=getattr(settings, f"{cluster}_iri_url"),
        api_version=getattr(settings, f"{cluster}_iri_api_version"),
        compute_resource_id=getattr(settings, f"{cluster}_iri_resource_id"),
        storage_resource_id=settings.perlmutter_iri_storage_id if cluster == "perlmutter" else None,
    )


# ---------------------------------------------------------------------------- errors

class IriError(ToolError):
    """ An IRI call failed; the message says what to do about it. """


class IriFileNotFound(IriError):
    """ A filesystem task found nothing at the path (the facility's 404). """

    def __init__(self, path: str):
        super().__init__(f"No such file or directory: {path}")
        self.path = path


class IriTaskFailed(IriError):
    """ A filesystem task failed or was canceled, for a reason other than a missing path. """


class IriTaskTimeout(IriError):
    """ A filesystem task was still running when its bound ran out. """


# ---------------------------------------------------------------------------- job specs

# JobSpec fields that sit beside `attributes` on the wire. Callers may still pass
# them inside `attributes`, the shape amscrot took; they are lifted out here.
_JOBSPEC_TOP_LEVEL = (
    "name", "directory", "stdout_path", "stderr_path", "stdin_path",
    "inherit_environment", "environment", "pre_launch", "post_launch", "launcher",
    "container",
)
_JOB_ATTRIBUTES = ("duration", "queue_name", "account", "reservation_id", "custom_attributes")


def wire_job_spec(spec: dict[str, Any], *, name: str) -> dict[str, Any]:
    """
    The IRI `JobSpec` body for `spec`.

    Fields given as None are left out, since both facilities reject a null for a
    field with a minimum length, and `resource_id` is dropped: the resource is
    the target's, named in the path. NERSC refuses unknown top-level fields, so
    only `JobSpec`'s own reach the top level, and an unknown attribute is an error
    here rather than a silently ignored one there.
    """
    attrs = {k: v for k, v in (spec.get("attributes") or {}).items() if v is not None}
    attrs.pop("resource_id", None)
    body: dict[str, Any] = {"name": name}
    for key in _JOBSPEC_TOP_LEVEL:
        lifted = attrs.pop(key, None)
        value = spec.get(key, lifted)
        if value is not None:
            body[key] = value
    body["executable"] = spec.get("executable") or "bash"
    if spec.get("arguments"):
        body["arguments"] = list(spec["arguments"])
    resources = {k: v for k, v in (spec.get("resources") or {}).items() if v is not None}
    if resources:
        body["resources"] = resources
    if unknown := sorted(set(attrs) - set(_JOB_ATTRIBUTES)):
        raise ValueError(f"Unknown IRI job attributes: {unknown}")
    if attrs:
        body["attributes"] = attrs
    return body


_TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELED", "TIMEOUT"}


def normalize_job_status(job: dict[str, Any]) -> dict[str, Any]:
    """
    `{state, exit_code?, message?}` from an IRI `Job`.

    The state is upper-cased. Both facilities report exit code 0 for a job still
    in the queue, so the exit code is kept only once the job has finished. S3M
    sends an absent message as the string "None"; that and an empty one are
    dropped, as is `meta_data` (S3M's whole Slurm record).
    """
    status = job.get("status") or {}
    state = str(status.get("state") or "UNKNOWN").upper()
    out: dict[str, Any] = {"state": state}
    if state in _TERMINAL_STATES and status.get("exit_code") is not None:
        out["exit_code"] = status["exit_code"]
    message = status.get("message")
    if message is not None and str(message).strip() not in ("", "None"):
        out["message"] = str(message)
    return out


# ---------------------------------------------------------------------------- filesystem

@dataclass(frozen=True)
class _FsOp:
    v1_method: str
    """ The method on v1, which takes GET/DELETE arguments as query parameters; v2 POSTs JSON. """
    multipart: bool = False
    """ The file goes as multipart `file` and its path as a query parameter, on both versions. """


_FS_OPS: dict[str, _FsOp] = {
    "mkdir": _FsOp("POST"),
    "ls": _FsOp("GET"),
    "head": _FsOp("GET"),
    "stat": _FsOp("GET"),
    "download": _FsOp("GET"),
    "upload": _FsOp("POST", multipart=True),
}

_TASK_BOUND_S = 120.0
_TASK_FIRST_DELAY_S = 0.5
_TASK_MAX_DELAY_S = 5.0


def _size_bound(size: int) -> float:
    """ How long to wait for a transfer task: the usual bound plus 10 s per MB. """
    return _TASK_BOUND_S + size / 100_000


def _is_missing(error: str) -> bool:
    return "404" in error or "No such file or directory" in error


# ---------------------------------------------------------------------------- client

class IriClient:
    """
    One cluster's IRI service, as one researcher (`token`).

    Each request opens its own connection; a tool call makes a handful.
    `transport` and `sleep` are for tests (`httpx.MockTransport`, a fake clock).
    """

    def __init__(
        self,
        target: IriTarget,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout: float = 60.0,
    ):
        self.target = target
        self._token = token
        self._transport = transport
        self._sleep = sleep
        self._timeout = timeout

    @property
    def compute_resource_id(self) -> str:
        return self.target.compute_resource_id

    @property
    def storage_resource_id(self) -> str:
        if not self.target.storage_resource_id:
            raise IriError(
                f"{self.target.cluster}'s files are not reached through IRI; "
                "they go through Globus."
            )
        return self.target.storage_resource_id

    # ------------------------------------------------------------------ http

    async def _request(self, method: str, path: str, *, what: str, **kwargs) -> httpx.Response:
        t = self.target
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as http:
            try:
                response = await http.request(
                    method, t.url(path),
                    headers={"Authorization": f"Bearer {self._token}"},
                    **kwargs,
                )
            except httpx.TimeoutException as e:
                if method == "POST" and path.startswith("compute/job/"):
                    raise IriError(
                        f"Submitting to {t.cluster} timed out ({e.__class__.__name__}). "
                        "The job may have been submitted: check its status before resubmitting."
                    ) from e
                raise IriError(f"{what} on {t.cluster} timed out ({e.__class__.__name__}).") from e
            except httpx.HTTPError as e:
                raise IriError(f"{what} on {t.cluster} failed: {e}") from e
        if response.status_code >= 300:
            self._raise_for(response, what=what, path=path)
        return response

    def _raise_for(self, response: httpx.Response, *, what: str, path: str) -> None:
        t, code = self.target, response.status_code
        body = response.text[:300]
        if code in (401, 403):
            raise IriError(
                f"{t.cluster} rejected your {_TOKEN_NAMES[t.cluster]} (HTTP {code}). It may have "
                f"expired: mint a new one and save it in VISTA's Settings. ({body})"
            )
        if code == 404 and path.startswith("compute/job/"):
            raise IriError(
                f"IRI at {t.base_url} ({t.api_version}) has no compute resource "
                f"{t.compute_resource_id!r} for {t.cluster} (HTTP 404). Check the "
                f"`{t.cluster}_iri_resource_id` and `{t.cluster}_iri_api_version` settings. ({body})"
            )
        if code == 404 and path.startswith("compute/"):
            raise IriError(f"{what} on {t.cluster}: no such job (HTTP 404). ({body})")
        raise IriError(f"{what} on {t.cluster} failed: HTTP {code}: {body}")

    # ------------------------------------------------------------------ compute

    async def submit_job(self, spec: dict[str, Any], *, name: str) -> str:
        """
        Submit `spec` and return the job id. Never retried: neither facility honours
        an idempotency key, so a second POST would be a second job.
        """
        body = wire_job_spec(spec, name=name)
        response = await self._request(
            "POST", f"compute/job/{self.compute_resource_id}", what="Job submission", json=body,
        )
        job_id = (response.json() or {}).get("id")
        if not job_id:
            raise IriError(
                f"{self.target.cluster} accepted job {name!r} but returned no job id: "
                f"{response.text[:300]}"
            )
        return str(job_id)

    async def get_job_status(self, job_id: str) -> dict[str, Any]:
        """ `{state, exit_code?, message?}`; see `normalize_job_status`. """
        response = await self._request(
            "GET", f"compute/status/{self.compute_resource_id}/{job_id}", what="Job status",
        )
        return normalize_job_status(response.json() or {})

    async def cancel_job(self, job_id: str) -> None:
        await self._request(
            "DELETE", f"compute/cancel/{self.compute_resource_id}/{job_id}", what="Job cancel",
        )

    # ------------------------------------------------------------------ filesystem tasks

    async def _task(
        self,
        op: str,
        path: str,
        args: dict[str, Any] | None = None,
        *,
        files: dict | None = None,
        bound: float = _TASK_BOUND_S,
    ) -> Any:
        """ Run filesystem operation `op` on `path` as a task, and return its output. """
        spec = _FS_OPS[op]
        url_path = f"filesystem/{op}/{self.storage_resource_id}"
        fields = {"path": path, **(args or {})}
        what = f"{op} {path}"
        if spec.multipart:
            response = await self._request(
                "POST", url_path, what=what, params={"path": path}, files=files,
            )
        elif self.target.api_version == "v2" or spec.v1_method == "POST":
            response = await self._request("POST", url_path, what=what, json=fields)
        else:
            params = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in fields.items()}
            response = await self._request(spec.v1_method, url_path, what=what, params=params)
        task_id = (response.json() or {}).get("task_id")
        if not task_id:
            raise IriError(f"{what} on {self.target.cluster} returned no task: {response.text[:300]}")
        return await self._await_task(task_id, op=op, path=path, bound=bound)

    async def _await_task(self, task_id: str, *, op: str, path: str, bound: float) -> Any:
        """
        Poll the task until it finishes, waiting 0.5 s and doubling up to 5 s between
        polls, for at most `bound` seconds of waiting in all.
        """
        waited, delay = 0.0, _TASK_FIRST_DELAY_S
        while True:
            response = await self._request("GET", f"task/{task_id}", what=f"{op} {path}")
            task = response.json() or {}
            status = task.get("status")
            result = task.get("result") or {}
            if status == "completed":
                return result.get("output") if isinstance(result, dict) else result
            if status in ("failed", "canceled"):
                error = str(result.get("error") if isinstance(result, dict) else result)
                if _is_missing(error):
                    raise IriFileNotFound(path)
                raise IriTaskFailed(f"{op} {path} on {self.target.cluster} {status}: {error[:300]}")
            if waited >= bound:
                raise IriTaskTimeout(
                    f"{op} {path} on {self.target.cluster} was still {status or 'pending'} "
                    f"after {bound:.0f} s (task {task_id})."
                )
            await self._sleep(delay)
            waited += delay
            delay = min(delay * 2, _TASK_MAX_DELAY_S)

    async def mkdir(self, path: str | Path, *, parents: bool = True) -> None:
        await self._task("mkdir", str(path), {"parent": parents})

    async def ls(self, path: str | Path, *, recursive: bool = False) -> list[dict[str, Any]]:
        """
        Entries under `path`, each `{path, type, size}` with `path` under the path
        asked for, and the folder itself left out.

        NERSC names entries by their resolved path (`/global/u1/...` for a
        `/global/homes/...` folder) and lists the folder first, so the names are
        rewritten onto the path asked for, which is the one callers compare with.
        """
        root = str(path).rstrip("/")
        output = await self._task("ls", root, {"recursive": recursive}) or []
        names = [str(e.get("name", "")).rstrip("/") for e in output]
        real = names[0] if names else root
        if not all(n == real or n.startswith(real + "/") for n in names):
            real = root  # not the recorded shape: take the names as they come
        entries = []
        for e, name in zip(output, names):
            if name == real:
                continue
            rel = name[len(real):] if name.startswith(real + "/") else "/" + name.lstrip("/")
            entries.append({"path": root + rel, "type": e.get("type"), "size": int(e.get("size") or 0)})
        return entries

    async def head(self, path: str | Path, *, lines: int | None = None) -> str:
        output = await self._task("head", str(path), {"lines": lines} if lines else None)
        if isinstance(output, dict):
            return str(output.get("content") or "")
        return str(output or "")

    async def stat(self, path: str | Path) -> dict[str, Any]:
        return await self._task("stat", str(path)) or {}

    async def download(self, remote: str | Path, local: str | Path) -> str:
        """
        Download `remote` to `local`, text or binary, and return `local`.

        The facility returns a text file as its text and a binary one as base64,
        with nothing to say which, so the file's `stat` size decides: the text
        when its UTF-8 length matches, else the base64 when its decoded length
        does. Anything else fails, naming the file, rather than saving it altered.
        """
        remote = str(remote)
        size = int((await self.stat(remote)).get("size") or 0)
        output = await self._task("download", remote, bound=_size_bound(size))
        if not isinstance(output, str):
            raise IriError(f"Download of {remote} on {self.target.cluster} returned no content.")
        data = output.encode("utf-8")
        if len(data) != size:
            try:
                data = base64.b64decode(output, validate=True)
            except (binascii.Error, ValueError):
                data = b""
            if len(data) != size:
                raise IriError(
                    f"Download of {remote} on {self.target.cluster} matches its size "
                    f"({size} bytes) neither as text nor as base64; not saved."
                )
        Path(local).write_bytes(data)
        return str(local)

    async def upload(self, local: str | Path, remote: str | Path) -> None:
        data = Path(local).read_bytes()
        await self._task(
            "upload", str(remote),
            files={"file": (Path(remote).name, data, "application/octet-stream")},
            bound=_size_bound(len(data)),
        )


# ---------------------------------------------------------------------------- factories

def iri_client(cluster: IriCluster, token: str) -> IriClient:
    return IriClient(iri_target(cluster), token)


async def create_iri_client(*, iri_token: str) -> IriClient:
    """ NERSC IRI client (Perlmutter). """
    return iri_client("perlmutter", iri_token)


async def create_olcf_iri_client(*, iri_token: str) -> IriClient:
    """ S3M IRI client for Frontier (moderate enclave); its files go through Globus. """
    return iri_client("frontier", iri_token)


async def create_odo_iri_client(*, iri_token: str) -> IriClient:
    """ S3M IRI client for Odo (open enclave); its files go through Globus. """
    return iri_client("odo", iri_token)
