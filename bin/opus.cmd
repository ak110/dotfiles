@echo off
rem NOTE: ‘Î‰‚·‚éLinux”Å ¨ bin/opus
call "%~dp0_claude-wrapper.cmd" --permission-mode=auto --model="opus[1m]" %*
