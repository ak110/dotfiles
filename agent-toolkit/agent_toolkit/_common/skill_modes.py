import re

_MODES = {"process-wi": {"single-lane", "all-repos"}, "review-session": {"batch", "meta", "research"}}

ENTRY_SKILL = "atk-mode"
ENTRY_WORKS = (
    "add-awi-by-user",
    "implement",
    "process-user-comments",
    "process-wi",
    "process-loop",
    "review-session",
    "export-session",
)
"""入口が受け付ける、ユーザーが名指しする作業の名前。"""

PROCESS_WI = "agent-toolkit:process-wi"
"""作業`process-wi`の修飾名。

入口の起動の読み替え先、`atk run-skill`の位置引数、起動入力の`skill`、WIの`scope`に保存する値であり、同名のスキルは無い。
"""

PROCESS_WI_LAUNCH_MARKER = f"スキル`agent-toolkit:{ENTRY_SKILL}`を作業`process-wi"
"""子セッションの目的文のうち、作業`process-wi`の起動を表す部分。セッション記録から起動を判別する側も使う。"""


def entry_launch(scope: str, *, free_text: bool) -> str:
    tail = "（起動入力の`free_text`が空でなければ引数の後ろへ続ける）" if free_text else ""
    return f"スキル`agent-toolkit:{ENTRY_SKILL}`を作業`{scope}`で起動して始める{tail}。"


def entry_work(skill: str) -> str | None:
    name = skill.removeprefix("agent-toolkit:")
    return name if name in ENTRY_WORKS else None


def resolve_work(skill: str, arguments: str | None) -> tuple[str, str | None]:
    if skill.removeprefix("agent-toolkit:") != ENTRY_SKILL:
        return skill, arguments
    match = re.match(r"\s*(\S+)(?:\s+|$)", arguments or "")
    if match is None or match[1] not in ENTRY_WORKS:
        return skill, arguments
    return f"agent-toolkit:{match[1]}", (arguments or "")[match.end() :]


def qualified(skill: str) -> str:
    """入口配下の作業の無修飾の名前を`agent-toolkit:<作業>`へそろえる。他の名前はそのまま返す。

    作業名を比べる箇所は、比べる両側を値の読み取り時にこの関数（起動単位は`invocation_scope`・`canonical_scope`）でそろえる。
    修飾名へそろえる前の`atk run-skill`は無修飾の作業名を実行記録へ保存した。
    再開したホストはその起動入力どおりの`scope`で報告を保存するため、保存済みの値は書き換えずに比べる側でそろえる。
    """
    return f"agent-toolkit:{skill}" if skill in ENTRY_WORKS else skill


def canonical_scope(scope: str) -> str:
    """保存済みの起動単位（報告用UWIの`scope`など）の先頭の作業名を`qualified`でそろえる。モード語などの後続は保つ。"""
    name, separator, rest = scope.partition(" ")
    return f"{qualified(name)}{separator}{rest}"


def entry_command(host: str, arguments: str) -> str:
    """ホストがユーザーの入力として受け付ける、入口`atk-mode`の起動の形を返す。Codexは`$`、それ以外は`/`で始める。"""
    return f"{'$' if host == 'codex' else '/'}{ENTRY_SKILL} {arguments}"


def split_mode(skill: str, arguments: str | None) -> tuple[str | None, str | None]:
    match = re.match(r"\s*(\S+)(?:\s+|$)", arguments or "")
    if match is None or match[1] not in _MODES.get(skill.removeprefix("agent-toolkit:"), set()):
        return None, arguments
    return match[1], arguments[match.end() :] if arguments is not None else None


def invocation_scope(skill: str, arguments: str | None) -> str:
    skill = qualified(skill)
    mode, _ = split_mode(skill, arguments)
    return f"{skill} {mode}" if mode else skill


def accepted_scopes(skill: str, arguments: str | None) -> tuple[str, ...]:
    scope = invocation_scope(skill, arguments)
    legacy = {
        "agent-toolkit:review-session": ("agent-toolkit:session-review",),
        "agent-toolkit:review-session batch": (
            "agent-toolkit:session-review batch",
            "agent-toolkit:session-review-batch",
        ),
    }
    candidates = (scope, *legacy.get(scope, ()))
    unqualified = (
        name.removeprefix("agent-toolkit:") + separator + rest
        for candidate in candidates
        for name, separator, rest in [candidate.partition(" ")]
        if entry_work(name) is not None and name.startswith("agent-toolkit:")
    )
    return (*candidates, *unqualified)
