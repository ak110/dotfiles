@echo off
uv run --no-config --no-project --python 3 python "%~dp0atk-hook" %*
exit /b %ERRORLEVEL%
