@echo off
REM Creates the Windows scheduled task that starts the 30-45 day swing bot at 8:00 AM every weekday.
schtasks /create /tn "SPY QQQ Swing Bot" /tr "C:\Users\stewc\spy-qqq-bot\start_bot_swing.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 08:00 /f
echo.
schtasks /query /tn "SPY QQQ Swing Bot"
echo.
pause
