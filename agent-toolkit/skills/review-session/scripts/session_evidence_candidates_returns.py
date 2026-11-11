"""証拠抽出の問題候補（session_evidence_candidates_returns）。"""

from __future__ import annotations

import collections
import contextlib
import json
import re
from typing import Any

import session_evidence_execution
from session_evidence_candidates_wi import _ADHOC_SHELL_TOOLS
from session_evidence_extract import _CollectedRecord
from session_evidence_tool_calls import _conversation_tool_calls, _record_tool_calls

from agent_toolkit._agents_server.wait_summary import WAIT_BODY_END_PREFIX, WAIT_BODY_START_PREFIX
from agent_toolkit._common import runtime_inserted as _runtime_inserted
from agent_toolkit._common.json_lines import split_lines
from agent_toolkit._common.shell_tokens import is_atk_command as _is_atk_command

# 委譲先の返却形式の欄名。日本語名へ改める前の英字の欄名（後者）で返す版の委譲先も同じ意味で読む。
_RETURN_STATUS_PREFIXES = ("状態:", "status:")


# 委譲先は続行できないことを`続行できない理由:`の行で表す。値が空の行（後続行へ列挙する形式の見出し）と、
# 省略時の値「なし」を書いた旧形式の行は続行不能を示さない。
_ESCALATION_REASON_PREFIX = "続行できない理由:"


_NO_ESCALATION_REASONS = frozenset({"", "なし"})


# 続行不能を状態値で示した旧形式の返却。過去のセッション記録に残るため読み取り互換として判定する。
_ESCALATION_RETURN_STATUS = "needs_escalation"


_COMPLETED_RETURN_LINES = frozenset(f"{prefix} completed" for prefix in _RETURN_STATUS_PREFIXES)


_UNRESOLVED_RETURN_PREFIXES = ("未解決の指摘数:", "unresolved:")


_ESCALATION_REASON_PREFIXES = (_ESCALATION_REASON_PREFIX, "reason:")


_DELEGATE_COMPLETION_VALUES = frozenset(
    {
        "計画作成完了",
        "計画なし準備完了",
        "計画検査完了",
        "実装完了",
        "対応完了",
        "対応完了（再レビュー不要）",
        "統合完了",
        "公開完了",
    }
)
"""`<役割名>.subagent.md`が成功の返却値として定める固定の先頭行。各値が`agent-toolkit/share/`の`<役割名>.subagent.md`に現れることをテストが確かめる。"""


_UNEXPECTED_EVENT_PREFIXES = ("想定外事象:", "想定外事象：")


_VERDICT_LINE = re.compile(r"^(?:#+\s*)?(?:\*\*)?\s*判定[^:：]{0,30}[:：]\s*(?:\*\*)?\s*(?P<value>\S.*)$")


_SHELL_DELEGATION_MARKER = "次のコマンドを実行し、結果を報告せよ。"
"""`agents_server`の`start`のshellが委譲先へ渡す指示本文の冒頭の文。値が同サーバーの指示本文と一致することをテストが確かめる。"""


_REPORTED_EXIT_CODE = re.compile(r"(?:終了コード|exit(?:[_ ]?code)?|(?<![A-Za-z])rc)[^0-9\n]{0,15}?(\d+)", re.IGNORECASE)


_REPORTED_NONZERO_COUNT = re.compile(
    r"(?:(?:failed|warnings?|diagnostics)[\"'`]*\s*[:=]\s*[1-9])|(?:(?:失敗|警告|診断)[^0-9\n]{0,10}?[1-9][0-9]*\s*件)",
    re.IGNORECASE,
)


