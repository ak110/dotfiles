@echo off
rem NOTE: ëŒâûÇ∑ÇÈ Linux î≈ Å® bin/fable
set CLAUDE_CODE_SUBAGENT_MODEL=claude-sonnet-5
call claude --permission-mode=auto --model=fable --fallback-model="claude-opus-4-7[1m]" --effort=low %*
set RC=%ERRORLEVEL%
call "%~dp0c.cmd"
exit /b %RC%
