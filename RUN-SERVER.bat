@echo off
title ECHO Radar - Server
cd /d "%~dp0"
echo ==============================================================
echo  ECHO Radar / Server starting...
echo  The sonar address will appear below - open it on your phone.
echo  Stop: close this window or press Ctrl+C
echo ==============================================================
py echo_server.py
echo.
echo Server stopped. You can close this window.
pause
