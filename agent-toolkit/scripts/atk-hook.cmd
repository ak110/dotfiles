@echo off
uv run --no-config --no-project --python 3.15 python "%~dp0atk-hook" %*
exit /b %ERRORLEVEL%
