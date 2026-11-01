@echo off
rem ============================================================
rem  AZS-DpsGain 一键启动：数据大脑 + 悬浮层 + 游戏
rem  必须用本脚本启动游戏，否则拿不到 addon 数据（需要 RUST_LOG）。
rem  注意：新版「AZS工具箱」已经内置了这个功能，本脚本是备用入口。
rem ============================================================
setlocal
cd /d "%~dp0.."

if not exist "%~dp0logs" mkdir "%~dp0logs"

rem ---- 已经在跑就先退出：RUST_LOG 必须在游戏启动那一刻生效 ----
tasklist /FI "IMAGENAME eq AzerothSurvivors.exe" /NH 2>nul | find /I "AzerothSurvivors.exe" >nul
if not errorlevel 1 (
  echo.
  echo [!] Azeroth Survivors is already running.
  echo     Close the game first, then run this script again.
  echo     ^(The plugin needs RUST_LOG set before the game starts.^)
  echo.
  pause
  exit /b 1
)

rem ---- 先清掉上一轮残留（按 pid 精确停止，避免重复双击堆进程） ----
if exist "%~dp0logs\brain.pid" for /f "usebackq delims=" %%i in ("%~dp0logs\brain.pid") do taskkill /F /PID %%i >nul 2>&1
if exist "%~dp0logs\overlay.pid" for /f "usebackq delims=" %%i in ("%~dp0logs\overlay.pid") do taskkill /F /PID %%i >nul 2>&1
del /q "%~dp0logs\brain.pid" >nul 2>&1
del /q "%~dp0logs\overlay.pid" >nul 2>&1

rem ---- 找一个能用的 pythonw（绿色版自带优先，其次本机装的） ----
set "AZSPY="
if exist "%~dp0runtime\pythonw.exe" set "AZSPY=%~dp0runtime\pythonw.exe"
if not defined AZSPY if exist "%~dp0..\runtime\pythonw.exe" set "AZSPY=%~dp0..\runtime\pythonw.exe"
if not defined AZSPY for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
    if exist "%%D\pythonw.exe" if not defined AZSPY set "AZSPY=%%D\pythonw.exe"
)
if not defined AZSPY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if exist "%%D\pythonw.exe" if not defined AZSPY set "AZSPY=%%D\pythonw.exe"
)
if not defined AZSPY for /d %%D in ("C:\Python*") do (
    if exist "%%D\pythonw.exe" if not defined AZSPY set "AZSPY=%%D\pythonw.exe"
)
if not defined AZSPY for /d %%D in ("%ProgramFiles%\Python*") do (
    if exist "%%D\pythonw.exe" if not defined AZSPY set "AZSPY=%%D\pythonw.exe"
)
if not defined AZSPY goto nopython

rem ---- 后台进程 ----
start "" "%AZSPY%" "%~dp0brain.py"
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0overlay.ps1"

rem ---- 带调试输出启动游戏（关键：没有 RUST_LOG 就拿不到卡牌数据） ----
set RUST_LOG=benilla_app::ui_chat::net=debug
"AzerothSurvivors.exe" > "%~dp0logs\client.log" 2>&1

endlocal
exit /b 0

:nopython
echo.
echo   没找到可用的 Python 运行时。
echo.
echo   如果这是从朋友那里拿到的文件夹，多半是压缩包没解压完整。
echo   请重新解压，确认里面有一个 runtime 目录（里面有 pythonw.exe）。
echo.
pause
endlocal
exit /b 1
