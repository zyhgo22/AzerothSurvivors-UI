@echo off
cd /d "%~dp0"

rem 不依赖 PATH：系统 PATH 里的 python 可能只是 Microsoft Store 的占位程序
set "PY="

rem 1) 绿色版自带的运行时（朋友电脑首选，不用装任何东西）
if exist "%~dp0runtime\python.exe" set "PY=%~dp0runtime\python.exe"
if not defined PY if exist "%~dp0..\runtime\python.exe" set "PY=%~dp0..\runtime\python.exe"

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

rem 优先用无控制台的 pythonw：黑窗一闪即过，关掉黑窗也不会杀掉面板
set "PYW=%PY:python.exe=pythonw.exe%"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0spellpanel.py"
    exit /b 0
)

"%PY%" "%~dp0spellpanel.py"
if errorlevel 1 (
    echo.
    echo 启动失败，详细日志：logs\panel_crash.log
    pause
)
exit /b 0

:nopython
echo.
echo   没找到可用的 Python 运行时。
echo.
echo   如果这是从朋友那里拿到的文件夹，多半是压缩包没解压完整。
echo   请重新解压，确认里面有一个 runtime 目录（里面有 python.exe）。
echo.
pause