def _delegate_notice_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """実際のwaitの結果で受信した改善点を、完了前も原記録の位置へ対応付ける。"""
    events = []
    for item in collected:
        calls = _record_tool_calls(item.records)
        wait_lines = {
            call.line
            for call in calls
            if call.tool.casefold().rsplit("__", 1)[-1].rsplit(".", 1)[-1] in _ADHOC_SHELL_TOOLS
            and any(
                _is_atk_command(list(simple.words)) and list(simple.words)[1:3] == ["agents", "wait"]
                for command in session_evidence_execution.command_inputs(call.tool, call.text)
                for simple in session_evidence_execution.shell_simple_commands(command)
            )
            or "agents_server" in call.tool
            and call.tool.endswith("__wait")
        }
        handles: set[tuple[str, int | str]] = set()
        for event in sorted(_conversation_tool_calls(item.records), key=lambda row: row["line"]):
            if event.get("kind") == "tool-call":
                if handles & session_evidence_execution.continuation_handles(event["tool"], event["text"]):
                    wait_lines.add(event["line"])
                continue
            if event.get("kind") != "tool-result" or event.get("input_line") not in wait_lines:
                continue
            bodies, resumed = _wait_response_parts(event["text"])
            handles.update(resumed)
            lines = [line for body in bodies for line in _runtime_inserted.reported_improvement_lines(body)]
            if lines:
                events.append({**event, "record": item.record_id, "text": "\n".join(dict.fromkeys(lines))})
    return events


def _wait_response_parts(value: Any) -> tuple[list[str], set[tuple[str, int | str]]]:
    """waitの本文と、後続回収へ引き継ぐ実行識別子を読む。"""
    bodies: list[str] = []
    handles: set[tuple[str, int | str]] = set()
    if isinstance(value, list):
        children = value
    elif isinstance(value, dict):
        notices = value.get("notices")
        bodies = (
            [notice["body"] for notice in notices if isinstance(notice, dict) and isinstance(notice.get("body"), str)]
            if isinstance(notices, list)
            else []
        )
        session = value.get("session_id")
        if isinstance(session, int) and not isinstance(session, bool):
            handles.add(("session_id", session))
        cell = value.get("cell_id")
        if isinstance(cell, str):
            handles.add(("cell_id", cell))
        children = [value[key] for key in ("stdout", "output", "text", "content", "results") if key in value]
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            children = []
            cell_match = re.match(r"^Script running with cell ID (\S+)", value)
            if cell_match:
                handles.add(("cell_id", cell_match[1]))
            active: list[str] | None = None
            session_text = ""
            for line in split_lines(value):
                if line.startswith(WAIT_BODY_START_PREFIX):
                    active, session_text = [], line.removeprefix(WAIT_BODY_START_PREFIX)
                elif active is not None and line == f"{WAIT_BODY_END_PREFIX}{session_text}":
                    bodies.append("\n".join(active))
                    active = None
                elif active is not None:
                    active.append(line)
                elif line.startswith("{"):
                    with contextlib.suppress(json.JSONDecodeError):
                        children.append(json.loads(line))
        else:
            return _wait_response_parts(parsed) if not isinstance(parsed, str) else ([], set())
    else:
        return [], set()
    for child in children:
        child_bodies, child_handles = _wait_response_parts(child)
        bodies.extend(child_bodies)
        handles.update(child_handles)
    return bodies, handles


def _is_delegate_return(event: dict[str, Any], *, main_record_id: str = "main") -> bool:
    """委譲先の空でない最終返却のうち、明示的なエスカレーション以外を返す。

    `final-result`は記録ごとの最後の非commentaryのアシスタントイベントであり、
    委譲先の記録ではその委譲先が委譲元へ返した返却値に対応する。
    成功の定型形式だけの返却の除外は、`_is_normal_delegate_return`が候補の集約時に行う。
    メイン記録の最終出力は委譲返却ではない。明示的なエスカレーションは独立した候補へ送る。
    """
    if event.get("kind") != "final-result" or event.get("record") == main_record_id:
        return False
    text = event.get("text")
    return isinstance(text, str) and bool(text.strip()) and not _is_escalation_return(event)


