@echo off
rem NOTE: ‘Î‰‚·‚éLinux”Å ¨ bin/sonnet
call claude --permission-mode=auto --model="sonnet[1m]" %*
set RC=%ERRORLEVEL%
call "%~dp0c.cmd"
exit /b %RC%
