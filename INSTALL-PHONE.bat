@echo off
title ECHO - Install on phone
cd /d "%~dp0"

set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
set "APK=%~dp0echo-radar-debug.apk"

if not exist "%APK%" (
  echo Положи echo-radar-debug.apk рядом с этим батником.
  pause
  exit /b 1
)
if not exist "%ADB%" (
  echo Не найден adb: %ADB%
  pause
  exit /b 1
)

echo Подключи телефон по USB и включи "Отладка по USB".
"%ADB%" devices
echo.
echo Устанавливаю ЭХО...
"%ADB%" install -r "%APK%"
echo.
echo Готово. Телефон и ПК должны быть в одной Wi-Fi сети.
echo В приложении: "Открыться для музыки" ^> Сервер ^> http://IP-ПК:8080
echo IP компьютера: ipconfig ^> строка "IPv4-адрес".
pause
