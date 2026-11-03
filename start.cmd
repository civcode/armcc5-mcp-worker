@echo off
cd /d "%~dp0"
if not defined ARMCC_ROOT set "ARMCC_ROOT=%LOCALAPPDATA%\Keil_v5\ARM\ARM_Compiler_5.06u6"
set "PATH=%ARMCC_ROOT%\bin;%PATH%"
if not exist ".venv\Scripts\python.exe" (
  echo Set up Python first. Run: py -3.11 -m venv .venv
  exit /b 1
)
".venv\Scripts\python.exe" server.py
