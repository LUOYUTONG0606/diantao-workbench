@echo off
setlocal
cd /d "%~dp0"
set "TAOBAO_VISION_BACKEND=workbuddy"
call run_taobao_energy.cmd run --repeat 30 %*
