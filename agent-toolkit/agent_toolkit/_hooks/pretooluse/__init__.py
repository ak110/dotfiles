"""PreToolUse統合フックの責務別実装。"""

from agent_toolkit._hooks.pretooluse import agent_checks as _agent_checks
from agent_toolkit._hooks.pretooluse import content_checks as _content_checks
from agent_toolkit._hooks.pretooluse import dispatch as _dispatch
from agent_toolkit._hooks.pretooluse import git_checks as _git_checks
from agent_toolkit._hooks.pretooluse import notices as _notices
from agent_toolkit._hooks.pretooluse import shell_checks as _shell_checks

_MODULES = (_notices, _dispatch, _content_checks, _git_checks, _shell_checks, _agent_checks)

for _target in _MODULES:
    for _source in _MODULES:
        for _name, _value in vars(_source).items():
            if not _name.startswith("__"):
                vars(_target).setdefault(_name, _value)

main = _dispatch.main

__all__ = ["main"]
