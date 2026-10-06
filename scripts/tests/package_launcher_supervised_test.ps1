# Hermetic tests for vista.ps1's supervised mode (package_launcher.ps1
# -Supervised -Progress jsonl), the Windows counterpart of
# package_launcher_supervised_test.sh: the same events in the same order, the
# same failure codes, and a stop on end-of-file that leaves no process behind.
#
#   pwsh -NoProfile -File scripts/tests/package_launcher_supervised_test.ps1
#
# On Windows it runs the launcher under Windows PowerShell, as VISTA.exe does,
# with the real cmd, taskkill and tar, and fake services compiled with the .NET
# Framework's csc. On macOS and Linux it runs the launcher under pwsh with
# stand-ins for those three, so the protocol is checked on every machine; what
# only Windows can show -- the job object, taskkill's tree -- needs Windows.

$ErrorActionPreference = 'Stop'

$OnWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
$Root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$Tmp = Join-Path ([IO.Path]::GetTempPath()) ("vista-ps1-test-" + [guid]::NewGuid().ToString('n').Substring(0, 8))
$Package = Join-Path $Tmp 'package'
$Tools = Join-Path $Tmp 'tools'
$State = Join-Path $Tmp 'state'
$Pids = Join-Path $Tmp 'service-pids'
$PortBase = 40000 + ($PID % 10000)

function New-Dir([string]$Path) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
function Write-Text([string]$Path, [string]$Text) {
  New-Dir (Split-Path -Parent $Path)
  [IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding $false))
}

$failures = 0
function Fail([string]$Message) {
  [Console]::Error.WriteLine("not ok: $Message")
  $script:failures++
}
function Pass([string]$Message) { Write-Host "  ok   $Message" }

# --- the fake package ----------------------------------------------------------

$McpExe = Join-Path $Package 'app/mcp_servers/vista_mcp_server/.venv/Scripts/vista-mcp-server.exe'
$BackendExe = Join-Path $Package 'app/backend/.venv/Scripts/vista-backend.exe'
$NodeExe = Join-Path $Package 'node/node.exe'
$MsbExe = Join-Path $Package 'app/mcp_servers/dev_mcp_server/.venv/Lib/site-packages/microsandbox/_bundled/bin/msb.exe'
$CurlExe = Join-Path $Package 'bin/curl.exe'

New-Dir $Package; New-Dir $Tools; New-Dir $Pids
Copy-Item (Join-Path $Root 'scripts/package_launcher.ps1') (Join-Path $Package 'vista.ps1')
Write-Text (Join-Path $Package 'VERSION') "test-version`n"
Write-Text (Join-Path $Package 'manifest.json') (
  '{"target": {"os": "windows", "arch": "x86_64", "longest_relative_path": 10, ' +
  '"longest_state_path": 10}, "window": {"exe": "app/window/missing-window.exe"}}')
New-Dir (Join-Path $Package 'python/cpython-3.14-test')
foreach ($project in 'backend', 'mcp_servers/vista_mcp_server', 'mcp_servers/dev_mcp_server') {
  Write-Text (Join-Path $Package "app/$project/.venv/pyvenv.cfg") "home = C:\elsewhere`n"
}
Write-Text (Join-Path $Package 'app/ui/server.js') ''
Write-Text (Join-Path $Package 'payload/parts.txt') ''
Write-Text (Join-Path $Package 'payload/sandbox-image.tar') ''

