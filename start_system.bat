@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
color 0A
title 商用航空发动机情报系统 - 核心引擎

rem ===================== Basic path config =====================
rem %~dp0 is the folder that contains this launcher.
rem Portable launch: use bundled Python, bundled Ollama, and local models.
set "APP_DIR=%~dp0."
set "PYTHON_EXE=%~dp0portable_python\python.exe"
set "PORTABLE_SCRIPTS=%~dp0portable_python\Scripts"
set "GTK_BIN=%~dp0dependencies\gtk3\bin"
set "DOC_PATH=%~dp0local_docs\all_papers_pool"
set "OLLAMA_MODEL_DIR=%~dp0ollama_models"
set "BUNDLED_OLLAMA_EXE=%~dp0ollama\ollama.exe"

rem ===================== Python and Ollama environment =====================
rem PYTHONUTF8/PYTHONIOENCODING reduce encoding issues in logs and paths.
rem OLLAMA_BASE_URL/OLLAMA_HOST point to local Ollama service.
rem OLLAMA_MODELS points to bundled model directory.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not defined OLLAMA_BASE_URL set "OLLAMA_BASE_URL=http://127.0.0.1:11434"
if not defined OLLAMA_HOST set "OLLAMA_HOST=127.0.0.1:11434"
if exist "%OLLAMA_MODEL_DIR%" set "OLLAMA_MODELS=%OLLAMA_MODEL_DIR%"

rem ===================== Locate bundled Ollama =====================
rem Preferred: ollama_models\ollama\ollama.exe
rem Compatible: ollama\ollama.exe or ollama_models\ollama\ollama.exe\ollama.exe
if not exist "%BUNDLED_OLLAMA_EXE%" if exist "%OLLAMA_MODEL_DIR%\ollama\ollama.exe" set "BUNDLED_OLLAMA_EXE=%OLLAMA_MODEL_DIR%\ollama\ollama.exe"
if not exist "%BUNDLED_OLLAMA_EXE%" if exist "%OLLAMA_MODEL_DIR%\ollama\ollama.exe\ollama.exe" set "BUNDLED_OLLAMA_EXE=%OLLAMA_MODEL_DIR%\ollama\ollama.exe\ollama.exe"

if not exist "%PYTHON_EXE%" goto :NO_PORTABLE_PYTHON

rem ===================== Runtime PATH =====================
rem Add bundled Python, GTK3, and Ollama runtime to PATH.
set "OLLAMA_EXE_DIR="
if exist "%BUNDLED_OLLAMA_EXE%" for %%F in ("%BUNDLED_OLLAMA_EXE%") do set "OLLAMA_EXE_DIR=%%~dpF"
set "PATH=%~dp0portable_python;%PORTABLE_SCRIPTS%;%GTK_BIN%;%OLLAMA_EXE_DIR%;%OLLAMA_EXE_DIR%lib\ollama;%PATH%"
if not exist "%~dp0logs" mkdir "%~dp0logs"

echo ===================================================
echo      商用航空发动机情报智能收集系统 (3-Agent)
echo                  启动控制台
echo ===================================================
echo.

echo [1/3] 正在检测便携运行环境...
echo       Python: %PYTHON_EXE%
echo       文献库: %DOC_PATH%
echo.

rem ===================== Start bundled Ollama =====================
rem Reuse an existing service on port 11434, or start bundled ollama.exe.
echo [2/3] 正在启动本地模型服务...
if exist "%BUNDLED_OLLAMA_EXE%" (
    call :CHECK_OLLAMA_RUNTIME
    curl -s "%OLLAMA_BASE_URL%/api/tags" >nul 2>nul
    if errorlevel 1 (
        start "" /b "%BUNDLED_OLLAMA_EXE%" serve > "%~dp0logs\ollama.log" 2>&1
    )
    call :WAIT_OLLAMA
) else (
    echo [提示] 未找到内置 Ollama，系统将使用关键词保底或外部 Ollama 服务。
)
echo.

echo [3/3] 正在拉起情报收集服务...
echo.
echo 系统即将就绪，浏览器将自动唤醒...
echo ===================================================
echo 注意: 请勿关闭此窗口! 这是系统的核心大脑.
echo 如需退出系统, 请在此窗口中按 Ctrl+C.
echo ===================================================
echo.

rem ===================== Open browser automatically =====================
rem Generate wait_browser.bat and open the web page after backend is ready.
echo @echo off > "%~dp0wait_browser.bat"
echo :loop >> "%~dp0wait_browser.bat"
echo curl -s -o nul http://127.0.0.1:8000 >> "%~dp0wait_browser.bat"
echo if errorlevel 1 ( >> "%~dp0wait_browser.bat"
echo     ping -n 2 127.0.0.1 ^>nul >> "%~dp0wait_browser.bat"
echo     goto loop >> "%~dp0wait_browser.bat"
echo ) >> "%~dp0wait_browser.bat"
echo start http://127.0.0.1:8000 >> "%~dp0wait_browser.bat"
start /b cmd /c "%~dp0wait_browser.bat"

rem ===================== Start backend =====================
rem Insert project root into sys.path to avoid portable Python _pth limits.
"%PYTHON_EXE%" -c "import os, sys, uvicorn; sys.path.insert(0, os.path.abspath(r'%APP_DIR%')); uvicorn.run('main:app', host='127.0.0.1', port=8000)"

echo.
echo [系统已停止] 后端服务已退出。
pause
exit /b

:NO_PORTABLE_PYTHON
echo ===================================================
echo [错误] 未找到 portable_python\python.exe。
echo 本便携启动器需要完整项目文件夹，
echo 请确认 portable_python 目录已随项目一起复制。
echo ===================================================
pause
exit /b 1

:WAIT_OLLAMA
rem ===================== Wait for Ollama =====================
for /l %%i in (1,1,20) do (
    curl -s "%OLLAMA_BASE_URL%/api/tags" >nul 2>nul
    if not errorlevel 1 (
echo [OK] 本地模型服务已就绪。
        exit /b 0
    )
    timeout /t 1 /nobreak >nul
)
echo [警告] 已找到内置 Ollama，但服务暂未响应。
echo        系统仍会继续启动；如果 Ollama 不可用，embedding 会切换到关键词保底。
exit /b 0

:CHECK_OLLAMA_RUNTIME
rem ===================== Check Ollama runtime =====================
rem ollama.exe alone is not enough; embeddings need llama-server.exe.
set "OLLAMA_EXE_DIR=%~dp0ollama\"
for %%F in ("%BUNDLED_OLLAMA_EXE%") do set "OLLAMA_EXE_DIR=%%~dpF"
if exist "%OLLAMA_EXE_DIR%llama-server.exe" exit /b 0
if exist "%OLLAMA_EXE_DIR%lib\ollama\llama-server.exe" exit /b 0
if exist "%OLLAMA_EXE_DIR%dist\windows-amd64\lib\ollama\llama-server.exe" exit /b 0
if exist "%OLLAMA_EXE_DIR%dist\windows_amd64\lib\ollama\llama-server.exe" exit /b 0
echo [警告] 已找到内置 ollama.exe，但未找到 llama-server.exe。
echo        Ollama 可能能列出模型，但无法真正生成 embedding。
echo        请复制完整 Ollama 运行目录，而不是只复制 ollama.exe。
exit /b 0
