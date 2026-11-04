@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%~dp0workbench\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] 找不到运行环境：%PY%
    pause
    exit /b 1
)
echo ============================================================
echo   淘金币任务 · 当前页模式
echo.
echo   使用前请先在手机上手动操作：
echo     1. 淘宝 -^> 领淘金币 -^> 去赚金币
echo     2. 滑到底部点一次【展开】
echo     3. 手机放桌上不要碰
echo.
echo   本模式不做任何导航，只识别并执行当前屏幕上的任务。
echo ============================================================
echo.
"%PY%" -u "%~dp0run_coin.py" here %*
echo.
echo 结束。
pause
