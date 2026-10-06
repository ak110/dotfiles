"""確認の前に読む資料を読まないまま`AskUserQuestion`を呼んだメインへ警告する。

`agent-toolkit:user-confirmation-and-report`の読込表は、確認要否を判定する前と選択肢を組む前に
`references/approval-scope.md`と`references/choice-construction.md`を全文読むと定める。
確認を組む時点はスキルの起動から離れ、読込表の行が想起されないまま確認が発行された。
本判定はtranscriptの最後の会話圧縮より後に2資料の読取の操作があるかだけを確かめ、読んだ内容の理解は判定しない。

判定の結論は警告とし、遮断せず反復しても昇格させない。`AskUserQuestion`の入力はトークン数が多いことが多く、
遮断して再発行させる損失が大きいためである（2026年10月6日、ユーザーの確認回答）。
根拠は`agent-toolkit:writing-standards`の`references/claude-hooks.md`「遮断・警告フックの成立条件」にある。
呼出主体がメインでない場合とtranscriptを読めない場合は判定しない。
"""

from __future__ import annotations

import json

from agent_toolkit._hooks import agent_id as _agent_id
from agent_toolkit._hooks import plugin_resources as _plugin_resources
from agent_toolkit._hooks import transcript as _transcript
from agent_toolkit._hooks.notice import _WARN_TAG
from agent_toolkit._hooks.notice import formatter as _notice_formatter

_llm_notice = _notice_formatter("pretooluse")

_SKILL = "user-confirmation-and-report"
_REFERENCES = ("references/approval-scope.md", "references/choice-construction.md")


def _is_compact_boundary(line: str) -> bool:
    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(entry, dict) and entry.get("type") == "system" and entry.get("subtype") == "compact_boundary"


def _read_references(lines: list[str]) -> set[str]:
    """最後の会話圧縮より後に`Read`か`Bash`で読取を行った資料の相対パスを返す。"""
    start = max((index + 1 for index, line in enumerate(lines) if _is_compact_boundary(line)), default=0)
    found: set[str] = set()
    for _, block in _transcript.iter_assistant_content_blocks(lines[start:]):
        if block.get("type") != "tool_use":
            continue
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            continue
        name = block.get("name")
        for reference in _REFERENCES:
            if name == "Read":
                file_path = tool_input.get("file_path")
                if isinstance(file_path, str) and file_path.replace("\\", "/").endswith(f"{_SKILL}/{reference}"):
                    found.add(reference)
            elif name == "Bash":
                command = tool_input.get("command")
                if isinstance(command, str) and reference.rsplit("/", 1)[-1] in command:
                    found.add(reference)
    return found


def unread_reference_warning(payload: dict, tool_name: str) -> str | None:
    """メインが2資料のどちらかを読まずに`AskUserQuestion`を呼んだ場合に警告本文を返す。"""
    if tool_name != "AskUserQuestion" or not _agent_id.is_main_agent_context(payload):
        return None
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    lines = _transcript.read_transcript_lines(transcript_path)
    if lines is None:
        return None
    unread = [reference for reference in _REFERENCES if reference not in _read_references(lines)]
    if not unread:
        return None
    shown = "、".join(_plugin_resources.skill_reference(_SKILL, reference) for reference in unread)
    return _llm_notice(
        f"確認の前に全文読む資料（{shown}）を読まないまま`AskUserQuestion`を呼んだ。",
        tag=_WARN_TAG,
        fix=(
            "回答を受け取ったら、回答に依存する操作へ進む前にその資料を全文読み、発行した質問の前提と選択肢が同書の規定を満たすか確かめる。"
            "満たさない点があれば、その点をユーザーへ伝えてから回答を扱う。以後の確認はその資料を読んでから組む。"
        ),
        removable_cause=True,
        escalate_on_repeat=False,
    )
