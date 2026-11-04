@echo off
cd /d "%~dp0"

rem ---------------------------------------------------------------
rem 原始发行版这里写的是 `rtk proxy ...`，rtk 是作者本机的封装命令，
rem 没有打进发行包，所以在你机器上会报「rtk 不是内部或外部命令」。
rem 这里改成直接调用：已建好环境就用环境里的 python，否则交给 uv 现开。
rem ---------------------------------------------------------------

if not exist "%USERPROFILE%\.local\bin\uv.exe" goto :have_uv_check
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
:have_uv_check

if exist "%~dp0.venv-energy\Scripts\python.exe" (
    "%~dp0.venv-energy\Scripts\python.exe" -u tools\run_taobao_energy.py %*
    exit /b %ERRORLEVEL%
)

where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 首次运行需要 uv 来创建能量任务环境。
    echo         请安装后重试： https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)

uv run --no-project --python 3.12 python -u tools\run_taobao_energy.py %*
exit /b %ERRORLEVEL%
