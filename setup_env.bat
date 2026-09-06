@echo off
:: 设置字符集为UTF-8，防止中文乱码
chcp 65001 >nul
:: 开启延迟变量扩展（用于读取用户输入）
setlocal enabledelayedexpansion
color 0B
title 首次部署 - 环境初始化

echo ===================================================
echo      商用航空发动机情报系统 - 首次部署与环境修复
echo ===================================================
echo.
echo 警告：此操作将创建或覆盖现有的虚拟环境。
echo 请确保本机已安装 Python 3.11。
pause

echo.
echo [1/3] 正在清理旧环境 (如果存在)...
if exist "venv" rmdir /s /q venv

echo [2/3] 正在智能寻找 Python 3.11 并创建纯净虚拟环境...

:: 方案 A：尝试使用 Windows 官方的 py 启动器（最标准的情况）
py -3.11 --version >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo [成功] 找到 Python 3.11 (通过官方 py 启动器)
    py -3.11 -m venv venv
    goto :env_created
)

:: 方案 B：尝试绝大多数人默认的个人安装路径
set "COMMON_PATH_1=%LocalAppData%\Programs\Python\Python311\python.exe"
if exist "%COMMON_PATH_1%" (
    echo [成功] 找到 Python 3.11 (通过默认个人路径)
    "%COMMON_PATH_1%" -m venv venv
    goto :env_created
)

:: 方案 C：尝试绝大多数人默认的全局安装路径
set "COMMON_PATH_2=C:\Program Files\Python311\python.exe"
if exist "%COMMON_PATH_2%" (
    echo [成功] 找到 Python 3.11 (通过全局安装路径)
    "%COMMON_PATH_2%" -m venv venv
    goto :env_created
)

:: 方案 D：如果脚本找不到，则要求用户手动输入（终极防线）
echo.
echo [警告] 系统未能自动检测到 Python 3.11 的安装位置！
echo 请找到您电脑上 Python 3.11 的安装目录，并复制 python.exe 的完整路径。
echo (例如: D:\Python311\python.exe 或 C:\Users\xxx\...\python.exe)
echo.
set /p USER_PYTHON_PATH="请在此处粘贴完整路径并按回车: "

:: 去除用户可能不小心带上的双引号
set USER_PYTHON_PATH=!USER_PYTHON_PATH:"=!

if not exist "!USER_PYTHON_PATH!" (
    echo.
    echo [致命错误] 您输入的路径不存在！请检查后重新运行此脚本。
    pause
    exit /b
)

echo [成功] 正在使用您提供的路径创建环境...
"!USER_PYTHON_PATH!" -m venv venv

:env_created
echo.
echo [3/3] 正在安装核心依赖包 (这可能需要几分钟，请保持网络畅通)...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ===================================================
echo 部署完美完成！环境已全部就绪。
echo 请双击 start_system.bat 启动情报收集系统主引擎。
echo ===================================================
pause