@echo off
setlocal
pushd "%SystemDrive%\"
if errorlevel 1 exit /b 1
uv run --no-cache --no-config --no-project --python 3.15 python "%~dp0atk-hook" %*
set "result=%ERRORLEVEL%"
popd
exit /b %result%
