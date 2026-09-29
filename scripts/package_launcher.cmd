@echo off
setlocal
rem Start VISTA from cmd or by double-clicking. Installed at the package root as
rem vista.cmd, beside vista.ps1, which does the work.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0vista.ps1" %*
set VISTA_EXIT=%errorlevel%
rem A double-clicked console closes the moment this ends, taking any error
rem vista.ps1 printed with it. Hold it open on failure so it can be read.
if not %VISTA_EXIT%==0 pause
exit /b %VISTA_EXIT%
