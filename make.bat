@echo off
setlocal enabledelayedexpansion

rem =============================================================================
rem Project: Distman - Simple File Distribution Manager
rem Windows make.bat equivalent for the provided Makefile.
rem
rem Usage:
rem   make.bat              -> build
rem   make.bat build        -> build
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
rem Build Python sdist/wheel into .\build (requires the 'build' package)
if not exist build mkdir build

python -m pip install -U pip setuptools wheel >NUL
python -m pip show build >NUL 2>&1
if errorlevel 1 (
  echo Installing python-build (pip package: build)...
  python -m pip install -U build
)

echo Building sdist/wheel into .\build ...
python -m build --outdir build
if errorlevel 1 exit /b %errorlevel%
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