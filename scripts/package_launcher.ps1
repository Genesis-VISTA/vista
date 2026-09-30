# Start VISTA from an unpacked Windows package. Installed at the package root
# as `vista.ps1`, behind `vista.cmd`, which is the entry point: run that, from
# cmd, PowerShell, or by double-clicking. It clears the mark of the web from
# this file and names an execution policy this script cannot run under, so run
# directly this script can fail with only PowerShell's own message. The
# Windows counterpart of package_launcher.sh, step for step.
#
#   .\vista.cmd            first-run setup if needed, then start and open the window
#   .\vista.cmd --help     show this
#
# Closing the VISTA window stops VISTA, as does Ctrl-C here or closing this
# console. VISTA is a desktop application: in a session that cannot show its
# window, such as SSH, it says why and stops.
#
# Everything the running system needs is inside this directory. Nothing is
# installed, downloaded, or configured on the machine: the only thing outside
# the package is the state directory, which holds the database, uploads, the
# corpus, and the sandbox image store.
#
# Environment:
#   VISTA_HOME          state directory (default: ~\.vista)
#   VISTA_UI_PORT       default 3000
#   VISTA_MCP_PORT      default 8000
#   VISTA_BACKEND_PORT  default 8001
#
#   VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN
#   VISTA_MCP_ODO_GLOBUS_HTTPS_REFRESH_TOKEN
#   VISTA_MCP_FRONTIER_GLOBUS_REFRESH_TOKEN
#   VISTA_MCP_FRONTIER_GLOBUS_HTTPS_REFRESH_TOKEN
#                       Deployment-wide Globus credentials for file transfer to
#                       Odo and Frontier, for a hosted install where one
#                       identity serves everyone. On a desktop, connect Globus
#                       in the VISTA user settings instead: nothing to export,
#                       and nothing to set up in this terminal.

$ErrorActionPreference = 'Stop'

$PACKAGE = $PSScriptRoot
$STATE = if ($env:VISTA_HOME) { $env:VISTA_HOME } else { Join-Path $HOME '.vista' }

$UI_PORT = if ($env:VISTA_UI_PORT) { $env:VISTA_UI_PORT } else { '3000' }
$MCP_PORT = if ($env:VISTA_MCP_PORT) { $env:VISTA_MCP_PORT } else { '8000' }
$BACKEND_PORT = if ($env:VISTA_BACKEND_PORT) { $env:VISTA_BACKEND_PORT } else { '8001' }

$VERSION = 'unknown'
if (Test-Path "$PACKAGE\VERSION") { $VERSION = (Get-Content "$PACKAGE\VERSION" -TotalCount 1).Trim() }

$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

function Die([string]$Message) {
  [Console]::Error.WriteLine("error: $Message")
  exit 1
}
function Log([string]$Message) { Write-Host $Message }

if ($args.Count -gt 0 -and ($args[0] -eq '--help' -or $args[0] -eq '-h')) {
  foreach ($line in Get-Content $PSCommandPath) {
    if ($line -notmatch '^#') { break }
    Write-Host ($line -replace '^# ?', '')
  }
  exit 0
}
if ($args.Count -gt 0) { Die "unexpected argument: $($args[0]) (try --help)" }

# --- platform guard ----------------------------------------------------------

# Checked before anything else, and without the bundled interpreter: on the
# wrong platform that interpreter is exactly what cannot run.
$manifest = Get-Content "$PACKAGE\manifest.json" -Raw | ConvertFrom-Json
$BUILT_OS = $manifest.target.os
$BUILT_ARCH = $manifest.target.arch
$HOST_OS = 'windows'
switch ($env:PROCESSOR_ARCHITECTURE) {
  'AMD64' { $HOST_ARCH = 'x86_64' }
  'ARM64' { $HOST_ARCH = 'arm64' }
  default { $HOST_ARCH = $env:PROCESSOR_ARCHITECTURE }
}

