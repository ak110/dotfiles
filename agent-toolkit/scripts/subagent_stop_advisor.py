"""SubagentStop hook: 完了報告の本文を空/Skill単独報告と縮退表明辞書で検査する。

公式仕様の`last_assistant_message`を直参照し、
当該サブエージェント自身の`transcript_path`に未消化のbackground起動（`has_pending_background_launches`）が
構造的に実在する場合は、完了報告本文の内容によらず無条件で承認する。まだ自身配下の作業が
構造的に残っている以上、続行の是非を本文の言い回しで判定する必要が無いためである。
未消化のbackground起動が無い場合に限り、`is_empty_completion_report`で実質空またはSkill呼び出し
単独の構造的欠落を検出し、続いて`_STOP_FOCUS_CATEGORIES_EXTENDED`と同一SSOTで縮退表明フレーズを照合する。
`stop_hook_active`真の再呼び出し時は判定処理をせず無条件approveを返し、
連続ブロック上限による強制終了を回避する。

`plan-impl-executor`完了報告（`transcript_path`から抽出した`agentId`が
`plan_impl_executor_active_subagent_sessions`辞書のキーと一致する場合のみ発火）は、
主要欄ラベルの欠落検査と、background並列起動宣言・`changed`欄未消化項目の矛盾検査（FB[3]）を行う。
書式不備・矛盾を検出しblockした場合はエントリを保持し、是正後の再試行でも検査を再発火させる。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _scope_escalation import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _STOP_FOCUS_CATEGORIES_EXTENDED,
    _match_scope_escalation,
    is_empty_completion_report,
)
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import has_pending_background_launches  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _transcript_agent_id import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_transcript_agent_id as _extract_transcript_agent_id,
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
    "applied_instructions",
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


def _llm_notice(body: str, *, tag: str = "") -> str:
    """LLM宛て通知メッセージを標準プレフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


def _inspect_plan_impl_executor_report_format(payload: dict) -> tuple[list[str], bool]:
    """`plan-impl-executor`完了報告本文の主要欄ラベル存在検査とbackground並列起動宣言矛盾検査を実施する。

    `transcript_path`のファイル名から抽出した`agentId`が、`posttooluse.py`が親セッション状態へ
    書き込む`plan_impl_executor_active_subagent_sessions`辞書のキーと一致する場合のみ発火する。
    抽出・突合に失敗した場合は対象外として`([], False)`を返す（安全側。他種別のサブエージェント
    停止時の誤発火と、他インスタンスの登録の巻き添え消去を防ぐ）。
    戻り値は「欠落ラベルのリスト」と「background並列起動宣言と`changed`欄未消化項目の矛盾有無」の組とする。
    ラベル欠落とbackground並列起動宣言矛盾は原因が異なるため、呼び出し元で別々のblock理由文を組み立てる（FB[3]）。
    いずれも該当なしの場合または対象外の場合は`([], False)`を返す。
    検査で欠落ラベル・矛盾のいずれも検出しなかった場合のみ、当該エントリを状態辞書から削除する
    （当該サブエージェントの完了検知としての消費）。block判定時はエントリを保持し、
    是正後の再試行でも同一エントリに対する検査が再度発火できるようにする。
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return [], False
    agent_id = _extract_transcript_agent_id(payload.get("transcript_path"))
    if agent_id is None:
        return [], False
    state = read_state(session_id)
    active = state.get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
    if not isinstance(active, dict) or agent_id not in active:
        return [], False

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
    violation = _detect_plan_impl_executor_background_parallel_violation(text)

    if not missing and not violation:

        def _drop_entry(current_state: dict, aid: str = agent_id) -> dict | None:
            current_active = current_state.get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
            if not isinstance(current_active, dict) or aid not in current_active:
                return None
            del current_active[aid]
            current_state[_PLAN_IMPL_EXECUTOR_ACTIVE_KEY] = current_active
            return current_state

        update_state(session_id, _drop_entry)

    return missing, violation


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
    transcript_path = payload.get("transcript_path")
    session_id = payload.get("session_id")
    if isinstance(transcript_path, str) and has_pending_background_launches(
        transcript_path, session_id if isinstance(session_id, str) else ""
    ):
        # 当該サブエージェント自身の配下に未消化のbackground起動が構造的に実在する場合、
        # 完了報告本文の内容（空判定・縮退表明照合を含む）によらず無条件で承認する。
        # Main側`is_pending_async_work`はMain自身のtranscriptのみを走査するため、
        # サブエージェントが自身の配下でさらに起動した孫エージェントの状態を観測できない。
        # ここでの構造判定がその唯一の観測点である。
        return 0
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
            " (directly or via a single non-parallel background delegation) before reporting completion, unless the"
            " caller's launch prompt explicitly authorized the parallel launch.",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    return 0