if ($OnWindows) {
  # One program for every fake, which acts on the name it is run as.
  $source = Join-Path $Tmp 'fake.cs'
  Write-Text $source @'
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;

static class Fake {
    static int Main(string[] args) {
        string self = Process.GetCurrentProcess().MainModule.FileName;
        string name = Path.GetFileName(self).ToLowerInvariant();
        if (name == "curl.exe") return 0;
        if (name == "msb.exe") {
            if (args.Length > 0 && args[0] == "doctor") { Console.WriteLine("Host setup is ready"); return 0; }
            if (args.Length > 1 && args[0] == "image" && args[1] == "inspect")
                return Environment.GetEnvironmentVariable("TEST_IMAGE_PRESENT") == "0" ? 1 : 0;
            if (args.Length > 0 && args[0] == "load")
                return Environment.GetEnvironmentVariable("TEST_MSB_IMPORT_FAIL") == "1" ? 1 : 0;
            return 0;
        }
        string pids = Environment.GetEnvironmentVariable("TEST_SERVICE_PIDS");
        File.WriteAllText(Path.Combine(pids, name + ".pid"), Process.GetCurrentProcess().Id.ToString());
        Thread.Sleep(Timeout.Infinite);
        return 0;
    }
}
'@
  $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
  $fake = Join-Path $Tmp 'fake.exe'
  & $csc /nologo /out:$fake $source | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "csc could not build the fake services" }
  foreach ($target in $McpExe, $BackendExe, $NodeExe, $MsbExe, $CurlExe) {
    New-Dir (Split-Path -Parent $target)
    Copy-Item $fake $target
  }
  # An empty tar, from the tar the launcher itself uses.
  $emptyDir = Join-Path $Tmp 'empty'
  New-Dir $emptyDir
  & (Join-Path $env:SystemRoot 'System32\tar.exe') -cf (Join-Path $Package 'payload/payload.tar') -C $emptyDir .
  $Shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
} else {
  # This file is checked out with CRLF (.gitattributes), so its here-strings
  # carry carriage returns, which bash reads as part of each line.
  function Write-Script([string]$Path, [string]$Body) {
    Write-Text $Path ("#!/usr/bin/env bash`n" + ($Body -replace "`r", ''))
    & chmod +x $Path
  }
  $service = 'printf ''%s\n'' "$$" > "$TEST_SERVICE_PIDS/${0##*/}.pid"' + "`nexec sleep 600`n"
  foreach ($target in $McpExe, $BackendExe, $NodeExe) { Write-Script $target $service }
  Write-Script $MsbExe @'
case "$1 $2" in
  doctor*) echo 'Host setup is ready' ;;
  'image inspect') [[ "${TEST_IMAGE_PRESENT:-1}" == 1 ]] ;;
  load*) [[ "${TEST_MSB_IMPORT_FAIL:-0}" != 1 ]] ;;
esac
'@
  Write-Script $CurlExe "exit 0`n"
  Write-Script (Join-Path $Tools 'curl.exe') "exit 0`n"
  # cmd /d /s /c "<command>": the quotes stripped, backslashes made slashes. The
  # test's paths have no spaces, so that is enough to run what the launcher wrote.
  Write-Script (Join-Path $Tools 'cmd.exe') @'
shift 3
line="$*"
line="${line//\"/}"
eval "${line//\\//}"
'@
  # taskkill /F /T /PID <pid>: the whole tree.
  Write-Script (Join-Path $Tools 'taskkill.exe') @'
kill_tree() {
  local child
  for child in $(pgrep -P "$1"); do kill_tree "$child"; done
  kill -KILL "$1" 2>/dev/null || true
}
kill_tree "${@: -1}"
'@
  $SystemRoot = Join-Path $Tmp 'windows'
  Write-Script (Join-Path $SystemRoot 'System32/tar.exe') "exec tar `"`$@`"`n"
  & tar -cf (Join-Path $Package 'payload/payload.tar') -T /dev/null
  $Shell = (Get-Command pwsh).Source
}

# --- running the launcher -------------------------------------------------------

