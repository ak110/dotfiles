"""スキルの先頭語からモードを選び、自由記述の残りを保持する。

入口スキル`atk-mode`の起動（`atk-mode <作業> [入力]`）は、作業の名前を持つスキルの起動へ読み替える。
作業の名前は移行前のスキルの名前と同じであり、保存済みの値（WIの`source`と`scope`、セッション状態）を変えない。
"""

import re

_MODES = {"process-wi": {"single-lane", "all-repos"}, "review-session": {"batch", "meta"}}

ENTRY_SKILL = "atk-mode"
ENTRY_WORKS = ("add-awi-by-user", "implement", "process-wi", "review-session", "export-session")
"""入口が受け付ける、ユーザーが名指しする作業の名前。"""

PROCESS_WI = "agent-toolkit:process-wi"
"""作業`process-wi`の修飾名。

入口の起動の読み替え先、`atk run-skill`の位置引数、起動入力の`skill`、WIの`scope`に保存する値であり、同名のスキルは無い。
"""

PROCESS_WI_LAUNCH_MARKER = f"スキル`agent-toolkit:{ENTRY_SKILL}`を作業`process-wi"
"""子セッションの目的文のうち、作業`process-wi`の起動を表す部分。セッション記録から起動を判別する側も使う。"""


def entry_launch(scope: str, *, free_text: bool) -> str:
    """子セッションの目的文へ書く、入口を作業と引数で起動させる文を返す。

    スキルは入力の先頭でしか展開されず、目的文は`atk-auto`要素で包まれるため、先頭の`/atk-mode`ではなく文で起動させる。
    `free_text`は、起動入力の自由記述を引数の後ろへ続けさせる場合に真にする。
    """
    tail = "（起動入力の`free_text`が空でなければ引数の後ろへ続ける）" if free_text else ""
    return f"スキル`agent-toolkit:{ENTRY_SKILL}`を作業`{scope}`で起動して始める{tail}。"


def entry_work(skill: str) -> str | None:
    """入口が受け付ける作業の名前を、修飾名（`agent-toolkit:<作業>`）か接頭辞の無い名前から返す。それ以外はNone。"""
    name = skill.removeprefix("agent-toolkit:")
    return name if name in ENTRY_WORKS else None


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
    """先頭語が完全一致する場合だけモードとして消費する。"""
    match = re.match(r"\s*(\S+)(?:\s+|$)", arguments or "")
    if match is None or match[1] not in _MODES.get(skill.removeprefix("agent-toolkit:"), set()):
        return None, arguments
    return match[1], arguments[match.end() :] if arguments is not None else None


def invocation_scope(skill: str, arguments: str | None) -> str:
    """自由記述の違いに依存しない起動単位を返す。"""
    skill = qualified(skill)
    mode, _ = split_mode(skill, arguments)
    return f"{skill} {mode}" if mode else skill


def accepted_scopes(skill: str, arguments: str | None) -> tuple[str, ...]:
    """保存済みUWIを取り込む起動単位を返す。新規保存はinvocation_scopeを使う。

    更新前の`atk run-skill`は、指定された作業名を修飾せずにscopeへ保存した（`review-session`、`review-session batch`など）。
    作業名が入口の作業なら、無修飾の形も含める。
    """
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
