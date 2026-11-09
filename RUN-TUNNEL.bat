@echo off
title ECHO Radar - Public Tunnel
cd /d "%~dp0"
echo ==============================================================
echo  ECHO Radar / Opening the sonar to the internet (trycloudflare)
echo  RUN-SERVER.bat must be running in another window!
echo  The address for your phone will appear below.
echo ==============================================================
py echo_tunnel.py
echo.
echo Tunnel stopped. You can close this window.
pause
