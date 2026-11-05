@echo off
setlocal
set PYTHONUTF8=1
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Falta el entorno Python local. & exit /b 1)
".venv\Scripts\python.exe" app.py menu
exit /b %ERRORLEVEL%
