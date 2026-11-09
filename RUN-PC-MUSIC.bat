@echo off
title ECHO Radar - PC Music
cd /d "%~dp0"
echo ==============================================================
echo  ECHO Radar / Broadcasting Windows music to the air (SMTC)
echo  Run RUN-SERVER.bat in another window first!
echo  Stop: close this window or press Ctrl+C
echo ==============================================================
py echo_pc.py
echo.
echo Node stopped. You can close this window.
pause
