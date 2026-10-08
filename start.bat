@echo off
REM ===========================================================================
REM  TheNormalizer - launcher
REM
REM  Double-click this file. On a machine with nothing installed it provisions
REM  everything it needs, then opens the window; on a machine that has already
REM  run it once, it goes straight to the window.
REM
REM  Structure note: flat labels and `goto` rather than nested parenthesised
REM  blocks, because cmd.exe loses the exit code of an `exit /b` inside a
REM  nested block -- a real defect in the sibling product's launcher.
REM ===========================================================================

setlocal
title TheNormalizer
cd /d "%~dp0"

if not exist "backend\server.py" goto :no_app
if not exist "frontend\package.json" goto :no_app
if not exist "package.json" goto :no_app

where powershell >nul 2>nul
if errorlevel 1 goto :no_powershell

REM --- Everything start.bat deliberately does not do -------------------------------------------
REM  Finding or installing Python, Node and ffmpeg, building the window's dependencies and the
REM  interface bundle: that is bootstrap.ps1, and it is a script rather than a hundred lines of
REM  batch because batch cannot report a failure legibly.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\bootstrap.ps1" %*
set code=%ERRORLEVEL%
REM  Captured on the line after the call and not from `%ERRORLEVEL%` inside a block: the value is
REM  only reliable immediately after the command that set it.
if not "%code%"=="0" goto :failed

endlocal & exit /b 0


REM --- Error paths -----------------------------------------------------------------------------

:no_app
echo.
echo   [!] The application files are not in this folder.
echo       Expected backend\server.py, frontend\package.json and package.json
echo       beside this launcher. Keep start.bat inside the TheNormalizer-V2 folder.
echo.
pause
endlocal & exit /b 1

:no_powershell
echo.
echo   [!] Windows PowerShell was not found on PATH.
echo       It ships with Windows; if it is missing, run this from a normal
echo       Windows installation or provision the tools in scripts\bootstrap.ps1 by hand.
echo.
pause
endlocal & exit /b 1

:failed
echo.
echo   [!] The launcher stopped. The reason is printed above.
echo       To run the provisioning again and read the whole story without opening
echo       a window:
echo.
echo           powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -NoLaunch
echo.
pause
endlocal & exit /b 1
