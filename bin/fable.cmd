@echo off
rem NOTE: ‘Î‰‚·‚éLinux”Å ¨ bin/fable
call "%~dp0_claude-wrapper.cmd" --permission-mode=auto --model=fable --fallback-model="claude-opus-4-7[1m]" --effort=low %*
