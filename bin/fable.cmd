@echo off
rem NOTE: ‘Î‰‚·‚é Linux ”Å ¨ bin/fable
set CLAUDE_CODE_SUBAGENT_MODEL=claude-sonnet-5
claude --permission-mode=auto --model=fable --fallback-model="claude-opus-4-7[1m]" --effort=low %*
