@echo off
REM Launches the ccollab interactive wizard. Pass any args to skip the wizard
REM and use the legacy non-interactive flow (e.g. launch.bat --resume).
python "%~dp0launcher.py" %*
pause
