@echo off
cd /d "%~dp0"

rem 不依赖 PATH：系统 PATH 里的 python 可能只是 Microsoft Store 的占位程序
set "PY="

rem 1) 绿色版自带的运行时（在上一层的 runtime 目录里，朋友电脑首选）
if exist "%~dp0..\runtime\python.exe" set "PY=%~dp0..\runtime\python.exe"
if not defined PY if exist "%~dp0runtime\python.exe" set "PY=%~dp0runtime\python.exe"

rem 2) WorkBuddy 自带的 Python
if not defined PY for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
    if exist "%%D\python.exe" if not defined PY set "PY=%%D\python.exe"
)

rem 3) 官方安装的常见位置
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if exist "%%D\python.exe" if not defined PY set "PY=%%D\python.exe"
)
if not defined PY for /d %%D in ("C:\Python*") do (
    if exist "%%D\python.exe" if not defined PY set "PY=%%D\python.exe"
)
if not defined PY for /d %%D in ("%ProgramFiles%\Python*") do (
    if exist "%%D\python.exe" if not defined PY set "PY=%%D\python.exe"
)

if not defined PY goto nopython

rem 先用 pythonw：没有黑窗。出错时下面会写 logs\save_crash.log
set "PYW=%PY:python.exe=pythonw.exe%"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0savepanel.py"
    exit /b 0
)

"%PY%" "%~dp0savepanel.py"
if errorlevel 1 (
    echo.
    echo 启动失败，详细日志：logs\save_crash.log
    pause
)
exit /b 0

:nopython
echo.
echo   没找到可用的 Python 运行时。
echo   请确认上一层的 runtime 目录还在（里面有 python.exe）。
echo.
pause
