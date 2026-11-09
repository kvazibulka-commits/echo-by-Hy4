@echo off
title ECHO - Emulator round-trip
cd /d "%~dp0"

set "SDK=%LOCALAPPDATA%\Android\Sdk"
set "ADB=%SDK%\platform-tools\adb.exe"
set "EMU=%SDK%\emulator\emulator.exe"
set "APK=%~dp0echo-radar-debug.apk"

if not exist "%APK%" (
  echo ==============================================================
  echo  Нет файла echo-radar-debug.apk рядом с этим батником.
  echo  Скачай артефакт из GitHub Actions и положи сюда.
  echo ==============================================================
  pause
  exit /b 1
)
if not exist "%ADB%" (
  echo Не найден adb: %ADB%
  pause
  exit /b 1
)

echo [1/6] Запускаю сервер ЭХО в отдельном окне...
start "ECHO Server" cmd /k "py echo_server.py"

echo [2/6] Запускаю эмулятор EchoTest...
start "ECHO Emulator" "%EMU%" -avd EchoTest -no-audio -gpu swiftshader

echo [3/6] Жду загрузки Android...
"%ADB%" wait-for-device
"%ADB%" shell "while [ -z $(getprop sys.boot_completed) ]; do sleep 1; done"

echo [4/6] Устанавливаю APK...
"%ADB%" install -r "%APK%"

echo [5/6] Открываю ЭХО...
"%ADB%" shell am start -n ru.echo.radar/.MainActivity

echo [6/6] Автоматический round-trip тест сервера...
py tests\roundtrip_test.py

echo.
echo ==============================================================
echo  В ЭМУЛЯТОРЕ (обязательно, UDP-маяк в эмуляторе не работает):
echo   1. Выбери "Открыться для музыки" (opt-in).
echo   2. Кнопка "Сервер" ^> введи:  http://10.0.2.2:8080
echo   3. Введи трек вручную ("Ввести трек вручную").
echo  НА КОМПЕ открой http://127.0.0.1:8080/app - увидишь трек.
echo  Отправь импульс из веб-сонара - на телефоне появится баннер.
echo  Это и есть связка в обе стороны.
echo ==============================================================
pause
