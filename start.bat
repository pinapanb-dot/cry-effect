@echo off
rem Cry Effect GUI launcher (drag a WAV or project JSON onto this file to open it)
"%~dp0.venv\Scripts\python.exe" "%~dp0app.py" %*
pause
