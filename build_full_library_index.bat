@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
    "portable_python\python.exe" -u build_local_rag.py --build
) else (
    "portable_python\python.exe" -u build_local_rag.py %*
)
set "index_exit_code=%ERRORLEVEL%"
echo.
echo Index command finished with exit code %index_exit_code%.
exit /b %index_exit_code%