if ($BUILT_OS -and ($HOST_OS -ne $BUILT_OS -or $HOST_ARCH -ne $BUILT_ARCH)) {
  Die ("this package was built for $BUILT_OS-$BUILT_ARCH, but this machine is " +
    "$HOST_OS-$HOST_ARCH. Interpreters and compiled libraries inside it cannot run here; " +
    "use the $HOST_OS-$HOST_ARCH build.")
}

# --- path length -------------------------------------------------------------

# Windows caps a full path at 260 characters unless long paths are enabled, and
# the package's deepest files sit far enough down that a deep unpack location
# pushes them past it. What fails then is an import deep inside a service, long
# after startup said everything was fine, so it is caught here instead. The
# build records the package's longest path relative to its root; this adds
# where it was unpacked.
$longPaths = (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
    -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled
$longest = [int]$manifest.target.longest_relative_path
$deepest = $PACKAGE.Length + 1 + $longest
if ($longPaths -ne 1 -and $deepest -ge 260) {
  Die ("this package is unpacked too deep for Windows' path-length limit:`n" +
    "    $PACKAGE`n" +
    "  puts its deepest file at $deepest characters, and Windows allows 259 unless`n" +
    "  long paths are enabled. Move the folder somewhere shorter, such as C:\vista,`n" +
    "  or have an administrator enable long paths (LongPathsEnabled under`n" +
    "  HKLM\SYSTEM\CurrentControlSet\Control\FileSystem), then re-run.")
}

# The same for the state directory, which the corpus is extracted into on first
# run. Its deepest files are corpus PDFs with long names; `tar` can write them
# past the limit, but the backend then cannot open them and fails at startup.
$longestState = [int]$manifest.target.longest_state_path
$deepestState = $STATE.Length + 1 + $longestState
if ($longPaths -ne 1 -and $deepestState -ge 260) {
  Die ("the state directory is too deep for Windows' path-length limit:`n" +
    "    $STATE`n" +
    "  puts its deepest file at $deepestState characters, and Windows allows 259`n" +
    "  unless long paths are enabled. Set VISTA_HOME to a shorter folder, such as`n" +
    "  C:\vista-data, or have an administrator enable long paths, then re-run.")
}

# --- hardware virtualisation -------------------------------------------------

# The code-execution sandbox runs each agent session in a microVM, which on
# Windows needs a working Windows Hypervisor Platform. The reason to refuse,
# rather than start without it, is the same as on Linux without KVM: the
# sandbox server is part of every agent's toolset and spawns a microVM at
# startup, so without it every agent tool call fails -- retrieval included.
#
# Asked of the bundled msb itself (`msb doctor`, which needs no administrator
# rights), not of Windows' optional-feature list. The two disagree: a host can
# list the HypervisorPlatform feature as disabled while the hypervisor msb
# uses is available and its sandboxes run -- measured on the Windows build
# host, where refusing on the feature list would have stopped a working
# install. Not ready means msb doctor exits non-zero or does not report the
# host ready; its own output, which names the fix, is shown. There is no way
# to start without it: VISTA always needs a microVM.
$MSB = "$PACKAGE\app\mcp_servers\dev_mcp_server\.venv\Lib\site-packages\microsandbox\_bundled\bin\msb.exe"
if (-not (Test-Path $MSB)) { Die "the package has no sandbox runtime at $MSB" }
& {
  $ErrorActionPreference = 'Continue'
  $doctor = (& $MSB doctor 2>&1 | ForEach-Object { "$_" }) -join "`n"
  $doctorExit = $LASTEXITCODE
  $ErrorActionPreference = 'Stop'
  if ($doctorExit -ne 0 -or $doctor -notmatch 'Host setup is ready') {
    Die ("the code-execution sandbox cannot run on this machine. Its own check says:`n`n" +
      "$doctor`n`n" +
      "  On Windows this usually means Windows Hypervisor Platform is not available.`n" +
      "  Turning it on needs administrator rights and a restart; ``msb doctor --fix``,`n" +
      "  run as administrator, can do it.`n`n" +
      "  Every agent tool call depends on it, not just running code: the sandbox`n" +
      "  server is started as part of the agent's toolset, so without it retrieval`n" +
      "  fails too.")
  }
}

# --- port preflight ----------------------------------------------------------

# Reported before any service starts. Otherwise the conflict surfaces as a
# health poll that never answers, which looks like a hang and says nothing
# about the cause.
function Test-PortInUse([int]$Port) {
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $connect = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
    return ($connect.AsyncWaitHandle.WaitOne(500) -and $client.Connected)
  } catch {
    return $false
  } finally {
    $client.Close()
  }
}

