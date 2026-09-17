@echo off
chcp 65001 >nul
setlocal EnableExtensions
REM Work Hunter launcher: venv -> doctor -> UI (host/port из .work-hunter config)
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [Work Hunter] Нет .venv. Создаю окружение...
  python -m venv .venv
  call "%PY%" -m pip install -e ".[browser,ui]"
  call "%PY%" -m playwright install chromium
)

set "UI_HOST="
set "UI_PORT="
for /f "delims=" %%s in ('""%PY%" "%~dp0scripts\ui-endpoint.py""') do set "UI_ENDPOINT=%%s"
for /f "tokens=1,2" %%a in ("%UI_ENDPOINT%") do (
  set "UI_HOST=%%a"
  set "UI_PORT=%%b"
)
if not defined UI_HOST set "UI_HOST=127.0.0.1"
if not defined UI_PORT set "UI_PORT=8787"

echo [Work Hunter] Проверка окружения...
call "%PY%" -m work_hunter --root . doctor
echo.
echo [Work Hunter] Сервер: http://%UI_HOST%:%UI_PORT%
echo [Work Hunter] Ctrl+C или закрытие этого окна останавливает сервер.
echo.

REM Браузер откроется сам, как только сервер начнёт отвечать.
start "" /b "%PY%" "%~dp0scripts\open-when-ready.py" "http://%UI_HOST%:%UI_PORT%" 60

call "%PY%" -m work_hunter --root . ui --host %UI_HOST% --port %UI_PORT%