#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""SubagentStop hook: 完了報告の本文を空/Skill単独報告と縮退表明・待機表明辞書で検査する。

公式仕様の`last_assistant_message`を直参照し、
`transcript_path`とisSidechain判定に依存しない。
`is_empty_completion_report`で実質空またはSkill呼び出し単独の構造的欠落を検出し、
続いて`_STOP_FOCUS_CATEGORIES_EXTENDED`と同一SSOTで縮退表明フレーズを照合する。
`async-wait`カテゴリ検出時は、ハーネスが追跡するbackground起動の未消化実在
（`has_pending_background_launches`）を確認し、実在する場合はブロックせず通過させる
（task-notificationで自動発火する経路の待機表明を誤ってブロックしないため）。
ただし完了報告本文が自身の配下でbackground起動したレビュアー系サブエージェント
（`plan-reviewer`・`plan-codex-reviewer`等）への待機表明である場合
（`_is_self_launched_subagent_wait`が真の場合）は、上記bypassを適用せず現行どおりブロックする。
判定に用いる正規表現は`_scope_escalation.py`の共有定数`_ASYNC_WAIT_SELF_LAUNCHED_RE`をaliasで参照する。
`stop_hook_active`真の再呼び出し時は判定処理をせず無条件approveを返し、
連続ブロック上限による強制終了を回避する。

named subagent（`agent_name`非空）でtranscript内のtool_use数が閾値以上ある場合、
メイン宛のSendMessage送付履歴（`name == "SendMessage"`かつ`input.to == "main"`）が
無い時にblockを返し、完了報告の能動送付（またはメイン受領）を促す。
当該判定結果は`agent_name`・tool_use数・送付有無を含めて`append_stop_log`で常時ログ化する。

`plan-impl-executor`完了報告（`plan_impl_executor_active_subagent_sessions`登録時のみ発火）は、
主要欄ラベルの欠落検査と、background並列起動宣言・`changed`欄未消化項目の矛盾検査（FB[3]）を行う。
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _scope_escalation import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _ASYNC_WAIT_SELF_LAUNCHED_RE,
    _STOP_FOCUS_CATEGORIES_EXTENDED,
    _match_scope_escalation,
    is_empty_completion_report,
)
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    append_stop_log,
    has_pending_background_launches,
)

_HOOK_ID = "agent-toolkit/subagent-stop"

# `posttooluse.py`の同名定数と同一集合を保つ。
_PLAN_IMPL_EXECUTOR_ACTIVE_KEY = "plan_impl_executor_active_subagent_sessions"

# `plan-impl-executor`完了報告本文の主要欄ラベル集合。
# SSOTは`agent-toolkit/references/plan-impl/caller-reception.md`手順0および
# `agent-toolkit/agents/plan-impl-executor.md`「出力」節。
# ラベル定義変更時は本定数と両ファイルを同時に更新する。
_PLAN_IMPL_EXECUTOR_REQUIRED_LABELS: tuple[str, ...] = (
    "status",
    "summary",
    "changed",
    "verification",
    "commit_sha",
    "review_handoff",
    "pending_confirmations",
    "plan_gaps",
)
_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL = "blockers"
_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_RE = re.compile(r"^status:\s*needs_escalation\b", re.MULTILINE)

# `plan-impl-executor`が自身の判断でbackground並列起動した宣言と、
# `changed`欄の未消化項目（`- [ ]`）が共起するかの判定パターン（FB[3]）。
# `plan-impl-executor.md`「停止禁止」節が禁止するbackground並列起動の再発検出用。
_PLAN_IMPL_EXECUTOR_BACKGROUND_LAUNCH_RE = re.compile(r"run_in_background\s*=\s*true|バックグラウンドで?並列起動")
_PLAN_IMPL_EXECUTOR_UNCHECKED_CHANGED_ITEM_RE = re.compile(r"^-\s*\[\s\]", re.MULTILINE)
_PLAN_IMPL_EXECUTOR_STATUS_COMPLETED_RE = re.compile(r"^status:\s*completed\b", re.MULTILINE)

