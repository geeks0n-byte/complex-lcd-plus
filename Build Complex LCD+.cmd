@echo off
cd /d "%~dp0src"
python tools\build.py 2>nul
echo.
echo Latest build: %~dp0ComplexLCDPlus.face
pause
