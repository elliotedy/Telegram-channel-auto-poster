@echo off
setlocal
echo === Setting up Telegram Auto Poster on Windows ===
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH.
    pause
    exit /b 1
)

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
)

echo Installing dependencies...
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt

if not exist .env (
    if exist .env.example (
        echo Copying .env.example to .env...
        copy .env.example .env
        echo Please edit .env with your bot token and ADMIN_IDS.
    )
)

echo === Setup completed successfully! ===
pause
