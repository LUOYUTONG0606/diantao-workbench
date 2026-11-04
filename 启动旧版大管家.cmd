@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0workbench\.venv\Scripts\python.exe" (
    echo Please run 启动大管家.cmd once to prepare the environment.
    pause
    exit /b 1
)
set "PYTHON_HOME="
for /f "tokens=1,* delims==" %%A in ('findstr /b "home =" "%~dp0workbench\.venv\pyvenv.cfg"') do set "PYTHON_HOME=%%B"
for /f "tokens=* delims= " %%A in ("%PYTHON_HOME%") do set "PYTHON_HOME=%%A"
set "PYTHONPATH=%~dp0workbench\.venv\Lib\site-packages;%~dp0;%PYTHONPATH%"
start "" "%PYTHON_HOME%\pythonw.exe" "%~dp0workbench\app.py"
exit /b 0
