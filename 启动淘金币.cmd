@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

rem ================================================================
rem  淘金币任务 · 启动器（去赚金币 / 经典金币任务）
rem
rem  跑的是 淘金币任务.py：淘宝 -> 领淘金币 -> 去赚金币 -> 逐项做任务
rem  不重启淘宝：请先在手机上打开淘宝首页，再双击本文件。
rem  不启动点淘大管家，也不碰走路赚元宝等点淘任务。
rem
rem  注意：本脚本依赖 opencv（utils.py 需要），必须用 workbench 环境。
rem ================================================================

rem 本机有系统代理时，发往 127.0.0.1 的请求会被代理劫持成 502，绕开回环地址
set "NO_PROXY=127.0.0.1,localhost,::1"
set "no_proxy=127.0.0.1,localhost,::1"

set "PY=%~dp0workbench\.venv\Scripts\python.exe"
set "PY_OCR=%~dp0.venv-ocr\Scripts\python.exe"
set "ADB=%~dp0.vendor\platform-tools\adb.exe"

if not exist "%PY%" (
    echo [ERROR] 找不到运行环境：%PY%
    echo         请先双击一次「启动大管家.cmd」让它把环境装好。
    pause
    exit /b 1
)

rem ---------- 1. 确保 OCR 服务可用（没跑就自动拉起） ----------
echo.
if exist "%PY_OCR%" (
    "%PY_OCR%" -u "%~dp0tools\ensure_ocr.py" --port 8888
    if errorlevel 1 (
        echo.
        echo [ERROR] OCR 服务没能就绪，任务无法识别页面，已停止。
        pause
        exit /b 1
    )
) else (
    echo [WARN] 未找到 OCR 环境，跳过自动拉起；请确认 8888 端口有 OCR 服务
)

rem ---------- 1.5 手机没连上时，自动在热点网段找回无线调试连接 ----------
rem Windows 移动热点不转发 mDNS，工作台扫码在「手机连电脑热点」时找不到手机，
rem 这里按 IP 直连找回；已有可用设备时脚本会秒退，不干扰正常流程。
"%ADB%" devices | findstr /r /c:"device$" >nul
if errorlevel 1 (
    echo [自动重连] 未检测到手机，正在热点网段里查找无线调试端口...
    "%PY%" -u "%~dp0tools\reconnect_phone.py" --adb "%ADB%"
)

rem ---------- 2. 检查手机连接 ----------
set "COUNT=0"
set "DEVICE="
for /f "skip=1 tokens=1" %%D in ('"%ADB%" devices') do (
    if not "%%D"=="" (
        set /a COUNT+=1
        if not defined DEVICE set "DEVICE=%%D"
    )
)

if %COUNT%==0 (
    echo.
    echo [提示] 没有检测到手机。
    echo        1. 手机「开发者选项 - USB 调试」打开
    echo        2. 数据线连电脑，手机上点「允许 USB 调试」
    echo.
    pause
    exit /b 1
)
if %COUNT% GTR 1 (
    echo.
    echo [提示] 检测到 %COUNT% 个设备，脚本要求只连一台。
    echo        常见原因：无线调试同时开了 IP 和 mDNS 两条通道。
    echo        解决：adb disconnect 多余的地址，只保留一条。
    echo.
    pause
    exit /b 1
)

rem ---------- 3. 运行期间守护手机连接 ----------
rem 手机连接掉了，守护会自动「关开一次电脑热点 + 重连 adb」，不再需要手动处理。
rem 优先用 pythonw 后台运行：没有控制台窗口，不打扰任务日志。
set "PYW=%~dp0workbench\.venv\Scripts\pythonw.exe"
del /q "%~dp0.runtime\watch_phone.stop" >nul 2>&1
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0tools\watch_phone.py" --adb "%ADB%"
) else (
    start "手机连接守护" /min "%PY%" -u "%~dp0tools\watch_phone.py" --adb "%ADB%"
)

echo.
echo ============================================================
echo   淘金币任务（去赚金币）
echo   设备: %DEVICE%
echo   识别: 本机 OCR 127.0.0.1:8888
echo   请在手机淘宝首页保持不动，脚本会自动进入领淘金币
echo   中断: Ctrl+C
echo ============================================================
echo.

"%PY%" -u "%~dp0run_coin.py" run --max-tasks 30 %*

rem 让后台守护退出：写停止标志，守护下一轮检查到就自行结束
"%PY%" -c "from pathlib import Path; Path(r'%~dp0.runtime\watch_phone.stop').write_text('stop', encoding='utf-8')" >nul 2>&1

echo.
echo 任务结束。
pause
