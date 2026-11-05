@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

rem 手机连电脑移动热点时，Windows 不转发 mDNS，工作台扫码发现不到手机。
rem 本脚本按 IP 直连找回无线调试连接，端口变了也不用重新扫码配对。

set "PY=%~dp0workbench\.venv\Scripts\python.exe"
set "ADB=%~dp0.vendor\platform-tools\adb.exe"
if not exist "%PY%" set "PY=python"

echo.
echo ============================================================
echo   重连手机（无线调试 / 电脑移动热点）
echo ============================================================
echo.
"%PY%" -u "%~dp0tools\reconnect_phone.py" --adb "%ADB%"
echo.
if errorlevel 1 (
    echo [提示] 仍连不上可改用 USB 数据线 + 开发者选项「USB 调试」。
) else (
    echo [提示] 连上后请先回到淘宝首页，再双击「启动淘金币.cmd」。
)
echo.
pause
