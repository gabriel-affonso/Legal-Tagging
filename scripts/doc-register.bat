@echo off
rem Execute the source in this checkout, instead of an older installed package.
setlocal
for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "PYTHONPATH=%PROJECT_DIR%\src;%PYTHONPATH%"

if exist "%PROJECT_DIR%\.venv\Scripts\python.exe" (
    goto :dotvenv
)
if exist "%PROJECT_DIR%\venv\Scripts\python.exe" (
    goto :venv
)

where py >nul 2>nul
if not errorlevel 1 (
    goto :py
)
where python >nul 2>nul
if not errorlevel 1 (
    goto :python
)

echo Python was not found. Create .venv or install Python 3.11+ and try again.
exit /b 1

:dotvenv
"%PROJECT_DIR%\.venv\Scripts\python.exe" -m doc_register %*
exit /b %ERRORLEVEL%

:venv
"%PROJECT_DIR%\venv\Scripts\python.exe" -m doc_register %*
exit /b %ERRORLEVEL%

:py
py -3 -m doc_register %*
exit /b %ERRORLEVEL%

:python
python -m doc_register %*
exit /b %ERRORLEVEL%
