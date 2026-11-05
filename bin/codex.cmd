@echo off
setlocal
rem NOTE: ‘Î‰‚·‚é Linux ”Å ¨ bin/codex
uv run --frozen --project "%~dp0.." python "%~dp0..\libexec\codex_launcher.py" %*
exit /b %errorlevel%
