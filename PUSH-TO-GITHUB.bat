@echo off
title ECHO - Push to GitHub
cd /d "%~dp0"

rem Git установлен портативно внутри WorkBuddy и не прописан в системный PATH.
rem Добавляем его только для этого окна.
set "PATH=%LOCALAPPDATA%\Programs\WorkBuddyAI\resources\vendor\PortableGit\cmd;%PATH%"

echo ==============================================================
echo  ЭХО - отправка кода в GitHub
echo  Репозиторий: kvazibuka-commits/echo-by-Hy4
echo ==============================================================
echo.

git status
if errorlevel 1 (
  echo.
  echo Git не найден. Проверь путь к PortableGit.
  pause
  exit /b 1
)

echo.
echo Отправляю ветку main...
echo СЕЙЧАС ОТКРОЕТСЯ ОКНО ВХОДА GITHUB - войди в свой аккаунт.
echo.
git push -u origin main

if errorlevel 1 (
  echo.
  echo ==============================================================
  echo  Пуш не удался. Если просили пароль - нужен Personal Access
  echo  Token (scope repo), а не пароль аккаунта.
  echo ==============================================================
) else (
  echo.
  echo ==============================================================
  echo  ГОТОВО. Открой вкладку Actions на GitHub:
  echo  https://github.com/kvazibuka-commits/echo-by-Hy4/actions
  echo ==============================================================
)
pause
