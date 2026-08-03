@echo off
rem NOTE: ëŒâûÇ∑ÇÈLinuxî≈ Å® bin/fable
set CLAUDE_CODE_SUBAGENT_MODEL=claude-sonnet-5
call "%~dp0_claude-wrapper.cmd" --permission-mode=auto --model=fable --fallback-model="claude-opus-4-7[1m]" --effort=low %*