# `changed:`欄本文（次の主要ラベル行直前まで）を抽出する境界パターン（FB[3]）。
# `_PLAN_IMPL_EXECUTOR_REQUIRED_LABELS`・`_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL`と同じラベル集合を
# 境界として使い、`verification`・`blockers`等の他欄に含まれるチェックボックス様の記述を誤検出しない。
_PLAN_IMPL_EXECUTOR_ALL_LABELS: tuple[str, ...] = _PLAN_IMPL_EXECUTOR_REQUIRED_LABELS + (
    _PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL,
)
_PLAN_IMPL_EXECUTOR_CHANGED_SECTION_RE = re.compile(
    r"^changed:\s*\n((?:(?!^(?:" + "|".join(re.escape(label) for label in _PLAN_IMPL_EXECUTOR_ALL_LABELS) + r"):).*\n?)*)",
    re.MULTILINE,
)


def _extract_changed_section_body(text: str) -> str:
    """完了報告本文の`changed:`欄本文（次の主要ラベル行直前まで）を抽出する（FB[3]）。

    `changed:`欄が存在しない場合は空文字列を返す。
    """
    match = _PLAN_IMPL_EXECUTOR_CHANGED_SECTION_RE.search(text)
    return match.group(1) if match else ""


def _detect_plan_impl_executor_background_parallel_violation(text: str) -> bool:
    """`plan-impl-executor`完了報告のbackground並列起動宣言と`changed`欄未消化項目の共起を検出する（FB[3]）。

    `status: completed`かつ`run_in_background=true`相当の宣言があり、
    `changed`欄本文に限定して未チェック項目（`- [ ]`）が残る場合を違反として`True`を返す。
    `changed`欄本文への限定は`verification`・`blockers`等の他欄に現れるチェックボックス様の
    記述による誤検出を防ぐため。
    """
    if not _PLAN_IMPL_EXECUTOR_STATUS_COMPLETED_RE.search(text):
        return False
    if not _PLAN_IMPL_EXECUTOR_BACKGROUND_LAUNCH_RE.search(text):
        return False
    changed_body = _extract_changed_section_body(text)
    return bool(_PLAN_IMPL_EXECUTOR_UNCHECKED_CHANGED_ITEM_RE.search(changed_body))


# tool_use数がこの閾値未満のnamed subagentは短命扱いで送信検査対象外とする。
# 起動直後のOSエラー・単一ツール失敗など、能動送付を求めるほど作業が進んでいない
# ケースの誤検出を防ぐため、経験則的な下限として設定する。
_NAMED_SUBAGENT_MIN_TOOL_USES = 3


