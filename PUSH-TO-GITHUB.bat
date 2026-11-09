@echo off
title ECHO - Push to GitHub
cd /d "%~dp0"

rem Portable Git is not in the system PATH. Add it for this window only.
rem ASCII-only file: no chcp, no Cyrillic - cmd must not re-read it mid-run.
set "PG=%LOCALAPPDATA%\Programs\WorkBuddyAI\resources\vendor\PortableGit"
set "PATH=%PG%\cmd;%PG%\mingw64\bin;%PATH%"
set "GIT=%PG%\cmd\git.exe"

rem Windows Schannel cannot reach the certificate revocation server here.
rem OpenSSL backend keeps full certificate validation and fixes the TLS error.
"%GIT%" config http.sslBackend openssl >nul 2>&1

echo ==============================================================
echo  ECHO - pushing code to GitHub
echo  Repo: kvazibulka-commits/echo-by-Hy4
echo ==============================================================
echo.

"%GIT%" status
"%GIT%" remote -v
echo.
echo Pushing branch main...
echo A GITHUB SIGN-IN WINDOW WILL OPEN - log in and approve.
echo.
"%GIT%" push -u origin main
if errorlevel 1 goto FAILED

echo.
echo ==============================================================
echo  DONE. Open the Actions tab:
echo  https://github.com/kvazibulka-commits/echo-by-Hy4/actions
echo ==============================================================
goto END

:FAILED
echo.
echo ==============================================================
echo  PUSH FAILED.
echo  If a password was requested, use a GitHub Personal Access
echo  Token with scope repo - not your account password.
echo ==============================================================

:END
pause
