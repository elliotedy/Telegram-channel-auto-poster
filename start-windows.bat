@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Virtual environment not found. Running setup-windows.bat first...
    call setup-windows.bat
)
if not exist .env (
    echo Error: .env file not found. Copy .env.example to .env and configure it.
    pause
    exit /b 1
)
:loop
echo Starting Telegram Auto Poster...
.venv\Scripts\python.exe main.py
if errorlevel 1 (
    echo Bot stopped with an error.
    pause
    exit /b 1
)
echo Restarting in 2 seconds... Press Ctrl+C to stop.
ping 127.0.0.1 -n 3 >nul
goto loop