def _llm_notice(body: str, *, tag: str = "") -> str:
    """LLM宛て通知メッセージを標準プレフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


@dataclasses.dataclass(frozen=True)
class _NamedSubagentSendCheck:
    """`_inspect_named_subagent_send`の判定内訳。"""

    agent_name: str
    tool_use_count: int
    has_main_send: bool
    missing_main_send: bool


def _fail_open_check(agent_name: str) -> _NamedSubagentSendCheck:
    """判定不能（`agent_name`未指定・transcript読み取り不能）時のfail-open結果を返す。"""
    return _NamedSubagentSendCheck(agent_name=agent_name, tool_use_count=-1, has_main_send=False, missing_main_send=False)


def _inspect_named_subagent_send(payload: dict) -> _NamedSubagentSendCheck:
    """Named subagentのメイン宛SendMessage送付有無を判定内訳付きで返す。

    判定条件:
    - `agent_name`フィールドが非空文字列（named subagent起動）
    - `transcript_path`が読み取り可能
    - 当該subagentのtranscript内assistant `tool_use`ブロック総数が閾値以上
    - `name == "SendMessage"`かつ`input.to == "main"`のtool_use呼び出しが1件も存在しない

    `missing_main_send`は上記全てを満たす場合に真。foregroundの短命subagent等で
    tool_use数が閾値未満の場合、または既にメイン宛SendMessage送付済みの場合は偽。
    `agent_name`未指定・transcript読み取り失敗時は`tool_use_count=-1`・`missing_main_send=False`
    で返す（fail-open）。
    """
    agent_name = payload.get("agent_name")
    agent_name = agent_name if isinstance(agent_name, str) else ""
    if not agent_name:
        return _fail_open_check("")
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return _fail_open_check(agent_name)
    try:
        raw = pathlib.Path(transcript_path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return _fail_open_check(agent_name)
    tool_use_count = 0
    sent_to_main = False
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_use_count += 1
            if block.get("name") == "SendMessage":
                inp = block.get("input")
                if isinstance(inp, dict) and inp.get("to") == "main":
                    sent_to_main = True
    missing_main_send = tool_use_count >= _NAMED_SUBAGENT_MIN_TOOL_USES and not sent_to_main
    return _NamedSubagentSendCheck(
        agent_name=agent_name, tool_use_count=tool_use_count, has_main_send=sent_to_main, missing_main_send=missing_main_send
    )


def _log_named_subagent_check(session_id: object, check: _NamedSubagentSendCheck) -> None:
    """`_inspect_named_subagent_send`の判定結果を常時ログへ1行追記する。

    `decision`は`missing_main_send`が真なら`block_named_subagent_missing_send`、
    偽なら`allow_named_subagent_send`とする（`stop_advisor.py`の複合ラベル命名規約に揃える）。
    `session_id`が非文字列・空文字列の場合はログ出力をスキップする（`append_stop_log`の既定挙動）。
    """
    append_stop_log(
        session_id if isinstance(session_id, str) else "",
        "block_named_subagent_missing_send" if check.missing_main_send else "allow_named_subagent_send",
        {
            "agent_name": check.agent_name or "-",
            "tool_use_count": check.tool_use_count,
            "has_main_send": check.has_main_send,
        },
    )


def _inspect_plan_impl_executor_report_format(payload: dict) -> tuple[list[str], bool]:
    """`plan-impl-executor`完了報告本文の主要欄ラベル存在検査とbackground並列起動宣言矛盾検査を実施する。

    `posttooluse.py`が親セッション状態へ書き込む`plan_impl_executor_active_subagent_sessions`辞書に
    現在の`session_id`が登録されている場合のみ発火する。
    戻り値は「欠落ラベルのリスト」と「background並列起動宣言と`changed`欄未消化項目の矛盾有無」の組とする。
    ラベル欠落とbackground並列起動宣言矛盾は原因が異なるため、呼び出し元で別々のblock理由文を組み立てる（FB[3]）。
    いずれも該当なしの場合または対象外の場合は`([], False)`を返す。
    検査後は該当エントリを状態辞書から削除する（呼び出し元セッションの完了検知として消費）。
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return [], False
    state = read_state(session_id)
    active = state.get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
    if not isinstance(active, dict) or not active:
        return [], False

    def _drop_entries(current_state: dict) -> dict | None:
        current_active = current_state.get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
        if not isinstance(current_active, dict) or not current_active:
            return None
        current_state[_PLAN_IMPL_EXECUTOR_ACTIVE_KEY] = {}
        return current_state

    update_state(session_id, _drop_entries)

    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        return [], False
    required = list(_PLAN_IMPL_EXECUTOR_REQUIRED_LABELS)
    if _PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_RE.search(text):
        required.append(_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL)
    missing: list[str] = []
    for label in required:
        pattern = re.compile(rf"^{re.escape(label)}:", re.MULTILINE)
        if not pattern.search(text):
            missing.append(label)
    return missing, _detect_plan_impl_executor_background_parallel_violation(text)


# 自身の配下でbackground起動したレビュアー系サブエージェントへの待機表明を検出する補助パターン（fb 20260720-035611-001）。
# `has_pending_background_launches`のbypassはSubagentStop経路が起動主体（自己起動か外部起動か）を
# 区別せず追跡中background起動の実在のみで通過させる設計であり、自身が配下起動したサブエージェントへの
# 待機表明も誤って通過させる。本ヘルパーの判定パターンは`_scope_escalation.py`側の共有定数
# `_ASYNC_WAIT_SELF_LAUNCHED_RE`のaliasとして参照し、独立複製によるSSOT不一致を構造的に排除する。
_SELF_LAUNCHED_SUBAGENT_WAIT_RE = _ASYNC_WAIT_SELF_LAUNCHED_RE


def _is_self_launched_subagent_wait(text: str) -> bool:
    """完了報告本文が自身配下起動のレビュアー系サブエージェントへの待機表明かを判定する。

    `_scope_escalation.py`側の新規`async-wait`パターンと同一のフレーズ集合を照合し、
    いずれかに一致する場合に真を返す。`main()`が`has_pending_background_launches`による
    bypassを無効化しブロックへ倒す判定に用いる。
    非文字列・空文字列は`False`を返す。
    """
    if not isinstance(text, str) or not text:
        return False
    return bool(_SELF_LAUNCHED_SUBAGENT_WAIT_RE.search(text))


