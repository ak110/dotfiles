@echo off
rem NOTE: ‘Î‰‚·‚é Linux ”Å ¨ bin/opus
call claude --permission-mode=auto --model="opus[1m]" %*
set RC=%ERRORLEVEL%
call "%~dp0c.cmd"
exit /b %RC%
