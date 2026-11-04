@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "NO_PROXY=127.0.0.1,localhost,::1"
set "no_proxy=127.0.0.1,localhost,::1"
set "PY=%~dp0.venv-ocr\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] OCR 环境缺失：%PY%
  echo 请先在本目录执行：
  echo     uv venv .venv-ocr --python 3.12
  echo     uv pip install --python .venv-ocr\Scripts\python.exe rapidocr-onnxruntime fastapi uvicorn python-multipart pillow numpy
  pause
  exit /b 1
)
echo ============================================================
echo   本机 OCR 服务  http://127.0.0.1:8888
echo   做淘金币任务时请保持这个窗口开着
echo   关闭窗口 = 停止 OCR 服务
echo ============================================================
echo.
"%PY%" tools\ocr_rapid_server.py --host 127.0.0.1 --port 8888
echo.
echo OCR 服务已退出。
pause
