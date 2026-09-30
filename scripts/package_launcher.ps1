# Start VISTA from an unpacked Windows package. Installed at the package root
# as `vista.ps1`, with `vista.cmd` beside it for cmd and double-clicking. The
# Windows counterpart of package_launcher.sh, step for step.
#
#   .\vista.ps1            first-run setup if needed, then start
#   .\vista.ps1 --help     show this
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
# The parts are the members payload\parts.txt lists, one per corpus, rather than
# the tar's top-level folders: an upgraded state directory already has
# vista-data and knowledge-bases, and a corpus a newer package adds
# (vista-data/ai-safety) must still be installed. An installed member is never
# replaced or removed.
$payloadTar = "$PACKAGE\payload\payload.tar"
$missing = @()
if (Test-Path $payloadTar) {
  $partsFile = "$PACKAGE\payload\parts.txt"
  if (-not (Test-Path $partsFile)) {
    Die "this package has no payload\parts.txt; rebuild it with build_local_package.sh."
  }
  $missing = @(Get-Content -Encoding UTF8 $partsFile |
    Where-Object { $_.Trim() -and -not (Test-Path "$STATE\$($_.Trim())") } |
    ForEach-Object { $_.Trim() })
}
if ($missing.Count -gt 0) {
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
    & $MSB load -i $imageTar -t $env:VISTA_DEV_MCP_IMAGE *>> "$LOGS\setup.log"
    $loaded = ($LASTEXITCODE -eq 0)
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
function Start-VistaService([string]$Name, [string]$Command) {
  $proc = Start-Process -FilePath 'cmd.exe' `
    -ArgumentList '/d', '/s', '/c', "`"$Command > `"$LOGS\$Name.log`" 2>&1`"" `
    -WorkingDirectory $PACKAGE -WindowStyle Hidden -PassThru
  $script:services += $proc
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
  Start-VistaService 'mcp' ("`"$PACKAGE\app\mcp_servers\vista_mcp_server\.venv\Scripts\vista-mcp-server.exe`" " +
    "--transport=http --port $MCP_PORT")
  Wait-For $env:VISTA_MCP_URL 180 "$LOGS\mcp.log" 'the MCP server'

  if ($FIRST_RUN) {
    Log 'First run: preparing the database and corpus (this takes a minute)...'
  }
  Start-VistaService 'backend' "`"$PACKAGE\app\backend\.venv\Scripts\vista-backend.exe`""
  Wait-For "$env:VISTA_BACKEND_URL/openapi.json" 600 "$LOGS\backend.log" 'the backend'

  $env:PORT = $UI_PORT
  $env:HOSTNAME = '127.0.0.1'
  Start-VistaService 'ui' "`"$PACKAGE\node\node.exe`" `"$PACKAGE\app\ui\server.js`""
  Wait-For "http://127.0.0.1:$UI_PORT/" 120 "$LOGS\ui.log" 'the web interface'

  Log ''
  Log "VISTA is running at http://localhost:$UI_PORT"
  Log 'Press Ctrl-C to stop.'

  $services | Wait-Process
} finally {
  Stop-VistaServices
}