def main() -> int:
    """SubagentStop hookのエントリポイント。"""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    # Stop/SubagentStopフックの再帰呼び出し対策:
    # `stop_hook_active`真は直前の本hook呼び出しがブロックした再呼び出しを示す。
    # 連続ブロック上限到達による強制終了を避けるため、判定処理をせず無条件approveを返す。
    if payload.get("stop_hook_active") is True:
        print(json.dumps({"decision": "approve"}, ensure_ascii=False))
        return 0

    text = payload.get("last_assistant_message")
    if is_empty_completion_report(text):
        reason = _llm_notice(
            "blocked: the subagent completion report is effectively empty or consists only of a `Skill` invocation."
            " Either re-delegate the task or append the full completion body."
            " When resubmitting, restate the entire original completion report along with the added/corrected"
            " content (the main agent does not retain the body across this hook's block).",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    # `is_empty_completion_report`が非文字列・実質空を既に捕捉するため、
    # ここではtypeガードのみを残す。
    if not isinstance(text, str):
        return 0

    match_result = _match_scope_escalation(text, categories=_STOP_FOCUS_CATEGORIES_EXTENDED)
    if match_result is not None:
        category, _matched = match_result
        # async-waitカテゴリ検出時はハーネス追跡background起動の未消化実在で除外判定する。
        # 完了未消化の起動記録が存在する場合、task-notificationで自動発火する経路に該当するため待機表明を許容する。
        # ただし完了報告本文が自身配下起動のレビュアー系サブエージェントへの待機表明の場合はbypassを適用しない
        # （`_is_self_launched_subagent_wait`。配下起動を自己申告しつつ完了扱いにする再発パターンのため）。
        # 起動記録が無い場合・全消化済みの場合（判定不能・素の待機表明）は現行どおりブロックする（fail-closed）。
        transcript_path = payload.get("transcript_path")
        session_id = payload.get("session_id")
        if (
            category == "async-wait"
            and isinstance(transcript_path, str)
            and has_pending_background_launches(transcript_path, session_id if isinstance(session_id, str) else "")
            and not _is_self_launched_subagent_wait(text)
        ):
            return 0
        reason = _llm_notice(
            f"blocked: subagent completion report matched scope-escalation category `{category}`."
            " Either revise the flagged text or continue the work as unfinished."
            " When resubmitting, restate the entire original completion report and rewrite only the flagged"
            " passage (the main agent does not retain the body across this hook's block)."
            " For investigation/review reports that must quote a scope-escalation phrase as a normative"
            " reference, follow `agent-toolkit:agent-standards` 'Avoiding context contamination' section and"
            " use the category identifier or section name for indirect reference instead of the raw phrase.",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    missing_labels, has_background_parallel_violation = _inspect_plan_impl_executor_report_format(payload)
    if missing_labels:
        reason = _llm_notice(
            "blocked: `plan-impl-executor` completion report is missing required labels:"
            f" {', '.join(missing_labels)}."
            " See `agent-toolkit/agents/plan-impl-executor.md` '出力' section for the required format."
            " When resubmitting, restate the entire original completion report with the missing labels added"
            " (the main agent does not retain the body across this hook's block).",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    if has_background_parallel_violation:
        reason = _llm_notice(
            "blocked: `plan-impl-executor` completion report declares a self-initiated background parallel"
            " subagent launch (`run_in_background=true`) while the `changed` section still has unchecked"
            " (`- [ ]`) items. This violates `agent-toolkit/agents/plan-impl-executor.md` '停止禁止' section,"
            " which prohibits self-judged background parallel launches. Complete the unfinished work"
            " (directly or via `run_in_background=false` delegation) before reporting completion, unless the"
            " caller's launch prompt explicitly authorized the parallel launch.",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    named_subagent_check = _inspect_named_subagent_send(payload)
    _log_named_subagent_check(payload.get("session_id"), named_subagent_check)
    if named_subagent_check.missing_main_send:
        reason = _llm_notice(
            "blocked: this named subagent finished without ever calling `SendMessage(to='main')`."
            " Named subagents launched with `run_in_background=true` must actively deliver the completion"
            " report to the main agent via `SendMessage(to='main', message=<full body>)`; waiting for the main"
            " agent to poll is treated as incomplete."
            " Send the completion report body to main now and then stop."
            " If this subagent was launched in the foreground and the main agent already received the return"
            " value directly, ignore this notice.",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
