@echo off
chcp 65001 >nul
REM Work Hunter launcher: venv + browser + UI on 127.0.0.1:8787
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [Work Hunter] Нет .venv. Создаю окружение...
  python -m venv .venv
  call ".venv\Scripts\python.exe" -m pip install -e ".[browser,ui]"
  call ".venv\Scripts\python.exe" -m playwright install chromium
)
echo [Work Hunter] Проверка окружения...
call ".venv\Scripts\python.exe" -m work_hunter --root . doctor
echo.
echo [Work Hunter] Открываю http://127.0.0.1:8787 ...
start "" "http://127.0.0.1:8787"
call ".venv\Scripts\python.exe" -m work_hunter --root . ui --host 127.0.0.1 --port 8787
pause
