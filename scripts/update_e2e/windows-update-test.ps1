<#
The in-app update's end-to-end run on a Windows machine with no VISTA checkout
(docs/validation-lane.md, "In-app update"). Needs only what Windows ships:
PowerShell 5.1 or later and tar.exe.

  1. powershell -ExecutionPolicy Bypass -File windows-update-test.ps1 -Package <vista-...-win-x86.zip>
     Makes fake releases 0.0.1, 0.0.2 and 0.0.3 from that package, installs 0.0.1
     into C:\vista-e2e\app, opens it, and then serves the fake GitHub. Leave the
     window open: closing it stops the fake GitHub.
  2. Update to 0.0.2 in Settings > About.
  3. Quit VISTA, then in a second window:
     powershell -ExecutionPolicy Bypass -File windows-update-test.ps1 -Fail
     Offers 0.0.3 with an installer that will refuse it, and opens VISTA again.

-CopyHome copies %USERPROFILE%\.vista into the test's state folder first, to test
with real chats; without it the test starts from an empty one. Nothing outside
C:\vista-e2e is written: no Start-menu entry, and your own VISTA is not touched.
Delete C:\vista-e2e afterwards.
#>
param(
  [string]$Package,
  [switch]$Fail,
  [switch]$CopyHome,
  [string]$Root = 'C:\vista-e2e',
  [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$branch = 'worktree-in-app-updates'
$installerUrl = "https://raw.githubusercontent.com/Genesis-VISTA/vista/$branch/scripts/install.ps1"
$api = "http://localhost:$Port"
$releases = Join-Path $Root 'releases'
$installed = Join-Path $Root 'installed'
$later = Join-Path $Root 'later'
$app = Join-Path $Root 'app'
$home_ = Join-Path $Root 'home'
$window = Join-Path $app 'app\window\VISTA.exe'
$tar = Join-Path $env:SystemRoot 'System32\tar.exe'

function Say([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }

# The environment VISTA starts with; the update helper and the installer inherit it.
function Set-TestEnvironment {
  $env:VISTA_HOME = $home_
  $env:VISTA_BACKEND_UPDATES__GITHUB_API = $api
  $env:VISTA_INSTALL_NO_SHORTCUT = '1'
}

function Open-Vista {
  Say "opening VISTA from $app"
  Start-Process -FilePath $window -ArgumentList '--startup' -WorkingDirectory $app
}

# --- a fake release ----------------------------------------------------------

function New-Release([string]$Stage, [string]$Built, [string]$Version, [string]$Into, [string]$Installer) {
  $name = "vista-$Version-win-x86"
  $out = Join-Path $Into "v$Version"
  New-Item -ItemType Directory -Force -Path $out | Out-Null
  Rename-Item (Join-Path $Stage $Built) $name
  Set-Content -Path (Join-Path $Stage "$name\VERSION") -Value $Version -Encoding ascii
  $manifestPath = Join-Path $Stage "$name\manifest.json"
  $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
  $manifest.version = $Version
  [IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 20))
  $zip = Join-Path $out "$name.zip"
  Say "packing $name.zip"
  & $tar -a -c -f $zip -C $Stage $name
  if ($LASTEXITCODE -ne 0) { throw "could not pack $zip" }
  $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
  [IO.File]::WriteAllText("$zip.sha256", "$hash  $name.zip`n")
  [IO.File]::WriteAllText((Join-Path $out 'install.ps1'),
    $Installer.Replace('@VISTA_VERSION@', $Version).Replace('@VISTA_REPO_URL@', 'https://github.com/Genesis-VISTA/vista'))
  [IO.File]::WriteAllText((Join-Path $out 'update.json'), "{`"data_compatible_from`": `"0.0.1`"}`n")
  [IO.File]::WriteAllBytes((Join-Path $out 'vista-schema.sqlite'), [byte[]]@())
  return $name
}

function New-Releases([string]$From) {
  if (-not (Test-Path $From)) { throw "no package at $From" }
  Say "downloading the installer from the $branch branch"
  $installer = (Invoke-WebRequest -UseBasicParsing -Uri $installerUrl).Content
  $stage = Join-Path $Root 'stage'
  if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
  New-Item -ItemType Directory -Force -Path $stage | Out-Null
  Say "unpacking $From"
  & $tar -x -f $From -C $stage
  if ($LASTEXITCODE -ne 0) { throw "could not unpack $From" }
  $built = (Get-ChildItem $stage -Directory | Select-Object -First 1).Name
  $built = New-Release $stage $built '0.0.1' $installed $installer
  $built = New-Release $stage $built '0.0.2' $releases $installer
  $built = New-Release $stage $built '0.0.3' $later $installer
  Remove-Item -Recurse -Force $stage
}

# --- the fake GitHub -----------------------------------------------------------

$digests = @{}
function Get-Digest([IO.FileInfo]$File) {
  $key = "$($File.FullName)|$($File.Length)|$($File.LastWriteTimeUtc.Ticks)"
  if (-not $digests.ContainsKey($key)) {
    $digests[$key] = (Get-FileHash $File.FullName -Algorithm SHA256).Hash.ToLower()
  }
  return $digests[$key]
}

function Get-Releases {
  $folders = @(Get-ChildItem $releases -Directory | Where-Object { $_.Name -like 'v*' } |
    Sort-Object { [version]($_.Name.TrimStart('v')) } -Descending)
  foreach ($folder in $folders) {
    $tag = $folder.Name
    $assets = @(Get-ChildItem $folder.FullName -File | ForEach-Object {
      [ordered]@{
        name = $_.Name
        size = $_.Length
        digest = 'sha256:' + (Get-Digest $_)
        browser_download_url = "$api/files/$tag/$($_.Name)"
      }
    })
    [ordered]@{ tag_name = $tag; draft = $false; prerelease = $false; html_url = "$api/notes/$tag"; assets = $assets }
  }
}

function Send-Json($Response, $Body, [int]$Status = 200) {
  $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Body -Depth 6 -Compress))
  $Response.StatusCode = $Status
  $Response.ContentType = 'application/json'
  $Response.ContentLength64 = $bytes.Length
  $Response.OutputStream.Write($bytes, 0, $bytes.Length)
}

function Start-FakeGitHub {
  $listener = New-Object Net.HttpListener
  $listener.Prefixes.Add("$api/")
  $listener.Start()
  Say "fake GitHub on $api, serving $releases. Leave this window open; Ctrl+C stops it."
  try {
    while ($listener.IsListening) {
      $context = $listener.GetContext()
      $path = $context.Request.Url.AbsolutePath
      $response = $context.Response
      Write-Host "  $($context.Request.HttpMethod) $path"
      try {
        if ($path -eq '/repos/Genesis-VISTA/vista/releases/latest') {
          $all = @(Get-Releases)
          if ($all.Count -gt 0) { Send-Json $response $all[0] } else { Send-Json $response @{ message = 'Not Found' } 404 }
        } elseif ($path -eq '/repos/Genesis-VISTA/vista/releases') {
          Send-Json $response @(Get-Releases)
        } elseif ($path -like '/files/*') {
          $parts = $path.Split('/', 4)
          $file = Join-Path (Join-Path $releases $parts[2]) $parts[3]
          if (Test-Path $file -PathType Leaf) {
            $response.ContentType = 'application/octet-stream'
            $response.ContentLength64 = (Get-Item $file).Length
            $stream = [IO.File]::OpenRead($file)
            try { $stream.CopyTo($response.OutputStream, 1MB) } finally { $stream.Dispose() }
          } else {
            Send-Json $response @{ message = 'Not Found' } 404
          }
        } else {
          Send-Json $response @{ message = 'Not Found' } 404
        }
      } catch {
        Write-Host "  error: $($_.Exception.Message)" -ForegroundColor Yellow
      } finally {
        $response.Close()
      }
    }
  } finally {
    $listener.Stop()
  }
}

# --- the run -----------------------------------------------------------------

if ($Fail) {
  if (-not (Test-Path (Join-Path $later 'v0.0.3'))) { throw "no 0.0.3 waiting in $later; run step 1 first" }
  Move-Item (Join-Path $later 'v0.0.3') $releases
  Set-TestEnvironment
  # Far more than any disk has, so the real installer refuses 0.0.3.
  $env:VISTA_INSTALL_MIN_FREE_GB = '100000'
  Say '0.0.3 is offered, and its installer will refuse it for lack of disk space'
  Open-Vista
  Say 'In Settings > About: Update to 0.0.3, Restart to update, Restart now.'
  return
}

if (-not $Package) { throw 'pass -Package <the downloaded vista-...-win-x86.zip>, or -Fail for the second run' }
$Package = (Resolve-Path $Package).Path
if (Get-Process -Name VISTA -ErrorAction SilentlyContinue) {
  throw 'VISTA is running. Close it first: the test VISTA uses the same ports.'
}
New-Item -ItemType Directory -Force -Path $Root, $releases, $installed, $later, $home_ | Out-Null

if ($CopyHome -and (Test-Path "$env:USERPROFILE\.vista\vista.db")) {
  Say "copying $env:USERPROFILE\.vista into $home_"
  robocopy "$env:USERPROFILE\.vista" $home_ /E /XD logs updates /NFL /NDL /NJH /NJS /NP | Out-Null
}

New-Releases $Package

Say "installing 0.0.1 into $app"
$env:VISTA_INSTALL_BASE_URL = 'file:///' + (Join-Path $installed 'v0.0.1').Replace('\', '/')
$env:VISTA_INSTALL_DIR = $app
$env:VISTA_INSTALL_NO_SHORTCUT = '1'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $installed 'v0.0.1\install.ps1') -Version 0.0.1 -NoLaunch
if ($LASTEXITCODE -ne 0) { throw 'the 0.0.1 install failed' }
Remove-Item Env:VISTA_INSTALL_BASE_URL, Env:VISTA_INSTALL_DIR

Set-TestEnvironment
Open-Vista
Say 'In Settings > About: Update to 0.0.2, Restart to update, Restart now.'
Start-FakeGitHub
