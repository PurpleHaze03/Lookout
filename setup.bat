@echo off
rem ============================================================
rem  lookout one-shot setup (Windows)
rem  Creates a virtual environment, installs all dependencies,
rem  the headless browser, and your starter config files.
rem ============================================================
setlocal
cd /d "%~dp0"

echo.
echo  [1/5] Looking for Python...
where python >nul 2>nul
if %errorlevel%==0 (
    set "PY=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set "PY=py -3"
    ) else (
        echo  ERROR: Python not found. Install it from https://python.org
        echo         and tick "Add python.exe to PATH" during installation.
        pause
        exit /b 1
    )
)
%PY% --version

echo.
echo  [2/5] Creating virtual environment (.venv)...
if not exist ".venv" (
    %PY% -m venv .venv || (echo  ERROR: could not create venv & pause & exit /b 1)
) else (
    echo         .venv already exists - reusing it.
)

echo.
echo  [3/5] Installing lookout + dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -e . || (echo  ERROR: install failed & pause & exit /b 1)

echo.
echo  [4/5] Installing the headless browser (Chromium, one-time ~150 MB)...
".venv\Scripts\python.exe" -m playwright install chromium || (echo  WARNING: browser install failed - JS-heavy shops like AliExpress will not work until you run: .venv\Scripts\python -m playwright install chromium)

echo.
echo  [5/5] Creating starter config files...
if not exist ".env" (
    copy .env.example .env >nul
    echo         .env created  -^> fill in your Gmail address + app password!
) else (
    echo         .env already exists - not touched.
)
if not exist "watchlist.yaml" (
    copy watchlist.example.yaml watchlist.yaml >nul
    echo         watchlist.yaml created  -^> add the products you want to watch!
) else (
    echo         watchlist.yaml already exists - not touched.
)

echo.
echo  ============================================================
echo   Setup complete! Next steps:
echo.
echo     1. Edit .env            (notepad .env)
echo     2. Edit watchlist.yaml  (notepad watchlist.yaml)
echo     3. Activate the environment:
echo          .venv\Scripts\activate
echo     4. Test without sending mail:
echo          lookout check --dry-run
echo     5. Go live:
echo          lookout check
echo.
echo   Tip: run  lookout -h  to see all commands with examples.
echo  ============================================================
echo.
pause
endlocal
