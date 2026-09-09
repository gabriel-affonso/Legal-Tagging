@echo off
rem Execute the source in this checkout, instead of an older installed package.
setlocal
set "PROJECT_DIR=%~dp0.."
set "PYTHONPATH=%PROJECT_DIR%\src;%PYTHONPATH%"
"%PROJECT_DIR%\.venv\Scripts\python.exe" -m doc_register %*
exit /b %ERRORLEVEL%
