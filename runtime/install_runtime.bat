@echo off
setlocal
cd /d "%~dp0.."
chcp 65001 >nul

set "RUNTIME_ARCHIVE=runtime\commercial-aircraft-engine-runtime-win-x64-py311.zip"
set "RUNTIME_SHA256=70f340520f12f83557f464a5cf3f8000aefba7dc6f285a5619c74f15224345b8"

if not exist "%RUNTIME_ARCHIVE%" (
    echo [错误] 未找到运行环境压缩包：%RUNTIME_ARCHIVE%
    echo 请先运行 git lfs pull。
    exit /b 1
)

echo [1/3] 正在校验运行环境...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$actual=(Get-FileHash -Algorithm SHA256 -LiteralPath '%RUNTIME_ARCHIVE%').Hash.ToLower(); if($actual -ne '%RUNTIME_SHA256%'){Write-Error ('SHA-256 不匹配：'+$actual); exit 1}"
if errorlevel 1 exit /b 1

echo [2/3] 正在解压便携 Python 与文档导出依赖...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%RUNTIME_ARCHIVE%' -DestinationPath '.' -Force"
if errorlevel 1 exit /b 1

if not exist "portable_python\python.exe" (
    echo [错误] 解压后未找到 portable_python\python.exe。
    exit /b 1
)

echo [3/3] 正在验证 Python...
"portable_python\python.exe" --version
if errorlevel 1 exit /b 1

echo 运行环境安装完成。请配置 .env 后双击 start_system.bat。
exit /b 0
