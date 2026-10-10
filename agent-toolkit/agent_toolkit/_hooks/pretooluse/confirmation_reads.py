"""確認の前に読む資料を読まないまま`AskUserQuestion`を呼んだメインへ警告する。

`agent-toolkit:user-confirmation-and-report`の読込表は、確認要否を判定する前と選択肢を組む前に
`references/approval-scope.md`と`references/choice-construction.md`を全文読むと定める。
確認を組む時点はスキルの起動から離れ、読込表の行が想起されないまま確認が発行された。
本判定は最後の会話圧縮より後の対象資料の読取操作と成功結果を対応付け、読んだ内容の理解は判定しない。

判定の結論は警告とし、遮断せず反復しても昇格させない。`AskUserQuestion`の入力はトークン数が多いことが多く、
遮断して再発行させる損失が大きいためである（2026年10月6日、ユーザーの確認回答）。
根拠は`agent-toolkit:writing-standards`の`references/claude-hooks-block-warn.md`「遮断と警告の選択」にある。
呼出主体がメインでない場合とtranscriptを読めない場合は判定しない。
"""

from __future__ import annotations

import json
import pathlib

from agent_toolkit._common import transcript as _transcript
from agent_toolkit._hooks import agent_id as _agent_id
from agent_toolkit._hooks import plugin_resources as _plugin_resources
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._hooks.notice import _WARN_TAG
from agent_toolkit._hooks.notice import formatter as _notice_formatter
from agent_toolkit._hooks.pretooluse.large_reads import bash_read_paths

_llm_notice = _notice_formatter("pretooluse")

_SKILL = "user-confirmation-and-report"
_REFERENCES = ("references/approval-scope.md", "references/choice-construction.md")


def _read_references(lines: list[str], *, cwd: str = "") -> set[str]:
    """圧縮より後の対象資料の読取操作と成功結果を、呼出IDで対応付ける。"""
    targets = {
        reference: (pathlib.Path(__file__).resolve().parents[3] / "skills" / _SKILL / reference).resolve()
        for reference in _REFERENCES
    }
    pending: dict[str, set[str]] = {}
    found: set[str] = set()
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict) or not _transcript_scan.entry_in_scan_scope(entry, include_sidechain=False):
            continue
        if entry.get("type") == "system" and entry.get("subtype") == "compact_boundary":
            pending.clear()
            found.clear()
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else ():
            if not isinstance(block, dict):
                continue
            if entry.get("type") == "user" and block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id")
                if not isinstance(tool_id, str):
                    continue
                reference_set = pending.pop(tool_id, set())
                if block.get("is_error") is not True:
                    found.update(reference_set)
            elif entry.get("type") == "assistant" and block.get("type") == "tool_use":
                tool_id = block.get("id")
                tool_input = block.get("input")
                if not isinstance(tool_id, str) or not isinstance(tool_input, dict):
                    continue
                paths: list[pathlib.Path] = []
                if block.get("name") == "Read" and isinstance(tool_input.get("file_path"), str):
                    candidate = pathlib.Path(tool_input["file_path"]).expanduser()
                    if candidate.is_absolute():
                        paths.append(candidate)
                elif block.get("name") == "Bash" and isinstance(tool_input.get("command"), str):
                    base = entry.get("cwd", cwd)
                    paths.extend(
                        path
                        for group in bash_read_paths(
                            tool_input["command"], base if isinstance(base, str) else cwd, include_partial=True
                        )
                        for path in group
                    )
                matched = {
                    reference for reference, target in targets.items() if any(path.resolve() == target for path in paths)
                }
                if matched:
                    pending[tool_id] = matched
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
    cwd = payload.get("cwd")
    read = _read_references(lines, cwd=cwd if isinstance(cwd, str) else "")
    unread = [reference for reference in _REFERENCES if reference not in read]
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
