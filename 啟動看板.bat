@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 每日看板（關掉這個視窗就會關閉看板）

if exist ".venv\Scripts\python.exe" goto run

echo 第一次執行，正在準備環境，請稍等...
set "PY="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
if not defined PY goto nopy
"%PY%" -m venv .venv
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto pipfail

:run
".venv\Scripts\python.exe" server.py
pause
exit /b

:nopy
echo 找不到 Python，請先安裝 Python 3：https://www.python.org/downloads/
pause
exit /b 1

:pipfail
echo 套件安裝失敗，請確認網路連線後再試一次。
rmdir /s /q .venv
pause
exit /b 1
