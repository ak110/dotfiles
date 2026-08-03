@echo off
rem NOTE: ‘Î‰‚·‚éLinux”Å ¨ bin/sonnet
call "%~dp0_claude-wrapper.cmd" --permission-mode=auto --model="sonnet[1m]" %*
