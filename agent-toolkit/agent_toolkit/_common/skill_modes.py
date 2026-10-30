"""スキルの先頭語からモードを選び、自由記述の残りを保持する。"""

import re

_MODES = {"process-wi": {"single-lane", "all-repos"}, "session-review": {"batch"}}


def split_mode(skill: str, arguments: str | None) -> tuple[str | None, str | None]:
    """先頭語が完全一致する場合だけモードとして消費する。"""
    match = re.match(r"\s*(\S+)(?:\s+|$)", arguments or "")
    if match is None or match[1] not in _MODES.get(skill.removeprefix("agent-toolkit:"), set()):
        return None, arguments
    return match[1], arguments[match.end() :] if arguments is not None else None


def invocation_scope(skill: str, arguments: str | None) -> str:
    """自由記述の違いに依存しない起動単位を返す。"""
    mode, _ = split_mode(skill, arguments)
    return f"{skill} {mode}" if mode else skill