$conflicts = @()
foreach ($entry in @(@($UI_PORT, 'the web interface'), @($MCP_PORT, 'the MCP server'),
    @($BACKEND_PORT, 'the backend'))) {
  if (Test-PortInUse ([int]$entry[0])) { $conflicts += "port $($entry[0]) ($($entry[1])) is already in use" }
}
if ($conflicts.Count -gt 0) {
  [Console]::Error.WriteLine('error: VISTA cannot start:')
  foreach ($conflict in $conflicts) { [Console]::Error.WriteLine("  - $conflict") }
  [Console]::Error.WriteLine('Stop whatever is using it, or set VISTA_UI_PORT / VISTA_MCP_PORT /')
  [Console]::Error.WriteLine('VISTA_BACKEND_PORT to different ports.')
  exit 1
}

# --- state path length -------------------------------------------------------

# On macOS and Linux the sandbox store's path feeds a Unix socket path with a
# hard kernel limit, and package_launcher.sh refuses a store past 51
# characters. Whether msb on Windows is bound by any such budget is still open
# (windows-support task 6.8), so nothing is enforced here until it is known.
$MSB_STORE = Join-Path $STATE 'microsandbox'

# --- relocation --------------------------------------------------------------

# Each environment's pyvenv.cfg names the interpreter it runs on by absolute
# path. CPython resolves a relative `home` against the working directory rather
# than the file, so the build cannot make it relative the way it does on macOS
# and Linux; it is pointed at this copy of the interpreter here instead, which
# is what lets the package be moved. Rewritten only when it differs.
$pythonDir = Get-ChildItem -Directory "$PACKAGE\python" -Filter 'cpython-*' | Select-Object -First 1
if (-not $pythonDir) { Die "no bundled interpreter under $PACKAGE\python" }
foreach ($project in 'backend', 'mcp_servers\vista_mcp_server', 'mcp_servers\dev_mcp_server') {
  $cfg = "$PACKAGE\app\$project\.venv\pyvenv.cfg"
  $text = [System.IO.File]::ReadAllText($cfg)
  $wanted = "home = $($pythonDir.FullName)"
  $updated = [regex]::Replace($text, '(?m)^home = .*$', $wanted.Replace('$', '$$'))
  if ($updated -ne $text) { [System.IO.File]::WriteAllText($cfg, $updated, $Utf8NoBom) }
}

# --- environment -------------------------------------------------------------

# Every path is derived from this script's own location, so the package works
# from any working directory and after being moved. The reasons for each are in
# package_launcher.sh.
$env:PATH = "$PACKAGE\bin;$PACKAGE\node;$env:PATH"
# The bundled `uv` is a runtime dependency: the backend spawns the sandbox MCP
# server with `uv run` on every agent session. UV_NO_SYNC stops it deciding a
# relocated environment is stale and attempting a reinstall mid-session.
$env:UV_NO_SYNC = '1'
# Python 3.14 on Windows defaults text files to cp1252. VISTA's own code passes
# encoding="utf-8" everywhere; this is the backstop for third-party code.
$env:PYTHONUTF8 = '1'
$env:VISTA_VERSION = $VERSION
$env:VISTA_DATA_DIR = $STATE
$env:VISTA_MCP_SERVERS_PATH = "$PACKAGE\app\mcp_servers"
$env:VISTA_MCP_LOCAL_HPC_JOBS_DIR = "$PACKAGE\app\hpc_jobs"
$env:VISTA_HPC_JOBS_DIR = "$PACKAGE\app\hpc_jobs"
$env:VISTA_BUILD_RAG_DIR = "$PACKAGE\app"
$env:VISTA_DATA_PAYLOAD_DIR = "$STATE\vista-data"
$env:VISTA_MCP_URL = "http://127.0.0.1:$MCP_PORT/mcp"
$env:VISTA_BACKEND_URL = "http://127.0.0.1:$BACKEND_PORT"
$env:VISTA_BACKEND_PORT = $BACKEND_PORT
$env:HF_HOME = "$STATE\huggingface"
# A cache miss must fail loudly rather than quietly reaching the network: the
# weights ship inside the package precisely so this works offline.
$env:HF_HUB_OFFLINE = '1'
$env:MSB_HOME = $MSB_STORE
# Use the sandbox image imported below, rather than building one -- which
# would need docker or podman, and a researcher has neither. A space, not '':
# assigning '' to an $env: variable deletes it, and dev_mcp_server then falls
# back to its bundled Dockerfile. It reads a blank value as "no Dockerfile".
$env:VISTA_DEV_MCP_DOCKERFILE = ' '
$env:VISTA_DEV_MCP_IMAGE = 'vista-sandbox:latest'
$LOGS = "$STATE\logs"

