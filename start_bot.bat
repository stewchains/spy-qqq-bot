@echo off
REM Windows: double-click to start the bot. Keeps the PC awake while it runs is up to your power settings.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python run_bot.py
pause
