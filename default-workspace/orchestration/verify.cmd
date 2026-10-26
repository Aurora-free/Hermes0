@echo off
setlocal
set "PY=C:\Users\22649\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0run.py" init
"%PY%" "%~dp0run.py" preflight
"%PY%" "%~dp0tests\test_orchestration.py"
exit /b %errorlevel%