def _is_escalation_return(event: dict[str, Any]) -> bool:
    """上位判断を要求する明示的な最終返却であるかを返す。

    値を持つ`続行できない理由:`の行を持つ返却と、旧形式の状態値で続行不能を示した返却を対象とする。
    """
    if event.get("kind") != "final-result":
        return False
    text = event.get("text")
    if not isinstance(text, str):
        return False
    lines = [line.strip() for line in text.splitlines()]
    if any(
        line.startswith(_ESCALATION_REASON_PREFIX)
        and line.removeprefix(_ESCALATION_REASON_PREFIX).strip() not in _NO_ESCALATION_REASONS
        for line in lines
    ):
        return True
    return any(
        line.removeprefix(prefix).strip() == _ESCALATION_RETURN_STATUS
        for line in lines
        for prefix in _RETURN_STATUS_PREFIXES
        if line.startswith(prefix)
    )


def _is_normal_delegate_return(event: dict[str, Any], *, shell: bool = False, resumed: bool = False) -> bool:
    """想定外事象や改善点を持たず、正常な完了だけを示す委譲返却であるかを返す。

    正常な完了は、`状態: completed`（旧形式の`status: completed`を含む）の行を持ち未解決の指摘が0件の返却、
    `<役割名>.subagent.md`が定める返却値で始まる返却、
    全ての判定が適合または合格の返却、およびコマンド実行の委譲（`shell`）で報告した終了コードが全て0で
    失敗・警告・診断の件数に1以上が無い返却とする。
    `状態: completed`の前に置いた前置きの文は、1回の配送で終えた委譲先に限って正常な完了に含める。
    再開された委譲先（`resumed`）の前置きは、受け取り済みの報告の返し直しのような異常を述べる場合があるためである。
    委譲先は想定外の事象を`想定外事象:`行で返すため、この行を持つ返却と、調査結果のような
    自由記述の返却は、本文の意味の判断を要するため候補に残す。
    改善点の標識行は、完了・適合・shellの各正常分岐より先に候補へ保持する。
    """
    text = event.get("text")
    if not isinstance(text, str):
        return False
    if _runtime_inserted.reported_improvement_lines(text):
        return False
    lines = [line.strip() for line in text.splitlines()]
    if any(line.startswith(_UNEXPECTED_EVENT_PREFIXES) for line in lines):
        return False
    body = [line for line in lines if line and not line.startswith("```")]
    if not body:
        return False
    if body[0] in _COMPLETED_RETURN_LINES or (not _COMPLETED_RETURN_LINES.isdisjoint(body) and not resumed):
        unresolved = [line.partition(":")[2].strip() for line in body if line.startswith(_UNRESOLVED_RETURN_PREFIXES)]
        return all(value == "0" for value in unresolved)
    if body[0] in _DELEGATE_COMPLETION_VALUES:
        return True
    if shell:
        exit_codes = [int(match.group(1)) for match in _REPORTED_EXIT_CODE.finditer(text)]
        return bool(exit_codes) and all(code == 0 for code in exit_codes) and not _REPORTED_NONZERO_COUNT.search(text)
    verdicts = [match.group("value") for line in body if (match := _VERDICT_LINE.match(line)) is not None]
    if not verdicts or "不適合" in text or "不合格" in text:
        return False
    return all(value.startswith(("適合", "合格")) for value in verdicts)


def _delegation_record_kinds(timeline: list[dict[str, Any]], *, main_record_id: str = "main") -> tuple[set[str], set[str]]:
    """コマンド実行の委譲（`start`のshell）を受け取った委譲先と、再開された委譲先の記録IDを返す。

    実行環境が先に注入したユーザーロールの本文を除き、最初の配送本文で
    コマンド実行の委譲かを判定する。配送本文が2件以上ある記録を再開されたものとする。
    """
    user_texts: dict[str, list[str]] = collections.defaultdict(list)
    for event in timeline:
        record, text = event.get("record"), event.get("text")
        if (
            event.get("kind") == "user"
            and isinstance(record, str)
            and record != main_record_id
            and isinstance(text, str)
            and not event.get("runtime_inserted")
            and not text.startswith("# AGENTS.md instructions")
        ):
            user_texts[record].append(text)
    shell = {record for record, texts in user_texts.items() if texts and _SHELL_DELEGATION_MARKER in texts[0]}
    resumed = {record for record, texts in user_texts.items() if len(texts) >= 2}
    return shell, resumed
