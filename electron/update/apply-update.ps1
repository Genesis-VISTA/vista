# Install a downloaded VISTA update once VISTA has closed, then open VISTA again
# (openspec change `in-app-updates`, design D5). Windows; apply-update.sh is the
# macOS and Linux twin, and says more about each step.
#
# Started hidden and detached by the window from the update's own folder in
# VISTA_HOME\updates, with its inputs in the environment:
#
#   VISTA_UPDATE_INSTALL_DIR  the installed package (what the installer replaces)
#   VISTA_UPDATE_VERSION      the version being installed
#   VISTA_UPDATE_CACHE        the verified download: archive, .sha256, installer
#   VISTA_UPDATE_INSTALLER    the release's install.ps1, in that folder
#   VISTA_HOME                logs\update.log and updates\last-failure.json go here
#   VISTA_UPDATE_STATUS_WINDOW  the copied VISTA.exe in that folder, started first as
#                             the update status window (design D9); optional
#
# For tests only:
#   VISTA_UPDATE_WAIT_SECONDS how long to wait for VISTA to close (default 60)
#   VISTA_UPDATE_OPEN         a script that opens VISTA, instead of VISTA.exe --startup

$ErrorActionPreference = 'Stop'

$dir = $env:VISTA_UPDATE_INSTALL_DIR
$version = $env:VISTA_UPDATE_VERSION
$cache = $env:VISTA_UPDATE_CACHE
$installer = $env:VISTA_UPDATE_INSTALLER
$vistaHome = $env:VISTA_HOME
foreach ($name in 'VISTA_UPDATE_INSTALL_DIR', 'VISTA_UPDATE_VERSION', 'VISTA_UPDATE_CACHE', 'VISTA_UPDATE_INSTALLER', 'VISTA_HOME') {
  if (-not [Environment]::GetEnvironmentVariable($name)) { throw "apply-update: $name is not set" }
}
$waitSeconds = if ($env:VISTA_UPDATE_WAIT_SECONDS) { [int]$env:VISTA_UPDATE_WAIT_SECONDS } else { 60 }

$logs = Join-Path $vistaHome 'logs'
$updates = Join-Path $vistaHome 'updates'
New-Item -ItemType Directory -Force -Path $logs, $updates | Out-Null
$log = Join-Path $logs 'update.log'
$utf8 = New-Object System.Text.UTF8Encoding $false
$statusFile = Join-Path $cache 'status.json'

function Say([string]$Message) {
  $line = "$((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) apply-update: $Message"
  [IO.File]::AppendAllText($log, "$line`n", $utf8)
}

# The command the installer's own launch step uses (install.ps1 Start-Installed).
function Open-Vista {
  if ($env:VISTA_UPDATE_OPEN) {
    & $env:VISTA_UPDATE_OPEN $dir
    return
  }
  $window = Join-Path $dir 'app\window\VISTA.exe'
  Start-Process -FilePath $window -ArgumentList '--startup' -WorkingDirectory $dir
}

# What the status window shows: replaced whole, so it never reads half a file.
function Write-Status([string]$Step, [string]$Detail = '') {
  Say ("status: $Step" + $(if ($Detail) { " ($Detail)" } else { '' }))
  $body = [ordered]@{ version = $version; step = $Step; detail = $Detail } | ConvertTo-Json -Compress
  $tmp = "$statusFile.tmp"
  [IO.File]::WriteAllText($tmp, $body, $utf8)
  Move-Item -Force $tmp $statusFile
}

# Run from the update's folder, so the wait below never counts it as VISTA.
function Start-StatusWindow {
  $window = $env:VISTA_UPDATE_STATUS_WINDOW
  if (-not $window -or -not (Test-Path $window)) {
    Say 'no update status window'
    return
  }
  # The marker comes first, before any VISTA opens: the VISTA this helper opens
  # removes it once its startup window shows, which closes the status window
  # however late that window started.
  [IO.File]::WriteAllText((Join-Path $updates 'status-window.json'), ([ordered]@{ folder = $cache } | ConvertTo-Json -Compress), $utf8)
  Start-Process -FilePath $window -ArgumentList "--update-status=$cache" -WorkingDirectory $cache
  Say 'started the update status window'
}

function Fail([string]$Reason) {
  try { Write-Status 'failed' $Reason } catch { Say "could not write the status: $($_.Exception.Message)" }
  Say "update to $version failed: $Reason"
  $record = [ordered]@{ version = $version; reason = $Reason; log = 'update.log' } | ConvertTo-Json -Compress
  [IO.File]::WriteAllText((Join-Path $updates 'last-failure.json'), $record, $utf8)
  Say 'opening the installed version again'
  Open-Vista
  exit 1
}

# Any process run from the install folder means VISTA is still running: the
# installer's own test (install.ps1 Assert-NotRunning).
function Test-Running {
  $prefix = $dir.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
  $found = @(Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Id -ne $PID -and $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) })
  return $found.Count -gt 0
}

try {
  Say "updating VISTA in $dir to $version"
  Write-Status 'waiting'
  try { Start-StatusWindow } catch { Say "no update status window: $($_.Exception.Message)" }
  $waited = 0
  while (Test-Running) {
    if ($waited -ge $waitSeconds) {
      Fail "VISTA did not finish closing within $waitSeconds seconds, so nothing was installed."
    }
    Start-Sleep -Seconds 1
    $waited++
  }
  Say "VISTA has closed (after ${waited}s); running the installer"

  $env:VISTA_INSTALL_BASE_URL = $cache
  $env:VISTA_INSTALL_DIR = $dir
  # The PowerShell this runs in: powershell.exe from the window, pwsh in the tests.
  $powershell = (Get-Process -Id $PID).Path
  Write-Status 'installing' 'starting the installer'
  # Each of the installer's "==> " lines becomes the status window's line, as it
  # comes, up to "installed VISTA": what it says after that is for a terminal.
  $installed = $false
  $output = @(& $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer -Version $version -NoLaunch 2>&1 |
    ForEach-Object {
      $line = "$_"
      Say "installer: $line"
      if (-not $installed -and $line -match '^==> (.+)$') {
        Write-Status 'installing' $Matches[1]
        if ($Matches[1] -like 'installed VISTA*') { $installed = $true }
      }
      $line
    })
  $status = $LASTEXITCODE
  if ($status -ne 0) {
    # install.ps1 ends a refusal with one line: "error: VISTA was not installed: <why>".
    $refusal = @($output | ForEach-Object { "$_" } | Where-Object { $_ -match '^error: ' }) | Select-Object -Last 1
    $reason = if ($refusal) { $refusal -replace '^error: ', '' -replace '^VISTA was not installed: ', '' } else { "the installer exited with status $status." }
    Fail $reason
  }
  Say "installed VISTA $version; opening it"
  Write-Status 'opening'
  Open-Vista
} catch {
  Fail "the update could not run ($($_.Exception.Message))."
}
