@echo off
setlocal
rem Start VISTA. Installed at the package root as vista.cmd, beside vista.ps1,
rem which does the work. This is the entry point, from cmd, PowerShell, or a
rem double-click: vista.ps1 run directly skips the execution-policy step below.
set "VISTA_PS1=%~dp0vista.ps1"

rem -ExecutionPolicy Bypass loses to an execution policy set by Group Policy, as
rem on managed machines. Under RemoteSigned that policy refuses vista.ps1 when it
rem carries the mark of the web, which Explorer puts on every file it extracts
rem from a downloaded zip, and PowerShell says only "not digitally signed".
rem Running this file already cleared Windows' own warning for the download, so
rem the mark is taken off its sibling too. A command, unlike a script file, is
rem not subject to execution policy, so this step runs whatever the policy.
rem AllSigned cannot be met by an unsigned package; it is named instead.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Unblock-File -LiteralPath $env:VISTA_PS1 -ErrorAction SilentlyContinue; if ((Get-ExecutionPolicy) -eq 'AllSigned') { [Console]::Error.WriteLine('error: Group Policy on this machine lets PowerShell run only signed scripts (AllSigned), and vista.ps1 is not signed. Ask your IT administrator to allow it.'); exit 1 }"
set VISTA_EXIT=%errorlevel%
if not %VISTA_EXIT%==0 goto done

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%VISTA_PS1%" %*
set VISTA_EXIT=%errorlevel%

:done
rem A double-clicked console closes the moment this ends, taking any error
rem vista.ps1 printed with it. Hold it open on failure so it can be read.
if not %VISTA_EXIT%==0 pause
exit /b %VISTA_EXIT%
