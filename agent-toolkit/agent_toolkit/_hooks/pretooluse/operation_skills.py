"""操作を起動の契機とするスキルが未起動のまま、その操作を実行した呼び出しへ警告する。

スキルの起動の契機（descriptionと常時規範の参照文）はセッション開始時に配送されるが、
その操作を実行する時点には未起動であることを示す手掛かりが無く、起動されないまま本文の規定が適用されない。
本判定は操作の時点で未起動を示し、スキルを起動して今回の操作を確かめ直させる。

判定の結論は警告とする。根拠は`agent-toolkit:writing-standards`の`references/claude-hooks.md`
「遮断・警告フックの成立条件」にある。判定の入力はツール名、コマンド文字列およびSkill起動の記録だけで
機械的に確定し、発火は呼び出し主体の文脈ごとに1回であるため費用が小さい。
遮断しない理由は、スキルの起動が常時規範で努力目標とされていることと、検索が複合コマンドに含まれると
遮断で全体の再実行を要し、失うターンの作業が大きいことである。

呼び出し主体は`agent_id.resolve_hook_agent_id`で区別し、メイン会話と`Agent`ツールのサブエージェントを
別の文脈として扱う。`agents_server`の委譲先は別のセッションとして自身の状態ファイルで判定する。
起動済みの記録はセッション状態キー`rules_context.OPERATION_SKILL_READY_KEY`へ、警告を返した時点と
PostToolUse(Skill)の起動の観測時点で書く。CodexのPostToolUseはSkillの起動を観測できないため、
Codexでは警告を返した時点の記録だけで同じ文脈の再警告を止める。

表へスキルを加える場合は、操作の判定関数とその正例・負例のテストを同じ変更単位で加える。
判定関数はツール名とツール入力だけから結果を確定し、作業の意味の解釈を要する条件を含めない。
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Callable, Sequence

from agent_toolkit._hooks import agent_id as _agent_id
from agent_toolkit._hooks import bash_command_parser as _bash_command_parser
from agent_toolkit._hooks import plugin_resources as _plugin_resources
from agent_toolkit._hooks import rules_context as _rules_context
from agent_toolkit._hooks.notice import _WARN_TAG
from agent_toolkit._hooks.notice import formatter as _notice_formatter
from agent_toolkit._hooks.pretooluse import shell_checks as _shell_checks
from agent_toolkit._hooks.session_state import read_state, update_state

_llm_notice = _notice_formatter("pretooluse")

_SEARCH_COMMANDS = frozenset({"rg", "find"})
_GREP_COMMANDS = frozenset({"grep", "egrep", "fgrep"})
_GREP_RECURSIVE_LONG_OPTIONS = frozenset({"--recursive", "--dereference-recursive"})
_GREP_RECURSIVE_SHORT_OPTIONS = frozenset("rR")
_GREP_SHORT_OPTIONS_WITH_VALUE = frozenset("efmABCDd")
"""値を取る`grep`の短縮オプション。束ねた短縮オプションの中でこの文字より後ろは値として読む。"""


@dataclasses.dataclass(frozen=True)
class OperationSkill:
    """操作を起動の契機とするスキルと、その操作の判定関数の組。"""

    skill_name: str
    short_name: str
    operation: str
    matches: Callable[[str, dict], bool]


def _grep_is_recursive(arguments: Sequence[str]) -> bool:
    """`grep`系の引数列が再帰オプションを持つかを返す。"""
    index = 0
    while index < len(arguments):
        token = arguments[index]
        index += 1
        if token == "--":
            return False
        if token.startswith("--"):
            if token.split("=", 1)[0] in _GREP_RECURSIVE_LONG_OPTIONS:
                return True
            continue
        if not token.startswith("-") or token == "-":
            continue
        for position, char in enumerate(token[1:], start=1):
            if char in _GREP_RECURSIVE_SHORT_OPTIONS:
                return True
            if char in _GREP_SHORT_OPTIONS_WITH_VALUE:
                # 値を同じトークンへ連結しない場合は次のトークンが値になる。
                if position == len(token) - 1:
                    index += 1
                break
    return False


def _is_search_segment(segment: _bash_command_parser.ExecutionSegment) -> bool:
    """パイプラインの先頭区間が、リポジトリまたはディレクトリを検索するコマンドかを返す。"""
    if not segment.resolved or not segment.tokens:
        return False
    name = pathlib.PurePath(segment.tokens[0]).name
    if name in _SEARCH_COMMANDS:
        return True
    if name in _GREP_COMMANDS:
        return _grep_is_recursive(segment.tokens[1:])
    subcommand = _shell_checks._git_subcommand_tokens(segment)  # pylint: disable=protected-access
    return subcommand is not None and subcommand[0] == "grep"


def _is_search_operation(tool_name: str, tool_input: dict) -> bool:
    """`agent-toolkit:search`の起動の契機とする検索の呼び出しかを返す。

    パイプラインの2番目以降の区間は標準入力を検索するため対象から外す。
    """
    if tool_name in {"Grep", "Glob"}:
        return True
    if tool_name != "Bash":
        return False
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    return any(_is_search_segment(pipeline[0]) for pipeline in _bash_command_parser.extract_execution_pipelines(command))


OPERATION_SKILLS: tuple[OperationSkill, ...] = (OperationSkill("agent-toolkit:search", "search", "検索", _is_search_operation),)
"""操作を起動の契機とするスキルの表。"""


def canonical_skill_name(skill_name: object) -> str | None:
    """Skill起動の名前が表のスキルを指す場合に、その完全名を返す。"""
    if not isinstance(skill_name, str):
        return None
    for entry in OPERATION_SKILLS:
        if skill_name in {entry.skill_name, entry.short_name}:
            return entry.skill_name
    return None


def _mark_ready(skill_name: str, agent: str) -> Callable[[dict], dict | None]:
    def _mutator(state: dict) -> dict | None:
        recorded = state.get(_rules_context.OPERATION_SKILL_READY_KEY)
        recorded = dict(recorded) if isinstance(recorded, dict) else {}
        agents = recorded.get(skill_name)
        agents = list(agents) if isinstance(agents, list) else []
        if agent in agents:
            return None
        recorded[skill_name] = [*agents, agent]
        state[_rules_context.OPERATION_SKILL_READY_KEY] = recorded
        return state

    return _mutator


def _is_ready(session_id: str, skill_name: str, agent: str) -> bool:
    recorded = read_state(session_id).get(_rules_context.OPERATION_SKILL_READY_KEY)
    if not isinstance(recorded, dict):
        return False
    agents = recorded.get(skill_name)
    return isinstance(agents, list) and agent in agents


def record_skill_ready(session_id: str, skill_name: object, agent: str) -> None:
    """表のスキルの起動を、呼び出し主体の文脈について起動済みとして記録する。"""
    canonical = canonical_skill_name(skill_name)
    if canonical is None or not session_id:
        return
    update_state(session_id, _mark_ready(canonical, agent))


def _warning(entry: OperationSkill, *, is_codex: bool) -> str:
    short_name = entry.short_name
    if is_codex:
        start = f"{_plugin_resources.skill_reference(short_name, 'SKILL.md')}を読み"
    else:
        start = f"ツール`Skill`で`{entry.skill_name}`を起動し"
    return _llm_notice(
        f"`{entry.skill_name}`を起動しないまま{entry.operation}を実行した。",
        tag=_WARN_TAG,
        fix=(
            f"{start}、同スキルの基準で今回の{entry.operation}の手段と範囲を確かめ直す。"
            f"基準に合わない{entry.operation}の結果は使わず、基準に合う{entry.operation}でやり直す。"
        ),
        removable_cause=False,
    )


def operation_skill_warnings(payload: dict, tool_name: str, tool_input: dict, session_id: str, *, is_codex: bool) -> list[str]:
    """未起動のスキルの操作を実行する呼び出しへ、文脈ごとに1回だけ警告本文を返す。

    警告を返した時点でその文脈のそのスキルを起動済みとして記録する。
    `session_id`が無い呼び出しは記録できず文脈ごとの1回を保てないため、判定しない。
    """
    if not session_id:
        return []
    agent = _agent_id.resolve_hook_agent_id(payload)
    warnings: list[str] = []
    for entry in OPERATION_SKILLS:
        if _is_ready(session_id, entry.skill_name, agent) or not entry.matches(tool_name, tool_input):
            continue
        # 並行する呼び出しが同時に判定しても、記録を書き込んだ1件だけが警告する。
        if update_state(session_id, _mark_ready(entry.skill_name, agent)):
            warnings.append(_warning(entry, is_codex=is_codex))
    return warnings
