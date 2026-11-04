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

function Fail([string]$Reason) {
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
  $output = & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer -Version $version -NoLaunch 2>&1
  $status = $LASTEXITCODE
  foreach ($line in $output) { Say "installer: $line" }
  if ($status -ne 0) {
    # install.ps1 ends a refusal with one line: "error: VISTA was not installed: <why>".
    $refusal = @($output | ForEach-Object { "$_" } | Where-Object { $_ -match '^error: ' }) | Select-Object -Last 1
    $reason = if ($refusal) { $refusal -replace '^error: ', '' -replace '^VISTA was not installed: ', '' } else { "the installer exited with status $status." }
    Fail $reason
  }
  Say "installed VISTA $version; opening it"
  Open-Vista
} catch {
  Fail "the update could not run ($($_.Exception.Message))."
}
