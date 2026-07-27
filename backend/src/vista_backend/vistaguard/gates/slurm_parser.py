"""
SLURM job-script parser for VISTAGuard's G5 HPC Job Gate.

This module is **pure parsing**: it turns the text of a resolved SLURM
job script -- the kind ``submit_job_mcp.py`` produces by inlining a
job's ``job.slurm`` template into a ``/bin/bash -l -c`` payload -- into
a structured :class:`SlurmScript`. It contains *no* policy logic. The
gate (:mod:`vista_backend.vistaguard.gates.g5_hpc`) consumes the parse
result and applies allow-lists, resource ceilings, and IOC matching.

The split mirrors G4's ``slurm_parser`` / ``g5_hpc`` separation of the
``_extract_code`` helper from the gate: the parser is exhaustively
unit-testable against curated fixtures without ever constructing a
gate, a registry, or a PydanticAI agent.

## What the parser walks

1. **``#SBATCH`` directives.** Every standard form is handled --
   ``#SBATCH --nodes=4``, ``#SBATCH -N 4``, ``#SBATCH --nodes 4``,
   ``-N4`` -- and short flags are mapped to their long names. The
   resource-bearing directives (``--nodes``, ``--time``, ``--ntasks``,
   ``--cpus-per-task``, ``--gpus`` / ``--gpus-per-node`` / ``--gres``,
   ``--account``, ``--partition``, ``--qos``, ``--dependency``) are
   surfaced as typed fields on :class:`SbatchDirectives`; everything
   else is preserved in ``raw`` for diagnostics.

2. **Command / binary references.** The body is split into commands on
   shell control operators (``;`` ``&&`` ``||`` ``|`` ``&`` and
   newlines), respecting quotes and ``$( ... )`` / backtick
   substitutions. Each command's leading executable is resolved by
   peeling off leading ``VAR=value`` assignments and known wrappers
   (``srun``, ``mpirun``, ``shifter``, ``env``, ``sudo``, ``nohup``,
   ``time``, ``exec``, ``xargs`` ...). ``bash -c`` / ``sh -c`` / ``eval``
   bodies are recursively parsed so an embedded launcher is not hidden
   behind one level of quoting.

3. **File-path references.** Path-like tokens (absolute, ``~``-rooted,
   ``./`` / ``../``-relative, or containing a ``/``) are collected so
   the gate's path-scoping check can match sensitive prefixes
   (``~/.ssh``, ``/etc/``, ``~/.aws`` ...).

4. **Network egress targets.** Hosts referenced by ``curl`` / ``wget``
   / ``scp`` / ``sftp`` / ``rsync`` / ``nc`` invocations -- both
   ``http(s)://host/...`` URLs and ``user@host:path`` SCP forms -- are
   extracted so the gate can match them against an allow-list.

5. **Multi-stage payload structure.** The three SciAgentBench B5.1
   delivery shapes are flagged: download-then-execute
   (``wget ... && ./bin``), decode-pipe-to-shell (``base64 -d | bash``),
   and eval-of-substitution (``eval $(curl ...)``).

The parser is defensive: it never raises on malformed input. A line it
cannot tokenize is recorded as a best-effort whitespace split rather
than dropped, because for a security gate a *partial* view of a
command is strictly better than silently ignoring it.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


# -----------------------------------------------------------------
# Directive parsing
# -----------------------------------------------------------------


# Short SLURM flag -> long directive name. Only the resource-bearing
# flags G5 reasons about are mapped; unknown short flags are recorded
# under their raw key.
_SHORT_FLAG_TO_LONG: dict[str, str] = {
    "N": "nodes",
    "n": "ntasks",
    "c": "cpus-per-task",
    "t": "time",
    "A": "account",
    "p": "partition",
    "q": "qos",
    "d": "dependency",
    "G": "gpus",
}

# Long directives that take no value (boolean switches). Used so a
# bare ``--exclusive`` doesn't swallow the following token as its
# value.
_BOOLEAN_LONG_FLAGS: frozenset[str] = frozenset(
    {"exclusive", "no-requeue", "requeue", "overcommit", "verbose"}
)

_SBATCH_PREFIX = "#SBATCH"


@dataclass(frozen=True)
class DependencyRef:
    """
    One parsed ``--dependency`` clause, e.g. ``afterok:123:124`` ->
    ``DependencyRef(kind="afterok", job_ids=("123", "124"))``.

    ``singleton`` (and any other id-less form) yields an empty
    ``job_ids``.
    """

    kind: str
    job_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SbatchDirectives:
    """
    Typed view of the resource-bearing ``#SBATCH`` directives.

    Every field is ``None`` when the directive is absent. ``raw``
    preserves the full ``(key, value)`` list (long-name keys, short
    flags resolved) so diagnostics and future checks can read
    directives this dataclass doesn't promote to a field.
    """

    nodes: int | None = None
    time_seconds: int | None = None
    ntasks: int | None = None
    cpus_per_task: int | None = None
    gpus: int | None = None
    gpus_per_node: int | None = None
    account: str | None = None
    partition: str | None = None
    qos: str | None = None
    dependencies: tuple[DependencyRef, ...] = ()
    raw: tuple[tuple[str, str], ...] = ()

    @property
    def total_gpus(self) -> int | None:
        """
        Best-effort total GPU count.

        Prefers an explicit ``--gpus`` (a job-wide total). Otherwise,
        if a per-node count is known (``--gpus-per-node`` or
        ``--gres=gpu:N``), multiplies it by ``nodes`` (defaulting to a
        single node when ``--nodes`` is absent). Returns ``None`` when
        no GPU directive was given.
        """
        if self.gpus is not None:
            return self.gpus
        if self.gpus_per_node is not None:
            return self.gpus_per_node * (self.nodes if self.nodes else 1)
        return None


# -----------------------------------------------------------------
# Command parsing
# -----------------------------------------------------------------


@dataclass(frozen=True)
class ParsedCommand:
    """
    A single resolved command invocation extracted from the script
    body.

    ``binary`` is the basename of the resolved executable (wrappers
    and leading ``VAR=value`` assignments peeled off); ``argv`` is the
    full token list of the command segment as parsed; ``raw`` is the
    original text of the segment.
    """

    binary: str
    argv: tuple[str, ...]
    raw: str


# Command-position wrappers whose *next* command-position token is the
# real executable. ``env`` additionally skips leading ``VAR=value``
# pairs; that is handled in the resolver, not here.
_WRAPPERS: frozenset[str] = frozenset(
    {
        "srun",
        "mpirun",
        "mpiexec",
        "shifter",
        "singularity",
        "podman",
        "env",
        "sudo",
        "nohup",
        "time",
        "exec",
        "xargs",
        "nice",
        "ionice",
        "stdbuf",
        "setsid",
        "timeout",
        "command",
        "builtin",
        "then",
        "do",
        "else",
        "elif",
        "while",
        "until",
        "if",
        "!",
    }
)

# Wrappers that take a ``-c <script>`` (or ``--command``) argument
# whose value is itself a shell script to recurse into.
_DASH_C_WRAPPERS: frozenset[str] = frozenset({"bash", "sh", "zsh", "dash", "ksh"})

# Interpreters that, when on the receiving end of a pipe, turn the
# upstream producer into a code-execution sink.
_SHELL_SINKS: frozenset[str] = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "dash",
        "ksh",
        "python",
        "python2",
        "python3",
        "perl",
        "ruby",
        "node",
    }
)

_NET_BINARIES: frozenset[str] = frozenset(
    {"curl", "wget", "scp", "sftp", "rsync", "nc", "ncat", "ftp", "tftp"}
)

# Top-level shell control operators we split commands on. Ordered
# longest-first so ``&&`` is matched before ``&`` and ``|&``/``||``
# before ``|``.
_OPERATORS: tuple[str, ...] = ("&&", "||", "|&", ";;", ";", "|", "&", "\n")


def _split_top_level(text: str) -> list[str]:
    """
    Split ``text`` into command segments on top-level shell control
    operators, respecting single/double quotes, ``$( ... )``
    substitution, ``${ ... }`` expansion, and backticks.

    Nested operators *inside* a substitution stay with their segment;
    the substitution body is recursed into separately by the caller.
    Empty segments are dropped.
    """
    segments: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(text)
    quote: str | None = None
    paren_depth = 0  # $( ... ) and ${ ... }
    backtick = False

    while i < n:
        ch = text[i]

        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue

        if backtick:
            buf.append(ch)
            if ch == "`":
                backtick = False
            i += 1
            continue

        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue

        if ch == "`":
            backtick = True
            buf.append(ch)
            i += 1
            continue

        if ch == "$" and i + 1 < n and text[i + 1] in ("(", "{"):
            paren_depth += 1
            buf.append(ch)
            buf.append(text[i + 1])
            i += 2
            continue

        if paren_depth > 0:
            if ch in (")", "}"):
                paren_depth -= 1
            buf.append(ch)
            i += 1
            continue

        # Try to match a control operator at this position.
        matched = None
        for op in _OPERATORS:
            if text.startswith(op, i):
                matched = op
                break
        if matched is not None:
            segments.append("".join(buf))
            buf = []
            i += len(matched)
            continue

        buf.append(ch)
        i += 1

    segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


# ``$( ... )`` and backtick substitution bodies, for recursive parsing.
_SUBST_PATTERN = re.compile(r"\$\((?P<body>.*?)\)|`(?P<body2>[^`]*)`", re.DOTALL)


def _extract_substitutions(text: str) -> list[str]:
    """Return the inner text of every ``$( ... )`` / backtick span."""
    out: list[str] = []
    for m in _SUBST_PATTERN.finditer(text):
        body = m.group("body")
        if body is None:
            body = m.group("body2")
        if body and body.strip():
            out.append(body.strip())
    return out


def _tokenize(segment: str) -> list[str]:
    """
    Tokenize a single command segment.

    Uses ``shlex`` in POSIX mode but never raises: an unbalanced quote
    (common when a segment is a fragment of a substitution) falls back
    to a whitespace split so the gate still sees the tokens.
    """
    lex = shlex.shlex(segment, posix=True, punctuation_chars=False)
    lex.whitespace_split = True
    lex.commenters = ""  # comments are stripped upstream; '#' is literal here
    try:
        return list(lex)
    except ValueError:
        return segment.split()


_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _resolve_binary(tokens: list[str]) -> str:
    """
    Resolve the executable from a command's token list: peel leading
    ``VAR=value`` assignments, then unwrap known wrappers, returning
    the basename of the first real executable token.

    Returns ``""`` when the segment is only assignments / wrappers.
    """
    idx = 0
    seen_wrappers = 0
    while idx < len(tokens):
        tok = tokens[idx]
        # Leading environment assignments: FOO=bar cmd ...
        if _ASSIGNMENT_RE.match(tok):
            idx += 1
            continue
        base = _basename(tok)
        if base in _WRAPPERS and seen_wrappers < 8:
            seen_wrappers += 1
            idx += 1
            # ``env`` may be followed by more VAR=value assignments,
            # handled by the loop's assignment branch.
            continue
        return base
    return ""


def _basename(token: str) -> str:
    """
    Basename of a command token, stripping a leading ``./`` / path and
    surrounding quotes. ``/usr/bin/xmrig`` -> ``xmrig``,
    ``./miner`` -> ``miner``.
    """
    t = token.strip().strip("'\"")
    # Strip shell substitution sigils so ``$(curl`` -> ``curl`` and
    # ``bash)`` -> ``bash`` when a substitution body is recursed into.
    t = t.lstrip("$(`").rstrip(")`")
    if not t:
        return ""
    # Drop everything up to the last '/'.
    if "/" in t:
        t = t.rsplit("/", 1)[-1]
    return t


def _dash_c_body(tokens: list[str]) -> str | None:
    """
    If ``tokens`` is a ``bash``/``sh`` ``-c <script>`` invocation (or
    ``eval``), return the inner script body to recurse into.
    """
    if not tokens:
        return None
    # Strip leading assignments / wrappers to find the real head.
    head_idx = 0
    while head_idx < len(tokens) and _ASSIGNMENT_RE.match(tokens[head_idx]):
        head_idx += 1
    if head_idx >= len(tokens):
        return None
    head = _basename(tokens[head_idx])
    rest = tokens[head_idx + 1 :]

    if head == "eval":
        # eval joins its remaining args into one command string.
        return " ".join(rest) if rest else None

    if head in _DASH_C_WRAPPERS:
        for j, tok in enumerate(rest):
            if tok in ("-c", "--command") and j + 1 < len(rest):
                return rest[j + 1]
    return None


# -----------------------------------------------------------------
# Path + network extraction
# -----------------------------------------------------------------


def _looks_like_path(token: str) -> bool:
    t = token.strip().strip("'\"")
    if not t or t.startswith("-"):
        return False
    if t.startswith(("/", "~", "./", "../")):
        return True
    if t.startswith("$") and "/" in t:  # $HOME/.ssh, $VISTA_OUT/...
        return True
    if "/" in t and "://" not in t:
        return True
    return False


_URL_RE = re.compile(r"\b(?:https?|ftp|ftps|sftp)://([^/\s'\"]+)", re.IGNORECASE)
_SCP_RE = re.compile(r"\b(?:[A-Za-z0-9_.\-]+@)?([A-Za-z0-9_.\-]+):(?:/|~)")


def _extract_network_targets(command: ParsedCommand) -> list[str]:
    """
    Extract host targets from a network-tool command. Returns the
    bare hostnames referenced as ``http(s)://host/...`` URLs or
    ``user@host:path`` SCP/rsync forms.
    """
    if command.binary not in _NET_BINARIES:
        return []
    hosts: list[str] = []
    for tok in command.argv:
        for m in _URL_RE.finditer(tok):
            host = m.group(1)
            # Strip an optional ``user:pass@`` and ``:port`` suffix.
            if "@" in host:
                host = host.rsplit("@", 1)[-1]
            host = host.split(":", 1)[0]
            if host:
                hosts.append(host)
        if "://" not in tok:
            scp = _SCP_RE.search(tok)
            if scp:
                hosts.append(scp.group(1))
    return hosts


# -----------------------------------------------------------------
# Multi-stage payload detection
# -----------------------------------------------------------------


# download-then-exec, decode-pipe-to-shell, eval-of-substitution.
MULTISTAGE_DOWNLOAD_EXEC = "download_exec"
MULTISTAGE_DECODE_PIPE_SHELL = "decode_pipe_shell"
MULTISTAGE_EVAL_SUBSTITUTION = "eval_substitution"

_DECODE_PIPE_RE = re.compile(
    r"(?:base64\s+(?:-d|--decode)|base64\s+-{0,2}d\b|xxd\s+-r|openssl\s+enc\s+[^|]*-d|uudecode)"
    r"[^|]*\|\s*(?:bash|sh|zsh|dash|python[0-9.]*|perl)\b",
    re.IGNORECASE,
)
_EVAL_SUBST_RE = re.compile(
    r"\beval\b[^\n]*(?:\$\(|`)[^\n]*(?:curl|wget|fetch)\b",
    re.IGNORECASE,
)


def _detect_multistage(raw: str, commands: list[ParsedCommand]) -> frozenset[str]:
    """
    Flag the three SciAgentBench B5.1 delivery structures.

    Uses targeted regexes for the pipe/eval shapes (which depend on
    operator adjacency the command split discards) and the structured
    command list for download-then-exec.
    """
    flags: set[str] = set()
    normalized = re.sub(r"\s+", " ", raw)

    if _DECODE_PIPE_RE.search(normalized):
        flags.add(MULTISTAGE_DECODE_PIPE_SHELL)
    if _EVAL_SUBST_RE.search(normalized):
        flags.add(MULTISTAGE_EVAL_SUBSTITUTION)

    has_download = any(c.binary in {"curl", "wget"} for c in commands)
    runs_local = any(_runs_local_binary(c) for c in commands)
    if has_download and runs_local:
        flags.add(MULTISTAGE_DOWNLOAD_EXEC)

    return frozenset(flags)


def _runs_local_binary(command: ParsedCommand) -> bool:
    """
    True when the command executes a freshly-written local artifact --
    a ``./relative`` or ``/tmp`` path, or ``chmod +x`` of one. This is
    the second half of the download-then-exec pattern.
    """
    # These literals detect temp-dir execution patterns; they do not create
    # temp files, so bandit's hardcoded-tmp checks (B108) are false positives.
    for tok in command.argv:
        bare = tok.strip().strip("'\"")
        if (
            bare.startswith("./")
            or bare.startswith("/tmp/")  # nosec B108
            or bare.startswith("/dev/shm/")  # nosec B108
        ):
            return True
    return False


# -----------------------------------------------------------------
# SlurmScript -- the parse result
# -----------------------------------------------------------------


@dataclass(frozen=True)
class SlurmScript:
    """
    Structured parse of a resolved SLURM job script.

    This is the parser's sole output and the gate's sole input. It is
    re-exported from :mod:`vista_backend.vistaguard.gates.g5_hpc` so
    callers can import it from either module.
    """

    raw: str
    directives: SbatchDirectives
    commands: tuple[ParsedCommand, ...]
    file_paths: tuple[str, ...]
    network_targets: tuple[str, ...]
    multistage_flags: frozenset[str]

    @property
    def binaries(self) -> tuple[str, ...]:
        """The resolved binary basenames of every command, in order."""
        return tuple(c.binary for c in self.commands if c.binary)


# -----------------------------------------------------------------
# Time parsing
# -----------------------------------------------------------------


def parse_slurm_time(value: str) -> int | None:
    """
    Convert a SLURM ``--time`` value to whole seconds.

    Accepts the documented SLURM forms: ``minutes``, ``minutes:seconds``,
    ``hours:minutes:seconds``, ``days-hours``, ``days-hours:minutes``,
    ``days-hours:minutes:seconds``. Returns ``None`` on anything it
    can't parse (e.g. ``UNLIMITED``) -- the gate treats an unparseable
    limit as "no ceiling information," not as zero.
    """
    s = value.strip()
    if not s:
        return None
    days = 0
    has_dash = "-" in s
    if has_dash:
        day_part, _, s = s.partition("-")
        try:
            days = int(day_part)
        except ValueError:
            return None

    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None

    if has_dash:
        # days-hours[:minutes[:seconds]]
        hours = nums[0] if len(nums) >= 1 else 0
        minutes = nums[1] if len(nums) >= 2 else 0
        seconds = nums[2] if len(nums) >= 3 else 0
    else:
        if len(nums) == 1:  # minutes
            hours, minutes, seconds = 0, nums[0], 0
        elif len(nums) == 2:  # minutes:seconds
            hours, minutes, seconds = 0, nums[0], nums[1]
        elif len(nums) == 3:  # hours:minutes:seconds
            hours, minutes, seconds = nums[0], nums[1], nums[2]
        else:
            return None

    return days * 86400 + hours * 3600 + minutes * 60 + seconds


# -----------------------------------------------------------------
# Directive line parsing
# -----------------------------------------------------------------


def _parse_sbatch_line(line: str) -> list[tuple[str, str]]:
    """
    Parse the option(s) on one ``#SBATCH`` line into ``(key, value)``
    pairs with long-name keys. Handles ``--key=value``, ``--key value``,
    ``-k value``, ``-kvalue``, and bare boolean flags.
    """
    remainder = line[len(_SBATCH_PREFIX) :].strip()
    if not remainder:
        return []
    # Drop a trailing inline comment.
    remainder = remainder.split("#", 1)[0].strip()
    try:
        tokens = shlex.split(remainder)
    except ValueError:
        tokens = remainder.split()

    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            body = tok[2:]
            if "=" in body:
                key, _, val = body.partition("=")
                pairs.append((key, val))
                i += 1
            elif body in _BOOLEAN_LONG_FLAGS:
                pairs.append((body, ""))
                i += 1
            elif i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                pairs.append((body, tokens[i + 1]))
                i += 2
            else:
                pairs.append((body, ""))
                i += 1
        elif tok.startswith("-") and len(tok) >= 2:
            flag = tok[1]
            long_name = _SHORT_FLAG_TO_LONG.get(flag, flag)
            inline = tok[2:]
            if inline:  # -N4
                pairs.append((long_name, inline))
                i += 1
            elif i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                pairs.append((long_name, tokens[i + 1]))
                i += 2
            else:
                pairs.append((long_name, ""))
                i += 1
        else:
            i += 1
    return pairs


def _parse_dependencies(value: str) -> tuple[DependencyRef, ...]:
    """
    Parse a ``--dependency`` value into :class:`DependencyRef` clauses.

    ``afterok:1:2,afterany:9`` -> two refs. ``singleton`` -> one
    id-less ref. Separators may be ``,`` or ``?`` per SLURM syntax.
    """
    refs: list[DependencyRef] = []
    for clause in re.split(r"[,?]", value):
        clause = clause.strip()
        if not clause:
            continue
        parts = clause.split(":")
        kind = parts[0]
        job_ids = tuple(p for p in parts[1:] if p)
        refs.append(DependencyRef(kind=kind, job_ids=job_ids))
    return tuple(refs)


def _build_directives(pairs: list[tuple[str, str]]) -> SbatchDirectives:
    """Fold parsed ``(key, value)`` pairs into typed directive fields."""
    nodes = ntasks = cpus = gpus = gpus_per_node = None
    time_seconds = None
    account = partition = qos = None
    deps: tuple[DependencyRef, ...] = ()

    for key, val in pairs:
        k = key.lower()
        if k == "nodes":
            nodes = _safe_int(val, nodes)
        elif k == "ntasks" or k == "ntasks-per-node":
            ntasks = _safe_int(val, ntasks)
        elif k == "cpus-per-task":
            cpus = _safe_int(val, cpus)
        elif k == "time":
            parsed = parse_slurm_time(val)
            if parsed is not None:
                time_seconds = parsed
        elif k == "gpus":
            gpus = _safe_int(val, gpus)
        elif k == "gpus-per-node":
            gpus_per_node = _safe_int(val, gpus_per_node)
        elif k == "gres":
            g = _gres_gpu_count(val)
            if g is not None:
                gpus_per_node = g
        elif k == "account":
            account = val or account
        elif k == "partition":
            partition = val or partition
        elif k == "qos":
            qos = val or qos
        elif k == "dependency":
            if val:
                deps = _parse_dependencies(val)

    return SbatchDirectives(
        nodes=nodes,
        time_seconds=time_seconds,
        ntasks=ntasks,
        cpus_per_task=cpus,
        gpus=gpus,
        gpus_per_node=gpus_per_node,
        account=account,
        partition=partition,
        qos=qos,
        dependencies=deps,
        raw=tuple(pairs),
    )


def _safe_int(val: str, default: int | None) -> int | None:
    try:
        return int(val)
    except TypeError, ValueError:
        return default


def _gres_gpu_count(val: str) -> int | None:
    """
    Extract the GPU count from a ``--gres`` value. ``gpu:4`` -> 4,
    ``gpu:a100:8`` -> 8, ``gpu`` -> 1.
    """
    parts = val.split(",")
    for part in parts:
        sub = part.split(":")
        if sub and sub[0].lower() == "gpu":
            if len(sub) == 1:
                return 1
            # Last numeric component is the count.
            for piece in reversed(sub[1:]):
                n = _safe_int(piece, None)
                if n is not None:
                    return n
            return 1
    return None


# -----------------------------------------------------------------
# Comment / shebang stripping
# -----------------------------------------------------------------


def _strip_for_body(raw: str) -> tuple[list[str], list[str]]:
    """
    Split ``raw`` into (sbatch_directive_lines, body_lines).

    ``#SBATCH`` lines go to the first list; full-line comments and the
    shebang are dropped; everything else is body. Inline ``#`` comments
    are left to the tokenizer (which treats ``#`` as literal) -- in
    practice job-script bodies rarely use trailing comments, and
    keeping them avoids mis-stripping a ``#`` inside an unquoted URL
    fragment.
    """
    sbatch: list[str] = []
    body: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(_SBATCH_PREFIX):
            sbatch.append(stripped)
        elif stripped.startswith("#"):
            continue  # shebang or comment
        else:
            body.append(line)
    return sbatch, body


# -----------------------------------------------------------------
# Top-level parse entry point
# -----------------------------------------------------------------


_MAX_RECURSION = 6


def parse_slurm_script(raw: str) -> SlurmScript:
    """
    Parse a resolved SLURM job script into a :class:`SlurmScript`.

    Never raises on malformed input. ``raw`` is the literal script
    text (the inlined ``job.slurm`` body the MCP dispatcher submits).
    """
    if not isinstance(raw, str):
        raw = str(raw)

    sbatch_lines, body_lines = _strip_for_body(raw)

    # ----- directives -------------------------------------------------
    pairs: list[tuple[str, str]] = []
    for line in sbatch_lines:
        pairs.extend(_parse_sbatch_line(line))
    directives = _build_directives(pairs)

    # ----- commands (with bounded substitution recursion) -------------
    commands: list[ParsedCommand] = []
    body_text = "\n".join(body_lines)
    _walk_commands(body_text, commands, depth=0)

    # ----- paths + network -------------------------------------------
    file_paths: list[str] = []
    network_targets: list[str] = []
    seen_paths: set[str] = set()
    seen_hosts: set[str] = set()
    for cmd in commands:
        for tok in cmd.argv:
            if _looks_like_path(tok):
                norm = tok.strip().strip("'\"")
                if norm not in seen_paths:
                    seen_paths.add(norm)
                    file_paths.append(norm)
        for host in _extract_network_targets(cmd):
            if host not in seen_hosts:
                seen_hosts.add(host)
                network_targets.append(host)

    multistage = _detect_multistage(raw, commands)

    return SlurmScript(
        raw=raw,
        directives=directives,
        commands=tuple(commands),
        file_paths=tuple(file_paths),
        network_targets=tuple(network_targets),
        multistage_flags=multistage,
    )


def _walk_commands(text: str, out: list[ParsedCommand], depth: int) -> None:
    """
    Recursively split ``text`` into commands, appending each as a
    :class:`ParsedCommand` to ``out``. Recurses into ``bash -c`` /
    ``eval`` bodies and ``$( ... )`` / backtick substitutions, bounded
    by :data:`_MAX_RECURSION`.
    """
    if depth > _MAX_RECURSION or not text.strip():
        return
    for segment in _split_top_level(text):
        tokens = _tokenize(segment)
        if not tokens:
            continue
        binary = _resolve_binary(tokens)
        out.append(ParsedCommand(binary=binary, argv=tuple(tokens), raw=segment))

        # Recurse into an embedded shell-script body.
        inner = _dash_c_body(tokens)
        if inner:
            _walk_commands(inner, out, depth + 1)

        # Recurse into command substitutions in the segment.
        for sub in _extract_substitutions(segment):
            _walk_commands(sub, out, depth + 1)


__all__ = [
    "DependencyRef",
    "MULTISTAGE_DECODE_PIPE_SHELL",
    "MULTISTAGE_DOWNLOAD_EXEC",
    "MULTISTAGE_EVAL_SUBSTITUTION",
    "ParsedCommand",
    "SbatchDirectives",
    "SlurmScript",
    "parse_slurm_script",
    "parse_slurm_time",
]
