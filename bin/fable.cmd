@echo off
rem NOTE: 対応するLinux版 → bin/fable
set CLAUDE_CODE_SUBAGENT_MODEL=claude-sonnet-5
claude --model=fable --permission-mode=auto --append-system-prompt="作業の実行は、原則として適切な粒度でサブエージェントに明確な実行手順を与えて委譲すること。メインは全体進行の俯瞰と立案を行う。自己判断による例外は認める" %*
