@echo off
rem ============================================================
rem  AZS 工具箱 启动器
rem  1) 优先用包内自带的 runtime\pythonw.exe
rem     （绿色版：朋友电脑不用装任何东西）
rem  2) 找不到就退回本机已经装好的 Python
rem ============================================================
setlocal
cd /d "%~dp0"

set "APP=azs_toolkit.py"

if not exist "%~dp0%APP%" goto nofile

rem ---- 1. 包内自带运行时 ----
if exist "%~dp0runtime\pythonw.exe" (
    start "" "%~dp0runtime\pythonw.exe" "%~dp0%APP%"
    exit /b 0
)

rem ---- 2. WorkBuddy 自带的 Python ----
set "PY="
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
    if exist "%%D\pythonw.exe" set "PY=%%D\pythonw.exe"
)
if defined PY (
    start "" "%PY%" "%~dp0%APP%"
    exit /b 0
)

rem ---- 3. 系统安装的 Python ----
set "PY="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\pythonw.exe" set "PY=%%D\pythonw.exe"
)
if defined PY (
    start "" "%PY%" "%~dp0%APP%"
    exit /b 0
)

echo.
echo   没找到可用的 Python 运行时。
echo.
echo   如果你是从朋友那里拿到这个文件夹的，通常是压缩包没解压完整。
echo   请重新解压，并确认文件夹里有一个 runtime 目录，
echo   里面能看到 pythonw.exe 这个文件。
echo.
pause
exit /b 1

:nofile
echo.
echo   这个文件夹不完整：找不到 azs_toolkit.py。
echo.
echo   请把整个压缩包重新解压一遍，
echo   不要只从压缩包里拖出来其中几个文件。
echo.
pause
exit /b 1
