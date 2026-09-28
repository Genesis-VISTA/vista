@echo off
rem Start VISTA from cmd or by double-clicking. Installed at the package root as
rem vista.cmd, beside vista.ps1, which does the work.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0vista.ps1" %*
