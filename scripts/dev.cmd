@echo off
"%~dp0..\.venv\Scripts\python.exe" "%~dp0dev.py" %*
set "harnessDevExit=%errorlevel%"
if not "%harnessDevExit%"=="0" pause
exit /b %harnessDevExit%
