@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>&1
if errorlevel 1 (
    echo Please install uv first: https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)
uv run --no-project --python 3.11 --with "python-dotenv>=1,<2" python local_config.py --import-legacy --open
if errorlevel 1 pause