function New-LauncherInfo([string[]]$Arguments, [hashtable]$Environment = @{}) {
  $info = New-Object System.Diagnostics.ProcessStartInfo $Shell
  $info.Arguments = (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
      "`"$(Join-Path $Package 'vista.ps1')`"") + $Arguments) -join ' '
  $info.UseShellExecute = $false
  $info.RedirectStandardInput = $true
  $info.RedirectStandardOutput = $true
  $info.RedirectStandardError = $true
  $info.CreateNoWindow = $true
  $vars = $info.EnvironmentVariables
  $vars['VISTA_HOME'] = $State
  $vars['VISTA_UI_PORT'] = "$PortBase"
  $vars['VISTA_MCP_PORT'] = "$($PortBase + 1)"
  $vars['VISTA_BACKEND_PORT'] = "$($PortBase + 2)"
  $vars['TEST_SERVICE_PIDS'] = $Pids
  $vars['PROCESSOR_ARCHITECTURE'] = 'AMD64'
  if (-not $OnWindows) {
    # vista.ps1 prepends its own folders with Windows' `;`, which fuses them onto
    # the first entry here; that entry is a placeholder so the stand-ins survive.
    $vars['PATH'] = "/nonexistent:${Tools}:$($vars['PATH'])"
    $vars['SystemRoot'] = $SystemRoot
  }
  foreach ($key in $Environment.Keys) { $vars[$key] = $Environment[$key] }
  return $info
}

function Get-Lines([string]$Text) {
  @($Text -split "`n" | ForEach-Object { $_.TrimEnd("`r") } | Where-Object { $_ -ne '' })
}

# Runs the launcher to completion with stdin closed at once.
function Invoke-Launcher([string[]]$Arguments, [hashtable]$Environment = @{}) {
  $proc = [System.Diagnostics.Process]::Start((New-LauncherInfo $Arguments $Environment))
  $proc.StandardInput.Close()
  $stderr = $proc.StandardError.ReadToEndAsync()
  $stdout = $proc.StandardOutput.ReadToEnd()
  if (-not $proc.WaitForExit(120000)) { $proc.Kill(); throw 'the launcher did not finish' }
  [pscustomobject]@{ Status = $proc.ExitCode; Stdout = Get-Lines $stdout; Stderr = $stderr.Result }
}

function Assert-Lines([object[]]$Actual, [string[]]$Expected, [string]$What, [string]$Stderr = '') {
  if (($Actual -join "`n") -ceq ($Expected -join "`n")) { Pass $What; return }
  Fail "$What`n--- expected`n$($Expected -join "`n")`n--- got`n$($Actual -join "`n")`n--- stderr`n$Stderr"
}

function Get-ServicePids {
  @(Get-ChildItem $Pids -Filter '*.pid' -ErrorAction SilentlyContinue |
    ForEach-Object { [int](Get-Content $_.FullName -Raw).Trim() })
}

function Stop-FakeServices {
  foreach ($id in Get-ServicePids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
  Remove-Item (Join-Path $Pids '*') -Force -ErrorAction SilentlyContinue
}

function Test-Alive([int]$Id) { [bool](Get-Process -Id $Id -ErrorAction SilentlyContinue) }

$Running = '{"protocol":1,"phase":"preflight","state":"running","label":"Checking this computer"}'

try {
  # --- the argument contract ---------------------------------------------------

  $r = Invoke-Launcher @('-Supervised')
  if ($r.Status -ne 0 -and $r.Stderr -match '-Supervised requires -Progress jsonl' -and $r.Stdout.Count -eq 0) {
    Pass '-Supervised alone is refused, with no protocol output'
  } else { Fail "-Supervised alone: status $($r.Status), stderr: $($r.Stderr)" }

  $r = Invoke-Launcher @('-Progress', 'jsonl')
  if ($r.Status -ne 0 -and $r.Stderr -match '-Progress jsonl requires -Supervised') {
    Pass '-Progress jsonl alone is refused'
  } else { Fail "-Progress alone: status $($r.Status), stderr: $($r.Stderr)" }

  $Supervised = @('-Supervised', '-Progress', 'jsonl')

  # --- failures: their phase, their code, and an optional log basename --------

  $listener = New-Object System.Net.Sockets.TcpListener ([Net.IPAddress]::Loopback, $PortBase)
  $listener.Start()
  try {
    $r = Invoke-Launcher $Supervised
  } finally {
    $listener.Stop()
  }
  Assert-Lines $r.Stdout @($Running,
    '{"protocol":1,"phase":"preflight","state":"failed","code":"port-conflict"}') `
    'a port in use fails preflight with port-conflict' $r.Stderr
  if ($r.Status -eq 0) { Fail 'the port conflict exited 0' }

  Remove-Item (Join-Path $Package 'payload/parts.txt')
  $r = Invoke-Launcher $Supervised
  Assert-Lines $r.Stdout @($Running,
    '{"protocol":1,"phase":"preflight","state":"failed","code":"missing-component"}') `
    'a missing package part fails preflight with missing-component' $r.Stderr

  Write-Text (Join-Path $Package 'payload/parts.txt') "missing-resource`n"
  $r = Invoke-Launcher $Supervised
  Assert-Lines $r.Stdout @($Running,
    '{"protocol":1,"phase":"preflight","state":"complete"}',
    '{"protocol":1,"phase":"resources","state":"running","label":"Installing bundled resources"}',
    '{"protocol":1,"phase":"resources","state":"failed","code":"resource-extraction-failed"}') `
    'a payload that cannot be extracted fails resources' $r.Stderr
  Write-Text (Join-Path $Package 'payload/parts.txt') ''

  $r = Invoke-Launcher $Supervised @{ TEST_IMAGE_PRESENT = '0'; TEST_MSB_IMPORT_FAIL = '1' }
  Assert-Lines $r.Stdout @($Running,
    '{"protocol":1,"phase":"preflight","state":"complete"}',
    '{"protocol":1,"phase":"resources","state":"running","label":"Installing bundled resources"}',
    '{"protocol":1,"phase":"resources","state":"complete","skipped":true}',
    '{"protocol":1,"phase":"sandbox","state":"running","label":"Preparing the code-execution sandbox"}',
    '{"protocol":1,"phase":"sandbox","state":"failed","code":"sandbox-image-import-failed","log":"setup.log"}') `
    'a failed image import fails sandbox, naming setup.log' $r.Stderr

  # --- a full start, then the application closing stdin ------------------------

  $proc = [System.Diagnostics.Process]::Start((New-LauncherInfo $Supervised))
  $stderr = $proc.StandardError.ReadToEndAsync()
  $lines = New-Object System.Collections.Generic.List[string]
  $deadline = (Get-Date).AddSeconds(90)
  while ((Get-Date) -lt $deadline) {
    $next = $proc.StandardOutput.ReadLineAsync()
    if (-not $next.Wait([int](($deadline - (Get-Date)).TotalMilliseconds))) { break }
    if ($null -eq $next.Result) { break }
    $lines.Add($next.Result.TrimEnd("`r"))
    if ($next.Result -like '*"phase":"ui","state":"ready"*') { break }
  }
  for ($i = 0; $i -lt 100 -and (Get-ServicePids).Count -lt 3; $i++) { Start-Sleep -Milliseconds 100 }
  $servicePids = Get-ServicePids
  if ($servicePids.Count -eq 3) { Pass 'all three services started' }
  else { Fail "expected 3 running services, found $($servicePids.Count)" }

  $proc.StandardInput.Close()
  $rest = $proc.StandardOutput.ReadToEnd()
  if (-not $proc.WaitForExit(30000)) {
    Fail 'the launcher did not stop within 30s of stdin closing'
    $proc.Kill()
  } elseif ($proc.ExitCode -eq 0) {
    Pass 'closing stdin stops the launcher, with status 0'
  } else {
    Fail "closing stdin: the launcher exited $($proc.ExitCode)"
  }
  foreach ($line in Get-Lines $rest) { $lines.Add($line) }

  $url = "http://127.0.0.1:$PortBase"
  Assert-Lines $lines.ToArray() @($Running,
    '{"protocol":1,"phase":"preflight","state":"complete"}',
    '{"protocol":1,"phase":"resources","state":"running","label":"Installing bundled resources"}',
    '{"protocol":1,"phase":"resources","state":"complete","skipped":true}',
    '{"protocol":1,"phase":"sandbox","state":"running","label":"Preparing the code-execution sandbox"}',
    '{"protocol":1,"phase":"sandbox","state":"complete","skipped":true}',
    '{"protocol":1,"phase":"mcp","state":"running","label":"Starting scientific tools"}',
    '{"protocol":1,"phase":"mcp","state":"ready"}',
    '{"protocol":1,"phase":"backend","state":"running","label":"Preparing VISTA"}',
    '{"protocol":1,"phase":"backend","state":"ready"}',
    '{"protocol":1,"phase":"ui","state":"running","label":"Starting the interface"}',
    "{`"protocol`":1,`"phase`":`"ui`",`"state`":`"ready`",`"url`":`"$url`"}",
    '{"protocol":1,"phase":"stopping","state":"running","label":"Stopping VISTA"}') `
    'a full start reports the same events as package_launcher.sh, in order' $stderr.Result

  $left = @()
  for ($i = 0; $i -lt 50; $i++) {
    $left = @($servicePids | Where-Object { Test-Alive $_ })
    if ($left.Count -eq 0) { break }
    Start-Sleep -Milliseconds 100
  }
  if ($servicePids.Count -gt 0 -and $left.Count -eq 0) { Pass 'the stop leaves no service running' }
  else { Fail "services still running after the stop: $($left -join ', ')" }
  Stop-FakeServices

  # --- the diagnostic launcher keeps its human output ----------------------------

  $proc = [System.Diagnostics.Process]::Start((New-LauncherInfo @() @{ VISTA_NO_WINDOW = '1' }))
  $stderr = $proc.StandardError.ReadToEndAsync()
  $human = New-Object System.Collections.Generic.List[string]
  $deadline = (Get-Date).AddSeconds(90)
  while ((Get-Date) -lt $deadline) {
    $next = $proc.StandardOutput.ReadLineAsync()
    if (-not $next.Wait([int](($deadline - (Get-Date)).TotalMilliseconds))) { break }
    if ($null -eq $next.Result) { break }
    $human.Add($next.Result)
    if ($next.Result -like '*VISTA is running at*') { break }
  }
  if (($human -join "`n") -match [regex]::Escape("VISTA is running at $url (no window: VISTA_NO_WINDOW is set)") -and
      -not ($human | Where-Object { $_ -like '{"protocol"*' })) {
    Pass 'the ordinary launcher prints its address and no protocol'
  } else {
    Fail "ordinary launcher output:`n$($human -join "`n")`n--- stderr`n$($stderr.Result)"
  }
  if (-not $proc.HasExited) { $proc.Kill() }
  [void]$proc.WaitForExit(10000)
} finally {
  Stop-FakeServices
  Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}

if ($failures -gt 0) {
  [Console]::Error.WriteLine("$failures check(s) failed")
  exit 1
}
Write-Host 'vista.ps1 supervised-mode tests passed'
