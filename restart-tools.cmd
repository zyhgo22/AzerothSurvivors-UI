@echo off
rem ============================================================
rem  只重启后台（数据大脑 + 悬浮层），游戏不用关
rem ============================================================
setlocal
if not exist "%~dp0logs" mkdir "%~dp0logs"

rem ---- 先停掉上一轮（按 pid 文件精确停止，不用 taskkill /IM 误伤） ----
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

rem ---- 启动数据大脑 ----
start "" "%AZSPY%" "%~dp0brain.py"

rem ---- 启动悬浮层 ----
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0overlay.ps1" > "%~dp0logs\overlay.out" 2>&1

endlocal
exit /b 0

:nopython
echo.
echo   没找到可用的 Python 运行时，后台没有启动。
echo   请确认文件夹里有 runtime 目录（里面有 pythonw.exe）。
echo.
pause
endlocal
exit /b 1
