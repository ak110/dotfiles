"""スキルの先頭語からモードを選び、自由記述の残りを保持する。

入口スキル`atk-mode`の起動（`atk-mode <作業> [入力]`）は、作業の名前を持つスキルの起動へ読み替える。
作業の名前は移行前のスキルの名前と同じであり、保存済みの値（WIの`source`と`scope`、セッション状態）を変えない。
"""

import re

_MODES = {"process-wi": {"single-lane", "all-repos"}, "review-session": {"batch"}}

ENTRY_SKILL = "atk-mode"
ENTRY_WORKS = ("add-awi-by-user", "process-wi", "review-session", "export-session")
"""入口が受け付ける、ユーザーが名指しする作業の名前。"""

PROCESS_WI_LAUNCH_MARKER = f"スキル`agent-toolkit:{ENTRY_SKILL}`を作業`process-wi"
"""子セッションの目的文のうち、作業`process-wi`の起動を表す部分。会話記録から起動を判別する側も使う。"""


def entry_launch(scope: str, *, free_text: bool) -> str:
    """子セッションの目的文へ書く、入口を作業と引数で起動させる文を返す。

    スキルは入力の先頭でしか展開されず、目的文は`atk-auto`要素で包まれるため、先頭の`/atk-mode`ではなく文で起動させる。
    `free_text`は、起動入力の自由記述を引数の後ろへ続けさせる場合に真にする。
    """
    tail = "（起動入力の`free_text`が空でなければ引数の後ろへ続ける）" if free_text else ""
    return f"スキル`agent-toolkit:{ENTRY_SKILL}`を作業`{scope}`で起動して始める{tail}。"


def resolve_work(skill: str, arguments: str | None) -> tuple[str, str | None]:
    """入口の起動を`agent-toolkit:<作業>`と残りの引数へ読み替える。入口以外と作業名の無い起動はそのまま返す。

    認可を伴う状態（AWI投入、自律モード）を立てる呼び出し元は、ユーザーの入力と起動入力に限って使う。
    エージェントによる入口の起動の引数へ適用すると、エージェントが自分の判断で認可を得られる。
    """
    if skill.removeprefix("agent-toolkit:") != ENTRY_SKILL:
        return skill, arguments
    match = re.match(r"\s*(\S+)(?:\s+|$)", arguments or "")
    if match is None or match[1] not in ENTRY_WORKS:
        return skill, arguments
    return f"agent-toolkit:{match[1]}", (arguments or "")[match.end() :]


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


def accepted_scopes(skill: str, arguments: str | None) -> tuple[str, ...]:
    """保存済みUWIを取り込む起動単位を返す。新規保存はinvocation_scopeを使う。"""
    scope = invocation_scope(skill, arguments)
    legacy = {
        "agent-toolkit:review-session": ("agent-toolkit:session-review",),
        "agent-toolkit:review-session batch": (
            "agent-toolkit:session-review batch",
            "agent-toolkit:session-review-batch",
        ),
    }
    return (scope, *legacy.get(scope, ()))
