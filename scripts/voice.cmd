@echo off
pushd "%~dp0.." || exit /b 1
".venv\Scripts\python.exe" -m harness.voice %*
set "harnessVoiceExit=%errorlevel%"
popd
exit /b %harnessVoiceExit%
