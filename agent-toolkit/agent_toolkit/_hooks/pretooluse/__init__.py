"""PreToolUse統合フックの責務別実装。"""

import sys

from agent_toolkit._hooks import termination_evidence
from agent_toolkit._hooks.pretooluse import agent_checks as _agent_checks
from agent_toolkit._hooks.pretooluse import content_checks as _content_checks
from agent_toolkit._hooks.pretooluse import dispatch as _dispatch
from agent_toolkit._hooks.pretooluse import large_reads as _large_reads
from agent_toolkit._hooks.pretooluse import notices as _notices
from agent_toolkit._hooks.pretooluse import shell_checks as _shell_checks
from agent_toolkit._hooks.pretooluse import task_document_launch as _task_document_launch

_MODULES = (_notices, _dispatch, _content_checks, _large_reads, _shell_checks, _agent_checks, _task_document_launch)

for _target in _MODULES:
    for _source in _MODULES:
        for _name, _value in vars(_source).items():
            if not _name.startswith("__"):
                vars(_target).setdefault(_name, _value)


def main(payload_text: str) -> int:
    """元の操作の判定を保ち、呼び出しの開始位置を終了工程の供給先へ渡す。"""
    try:
        termination_evidence.observe_tool(payload_text, after=False)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"終了工程の開始位置を取得できない: {error}", file=sys.stderr)
    return _dispatch.main(payload_text)


__all__ = ["main"]
