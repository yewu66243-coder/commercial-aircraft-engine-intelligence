@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist "%~dp0portable_python\python.exe" (
    echo Bundled Python not found.
    exit /b 1
)
"%~dp0portable_python\python.exe" "%~dp0tools\manual_review.py" %*
exit /b %errorlevel%
