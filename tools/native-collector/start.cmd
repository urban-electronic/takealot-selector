@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 launcher.py %*
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python 3.10 or newer is required. Install Python, then double-click start.cmd again.
    echo https://www.python.org/downloads/windows/
  ) else (
    python launcher.py %*
  )
)
pause
