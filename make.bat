@echo off
setlocal enabledelayedexpansion

rem =============================================================================
rem Project: distman
rem Windows make.bat equivalent for the Makefile targets.
rem
rem Usage:
rem   make.bat              -> build
rem   make.bat build        -> build (clean + install-to-build + prune)
rem   make.bat clean        -> remove build artifacts
rem   make.bat dryrun       -> dist --dryrun
rem   make.bat install      -> build, then dist --yes
rem =============================================================================

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=build"

if /I "%TARGET%"=="build" goto :build
if /I "%TARGET%"=="clean" goto :clean
if /I "%TARGET%"=="dryrun" goto :dryrun
if /I "%TARGET%"=="install" goto :install

echo Unknown target: %TARGET%
echo.
echo Valid targets: build, clean, dryrun, install
exit /b 2

:build
call "%~f0" clean
if errorlevel 1 exit /b %errorlevel%

rem Ensure build dir exists
if not exist build mkdir build

rem Install this project + its runtime deps into .\build (per pyproject.toml)
python -m pip install --upgrade pip setuptools wheel >NUL
echo Installing distman into .\build ...
python -m pip install . -t build
if errorlevel 1 exit /b %errorlevel%

rem Prune items that are not intended to ship (match Makefile)
for %%D in (build\bin build\lib build\distman build\__pycache__) do (
  if exist "%%D" rmdir /s /q "%%D"
)

rem Remove bdist-style dirs under build (build\bdist*)
for /d %%D in ("build\bdist*") do (
  if exist "%%D" rmdir /s /q "%%D"
)

goto :eof

:clean
if exist build (
  rmdir /s /q build
)
goto :eof

:dryrun
dist --dryrun
exit /b %errorlevel%

:install
call "%~f0" build
if errorlevel 1 exit /b %errorlevel%
dist --yes
exit /b %errorlevel%