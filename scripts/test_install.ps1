# Hermetic tests for scripts/install.ps1, against fake packages served from a
# local folder. Runs under PowerShell 7 on Windows, Linux or macOS; no network
# and no real package. Off Windows, the Start menu and path-length steps are
# skipped, as the installer itself skips them.
#
#   pwsh scripts/test_install.ps1

$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot 'install.ps1'
$root = Join-Path ([IO.Path]::GetTempPath()) ("vista-install-test-" + [IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $root | Out-Null
# macOS's temporary folder is reached through a symlink (/var -> /private/var),
# and a process reports its real path; compare like with like.
if (-not $IsWindows) { $root = (& realpath $root).Trim() }
$script:failures = 0

function Check([string]$What, [bool]$Ok) {
  if ($Ok) { Write-Host "  ok   $What" } else { Write-Host "  FAIL $What"; $script:failures++ }
}

# Whether a refused run said why. PowerShell wraps an error message at the
# console width, so line breaks in the output are ignored. Prints the output
# when the check fails.
function Check-Refused([string]$What, [hashtable]$Result, [string]$Message) {
  $said = ($Result.Out -replace '\s+', ' ') -match [regex]::Escape($Message)
  Check $What ((-not $Result.Ok) -and $said)
  if ($Result.Ok -or -not $said) { Write-Host $Result.Out }
}

# On Windows, one fake executable that acts on its own name: as VISTA.exe it
# records its arguments, as sleeper.exe it waits. Built with the .NET
# Framework's csc, which every Windows has.
$fakeExe = $null
if ($IsWindows) {
  $source = Join-Path $root 'fake.cs'
  Set-Content -Path $source -Value @"
using System; using System.IO; using System.Threading;
static class Fake {
  static void Main(string[] args) {
    string self = System.Diagnostics.Process.GetCurrentProcess().MainModule.FileName;
    if (Path.GetFileName(self).ToLowerInvariant() == "sleeper.exe") { Thread.Sleep(60000); return; }
    File.WriteAllText(@"$root\ran", "ran " + self + " " + string.Join(" ", args));
  }
}
"@
  $fakeExe = Join-Path $root 'fake.exe'
  & (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe') /nologo "/out:$fakeExe" $source | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'csc could not build the fake VISTA.exe' }
}

# A fake package: VERSION, a vista.cmd that records that it ran, and the window
# at app\window\VISTA.exe.
function New-Release([string]$Dir, [string]$Version) {
  $name = "vista-$Version-win-x86"
  $src = Join-Path $root "src/$Version"
  New-Item -ItemType Directory -Force -Path (Join-Path $src "$name/app/window"), $Dir | Out-Null
  Set-Content -Path (Join-Path $src "$name/VERSION") -Value $Version
  Set-Content -Path (Join-Path $src "$name/vista.cmd") -Value "@echo ran > `"$root\ran`""
  $window = Join-Path $src "$name/app/window/VISTA.exe"
  if ($fakeExe) { Copy-Item $fakeExe $window } else { Set-Content -Path $window -Value '' }
  $zip = Join-Path $Dir "$name.zip"
  Compress-Archive -Path (Join-Path $src $name) -DestinationPath $zip
  $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
  Set-Content -Path "$zip.sha256" -Value "$hash  $name.zip"
}

# Run the installer in a child PowerShell, with its own environment.
function Invoke-Installer([hashtable]$Env, [string[]]$Arguments, [string]$Script = $installer) {
  $saved = @{}
  foreach ($k in $Env.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $Env[$k]) }
  try {
    $out = & pwsh -NoProfile -NonInteractive -File $Script @Arguments 2>&1 | Out-String
    return @{ Ok = ($LASTEXITCODE -eq 0); Out = $out }
  } finally {
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
  }
}

try {
  $installDir = Join-Path $root 'VISTA/app'
  $state = Join-Path $root 'state'
  New-Item -ItemType Directory -Path $state | Out-Null
  Set-Content -Path (Join-Path $state 'vista.db') -Value 'my chats'
  foreach ($v in '1.0.0', '1.1.0', '2.0.0') { New-Release (Join-Path $root "releases/$v") $v }
  $common = @{ VISTA_INSTALL_DIR = $installDir; VISTA_INSTALL_MIN_FREE_GB = '0'; VISTA_INSTALL_NO_SHORTCUT = '1'; VISTA_HOME = $state }

  Write-Host 'install.ps1'

  $r = Invoke-Installer ($common + @{ VISTA_INSTALL_BASE_URL = (Join-Path $root 'releases/1.0.0') }) @('-Version', '1.0.0', '-NoLaunch')
  if (-not $r.Ok) { Write-Host $r.Out }
  Check 'a fresh install succeeds' $r.Ok
  Check 'a fresh install unpacks the package' (Test-Path (Join-Path $installDir 'vista.cmd'))
  Check 'it records the installed version' ((Get-Content (Join-Path $installDir 'VERSION')).Trim() -eq '1.0.0')
  Check 'it leaves no work folder behind' (-not (Get-ChildItem (Join-Path $root 'VISTA') -Filter '.install.*' -Force))
  Check '-NoLaunch does not start VISTA' (-not (Test-Path (Join-Path $root 'ran')))

  $r = Invoke-Installer ($common + @{ VISTA_INSTALL_BASE_URL = (Join-Path $root 'nowhere') }) @('-Version', 'v1.0.0', '-NoLaunch')
  Check 'a re-run of the installed version downloads nothing' ($r.Ok -and $r.Out -match 'already installed')

  $r = Invoke-Installer ($common + @{ VISTA_INSTALL_BASE_URL = (Join-Path $root 'releases/1.1.0'); VISTA_INSTALL_NO_LAUNCH = '1' }) @('-Version', '1.1.0')
  if (-not $r.Ok) { Write-Host $r.Out }
  Check 'an upgrade installs the new version' ($r.Ok -and (Get-Content (Join-Path $installDir 'VERSION')).Trim() -eq '1.1.0')
  Check 'VISTA_INSTALL_NO_LAUNCH=1 does not start VISTA' (-not (Test-Path (Join-Path $root 'ran')))
  Check "an upgrade keeps VISTA's state" ((Get-Content (Join-Path $state 'vista.db')) -eq 'my chats')

  $bad = Join-Path $root 'releases/bad'
  Copy-Item -Recurse (Join-Path $root 'releases/2.0.0') $bad
  Add-Content -Path (Join-Path $bad 'vista-2.0.0-win-x86.zip') -Value 'corrupt'
  $r = Invoke-Installer ($common + @{ VISTA_INSTALL_BASE_URL = $bad }) @('-Version', '2.0.0', '-NoLaunch')
  Check-Refused 'a checksum mismatch fails and says so' $r 'does not match its checksum'
  Check 'a checksum mismatch keeps the installed version' ((Get-Content (Join-Path $installDir 'VERSION')).Trim() -eq '1.1.0')
  Check 'a checksum mismatch leaves no work folder' (-not (Get-ChildItem (Join-Path $root 'VISTA') -Filter '.install.*' -Force))

  $r = Invoke-Installer $common @('-NoLaunch')
  Check-Refused 'an unrendered installer without -Version is refused' $r 'no version written in'

  # VISTA running from the install: any process started from inside the folder.
  $sleeperDir = Join-Path $installDir 'app/bin'
  New-Item -ItemType Directory -Force -Path $sleeperDir | Out-Null
  if ($IsWindows) {
    $sleeper = Join-Path $sleeperDir 'sleeper.exe'
    Copy-Item $fakeExe $sleeper
    $holder = Start-Process -FilePath $sleeper -PassThru -WindowStyle Hidden
  } else {
    $sleeper = Join-Path $sleeperDir 'sleep'
    Copy-Item (Get-Command sleep -CommandType Application | Select-Object -First 1).Source $sleeper
    $holder = Start-Process -FilePath $sleeper -ArgumentList '60' -PassThru
  }
  try {
    Start-Sleep -Milliseconds 500
    # The check needs the stand-in seen at its own path. macOS kills a copy of
    # one of its system binaries at once, and under emulation (an amd64
    # container on Apple Silicon) the path is the emulator's; Windows and
    # Linux runners see it.
    $seen = (Get-Process -Id $holder.Id -ErrorAction SilentlyContinue).Path
    if ($seen -ne $sleeper) {
      Write-Host "  skip an upgrade while VISTA runs is refused -- this host shows the stand-in at '$seen', not its path"
    } else {
      $r = Invoke-Installer ($common + @{ VISTA_INSTALL_BASE_URL = (Join-Path $root 'releases/2.0.0') }) @('-Version', '2.0.0', '-NoLaunch')
      Check-Refused 'an upgrade while VISTA runs is refused' $r 'Close VISTA, then run this again'
      Check 'a refused upgrade changes nothing' ((Get-Content (Join-Path $installDir 'VERSION')).Trim() -eq '1.1.0')
    }
  } finally {
    Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue
    $holder.WaitForExit(10000) | Out-Null
  }
  Remove-Item -Recurse -Force $sleeperDir -ErrorAction SilentlyContinue

  $rendered = Join-Path $root 'install.ps1'
  (Get-Content $installer -Raw).Replace('@VISTA_VERSION@', '1.0.0') | Set-Content -Path $rendered -NoNewline
  Remove-Item -Recurse -Force $installDir
  $r = Invoke-Installer ($common + @{ VISTA_INSTALL_BASE_URL = (Join-Path $root 'releases/1.0.0') }) @('-NoLaunch') $rendered
  Check 'a rendered installer installs its own version' ($r.Ok -and (Get-Content (Join-Path $installDir 'VERSION')).Trim() -eq '1.0.0')

  if ($IsWindows) {
    $appData = Join-Path $root 'appdata'
    New-Item -ItemType Directory -Force -Path (Join-Path $appData 'Microsoft/Windows/Start Menu/Programs') | Out-Null
    $r = Invoke-Installer ($common + @{ VISTA_INSTALL_NO_SHORTCUT = '0'; APPDATA = $appData }) @('-Version', '1.0.0')
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $appData 'Microsoft/Windows/Start Menu/Programs/VISTA.lnk'))
    Check 'the Start menu entry opens VISTA.exe with --startup' (
      $lnk.TargetPath -eq (Join-Path $installDir 'app\window\VISTA.exe') -and $lnk.Arguments -eq '--startup')
    for ($i = 0; $i -lt 50 -and -not (Test-Path (Join-Path $root 'ran')); $i++) { Start-Sleep -Milliseconds 100 }
    $ran = if (Test-Path (Join-Path $root 'ran')) { Get-Content (Join-Path $root 'ran') -Raw } else { '' }
    Check 'without -NoLaunch it opens VISTA.exe --startup, not vista.cmd' (
      $r.Ok -and $ran -match 'VISTA\.exe --startup')
  }
}
finally {
  Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue
}

if ($script:failures) { Write-Host "$($script:failures) failed"; exit 1 }
Write-Host 'all passed'