# --- window ------------------------------------------------------------------

# VISTA is a desktop application: there is no browser mode to fall back to, so a
# session that cannot show the window is refused here, before anything is
# started or extracted, with the reason. VISTA_NO_WINDOW=1 starts the services
# alone and is for the build's smoke test, which has no one to look at a window.
# The manifest writes it with /, which cmd can misread as a switch.
$WINDOW_EXE = if ($manifest.window) { $manifest.window.exe.Replace('/', '\') } else { '' }
if ($env:VISTA_NO_WINDOW -ne '1') {
  if (-not $WINDOW_EXE -or -not (Test-Path "$PACKAGE\$WINDOW_EXE")) {
    Die 'this package has no VISTA window; rebuild it with build_local_package.sh.'
  }
  # A window started over SSH, or from a service, lands on a desktop no one
  # is looking at, if on any.
  if ($env:SSH_CONNECTION -or $env:SSH_CLIENT) {
    Die 'cannot open the VISTA window: this is a remote shell session.'
  }
  if (-not [Environment]::UserInteractive) {
    Die 'cannot open the VISTA window: this session has no desktop (is it running as a service?).'
  }
}

# --- first-run setup ---------------------------------------------------------

New-Item -ItemType Directory -Force -Path $STATE, $LOGS | Out-Null

$FIRST_RUN = -not (Test-Path "$STATE\vista.db")

# The payload is extracted out of the package rather than read in place: the
# knowledge-base row records absolute paths, and the corpus is the
# researcher's to add to. Each part is skipped when already present, which is
# what makes a second run cheap and an upgrade a directory replacement.
#
# It ships as one tar because some corpus file names are long enough that,
# under the package folder, they would pass the 260-character limit. Extracted
# straight into the state directory they fit. The tar.exe Windows ships reads
# it; named by path so no other tar on PATH is picked up.
$missing = @('vista-data', 'knowledge-bases', 'huggingface') | Where-Object { -not (Test-Path "$STATE\$_") }
$payloadTar = "$PACKAGE\payload\payload.tar"
if ($missing.Count -gt 0 -and (Test-Path $payloadTar)) {
  Log "First run: installing $($missing -join ' ')..."
  & (Join-Path $env:SystemRoot 'System32\tar.exe') -xf $payloadTar -C $STATE @missing
  if ($LASTEXITCODE -ne 0) { Die "could not extract the payload into $STATE" }
}


# Imports the sandbox image from the payload -- the only one the package ships.
# The `image inspect` guard is what makes a second run cheap.
$imageTar = "$PACKAGE\payload\sandbox-image.tar"
if ((Test-Path $MSB) -and (Test-Path $imageTar)) {
  $ErrorActionPreference = 'Continue'
  & $MSB image inspect --format=json $env:VISTA_DEV_MCP_IMAGE *> $null
  $present = ($LASTEXITCODE -eq 0)
  if (-not $present) {
    Log 'First run: importing the code-execution sandbox image...'
    # msb reports progress on stderr, in UTF-8. Redirected with `*>>`,
    # PowerShell 5.1 wraps every stderr line in a NativeCommandError record and
    # decodes it with the OEM code page, so a successful import reads as a
    # failure in setup.log. Stringified and decoded as UTF-8, the log holds
    # msb's own words.
    # Restored in finally: the code page belongs to the whole console, so a
    # failure or Ctrl-C here would otherwise leave the parent cmd on UTF-8.
    $consoleEncoding = [Console]::OutputEncoding
    try {
      [Console]::OutputEncoding = $Utf8NoBom
      & $MSB load -i $imageTar -t $env:VISTA_DEV_MCP_IMAGE 2>&1 | ForEach-Object { "$_" } |
        Out-File -Append -Encoding utf8 "$LOGS\setup.log"
      $loaded = ($LASTEXITCODE -eq 0)
    } finally {
      [Console]::OutputEncoding = $consoleEncoding
    }
  }
  $ErrorActionPreference = 'Stop'
  if (-not $present -and -not $loaded) { Die "could not import the sandbox image; see $LOGS\setup.log" }
}

# --- services ----------------------------------------------------------------

# Every service dies with this launcher, however it ends. The launcher joins a
# job object that kills its members when the job's last handle closes, and only
# this process holds that handle; every process started from here on -- the
# services, and whatever they start in turn, the sandbox servers and node
# included -- joins the job with it. So when this process goes, Windows closes
# the handle and ends them all, with no cleanup code of ours involved.
#
# The cleanup in Stop-VistaServices is not enough on its own. Ctrl-C in the
# `cmd` window running vista.cmd starts it, but cmd then ends the batch and
# takes this process with it partway through, leaving the services it had not
# yet reached running; closing the window or a crash runs no cleanup at all.
#
# If joining fails -- say this launcher runs inside a job that forbids it --
# the cleanup below still stops the services on an ordinary Ctrl-C.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class VistaJob {
    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimits {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimits {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    const int ExtendedLimitInformation = 9;
    const uint KillOnJobClose = 0x2000;

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimits info, uint length);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll")]
    static extern IntPtr GetCurrentProcess();

    // The job's handle is deliberately never closed: this process holding it
    // until it exits is what ties the job's lifetime to the launcher's.
    public static bool JoinKillOnCloseJob() {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) return false;
        var info = new ExtendedLimits();
        info.Basic.LimitFlags = KillOnJobClose;
        if (!SetInformationJobObject(job, ExtendedLimitInformation, ref info,
                (uint)Marshal.SizeOf(typeof(ExtendedLimits)))) return false;
        return AssignProcessToJobObject(job, GetCurrentProcess());
    }
}
'@
[void][VistaJob]::JoinKillOnCloseJob()

$services = @()

# Each service runs under cmd so stdout and stderr share one log file, which
# Start-Process cannot do itself. Each writes to its own file rather than
# interleaving here: the one thing a researcher needs from a successful start
# is the address.
#
# The whole command is wrapped in one more pair of quotes and run with /s.
# Without /s, a `cmd /c` line that starts with a quote and holds more than two
# loses its first and last quote characters -- which here are the ones around
# the executable and the log file -- and cmd refuses the line, so no service
# starts and no log is written.
#
# cmd's console is suppressed with CreateNoWindow rather than Start-Process
# -WindowStyle Hidden. The latter hands cmd a SW_HIDE show state, cmd passes it
# on to what it starts, and Windows applies it to that program's first
# top-level window -- so the VISTA window could open hidden. CreateNoWindow
# gives cmd a console with no window and sets no show state at all.
function Start-VistaService([string]$Name, [string]$Command) {
  $info = New-Object System.Diagnostics.ProcessStartInfo 'cmd.exe'
  $info.Arguments = "/d /s /c `"$Command > `"$LOGS\$Name.log`" 2>&1`""
  $info.WorkingDirectory = $PACKAGE
  $info.UseShellExecute = $false
  $info.CreateNoWindow = $true
  # Started directly, the process keeps its handle, so ExitCode can still be
  # read after it has gone -- the window's exit code says why it closed.
  $proc = [System.Diagnostics.Process]::Start($info)
  $script:services += $proc
  return $proc
}

# taskkill /T takes each service's whole tree -- cmd, the service, and the
# sandbox servers the backend spawned -- which is what `kill` on a process group
# does in package_launcher.sh.
function Stop-VistaServices {
  Log ''
  Log 'Stopping VISTA...'
  # 'Continue' because taskkill writes to stderr whenever part of a tree is
  # already gone, which 'Stop' turns into a throw -- and every service after
  # that one would be left running.
  $ErrorActionPreference = 'Continue'
  foreach ($proc in $services) {
    if (-not $proc.HasExited) { & taskkill.exe /F /T /PID $proc.Id 2>&1 | Out-Null }
  }
}

function Wait-For([string]$Url, [int]$Seconds, [string]$LogFile, [string]$What) {
  for ($i = 0; $i -lt $Seconds; $i++) {
    & curl.exe -s -o NUL -m 5 $Url
    if ($LASTEXITCODE -eq 0) { return }
    Start-Sleep -Seconds 1
  }
  [Console]::Error.WriteLine('')
  [Console]::Error.WriteLine("error: $What did not start within ${Seconds}s. Last lines of ${LogFile}:")
  Get-Content $LogFile -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { [Console]::Error.WriteLine($_) }
  exit 1
}

Log "VISTA $VERSION"
Log "Starting services (logs in $LOGS)..."

try {
  $null = Start-VistaService 'mcp' ("`"$PACKAGE\app\mcp_servers\vista_mcp_server\.venv\Scripts\vista-mcp-server.exe`" " +
    "--transport=http --port $MCP_PORT")
  Wait-For $env:VISTA_MCP_URL 180 "$LOGS\mcp.log" 'the MCP server'

  if ($FIRST_RUN) {
    Log 'First run: preparing the database and corpus (this takes a minute)...'
  }
  $null = Start-VistaService 'backend' "`"$PACKAGE\app\backend\.venv\Scripts\vista-backend.exe`""
  Wait-For "$env:VISTA_BACKEND_URL/openapi.json" 600 "$LOGS\backend.log" 'the backend'

  $env:PORT = $UI_PORT
  $env:HOSTNAME = '127.0.0.1'
  $null = Start-VistaService 'ui' "`"$PACKAGE\node\node.exe`" `"$PACKAGE\app\ui\server.js`""
  Wait-For "http://127.0.0.1:$UI_PORT/" 120 "$LOGS\ui.log" 'the web interface'

  # 127.0.0.1, not localhost: it is what the UI binds, and localhost can
  # resolve to ::1 first. It is also the origin the window's storage is kept
  # under, so it has to be the same on every run.
  $UI_URL = "http://127.0.0.1:$UI_PORT"

  if ($env:VISTA_NO_WINDOW -eq '1') {
    Log ''
    Log "VISTA is running at $UI_URL (no window: VISTA_NO_WINDOW is set)"
    Log 'Press Ctrl-C to stop.'
    $services | Wait-Process
    exit 0
  }

  # Under cmd like the services, so its output lands in window.log; cmd waits
  # for it and passes its exit code on.
  $window = Start-VistaService 'window' "`"$PACKAGE\$WINDOW_EXE`" --url=$UI_URL"
  Log ''
  Log "VISTA is open in its own window ($UI_URL)."
  Log 'Close the window, or press Ctrl-C here, to stop.'

  # The window closing or quitting ends the session; the finally below stops
  # the rest. 75 is what it exits with when another VISTA window already holds
  # the single-instance lock (EX_TEMPFAIL, set in electron/src/main.js).
  # Polled rather than a bare WaitForExit(): Ctrl-C cannot interrupt a blocking
  # .NET call in PowerShell 5.1, only the gap between two statements.
  while (-not $window.WaitForExit(500)) {}
  $windowStatus = $window.ExitCode
  if ($windowStatus -eq 75) {
    Die 'VISTA is already open in another window, which is showing the stack it started. Close that window first, or use it.'
  }
  if ($windowStatus -ne 0) {
    Die "the VISTA window stopped unexpectedly (exit $windowStatus); see $LOGS\window.log."
  }
} finally {
  Stop-VistaServices
}
