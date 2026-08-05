"""SubagentStop hook: 完了報告の構造的欠落と登録済みexecutorの書式を検査する。

公式仕様の`last_assistant_message`を直参照し、
当該サブエージェント自身の`agent_transcript_path`に未消化の孫エージェント起動
（`has_pending_agent_launches`）が構造的に実在する場合は、登録済みexecutorの書式検査後に承認する。
登録対象外は完了報告本文の内容によらず承認する。
未消化の孫エージェント起動が無い場合に限り、`is_empty_completion_report`で実質空またはSkill呼び出し
単独の構造的欠落を検出する。
`stop_hook_active`真の再呼び出し時は再blockせずapproveを返す。
登録済みexecutorは適合報告かつ未消化の孫起動なしの場合だけactive entryを消費する。

`plan-impl-executor`完了報告（`agent_id`が
`plan_impl_executor_active_subagent_sessions`辞書のキーと一致する場合のみ発火）は、
主要欄ラベルの欠落検査、二系統レビュー値の整合検査、
background並列起動宣言・`changed`欄未消化項目の矛盾検査（FB[3]）を行う。
書式不備・矛盾を検出しblockした場合はエントリを保持し、是正後の再試行でも検査を再発火させる。
適合報告でも未消化の孫起動がある間はエントリを保持し、最終報告の承認時にだけ消費する。

縮退表明の本文検査はAskUserQuestionとStopへ集約し、本hookでは実施しない。

"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _scope_escalation import is_empty_completion_report  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import has_pending_agent_launches  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _transcript_agent_id import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_transcript_agent_id as _extract_transcript_agent_id,
)

_HOOK_ID = "agent-toolkit/subagent-stop"

# `posttooluse.py`の同名定数と同一集合を保つ。
_PLAN_IMPL_EXECUTOR_ACTIVE_KEY = "plan_impl_executor_active_subagent_sessions"

# `plan-impl-executor`完了報告本文の主要欄ラベル集合。
# SSOTは`agent-toolkit/skills/plan-mode/references/plan-impl-caller-reception.md`
# 「完了報告の検収」節および
# `agent-toolkit/agents/plan-impl-executor.md`「出力」節。
# ラベル定義変更時は本定数と両ファイルを同時に更新する。
PLAN_IMPL_EXECUTOR_REQUIRED_LABELS: tuple[str, ...] = (
    "status",
    "summary",
    "changed",
    "external_operations",
    "verification",
    "commit_sha",
    "review_status",
    "review_final_findings",
    "review_skip_instruction",
    "review_caller_verification",
    "pending_confirmations",
    "plan_gaps",
    "applied_instructions",
    "implementation_thread_id",
    "plan_review_thread_id",
    "independent_review_thread_id",
    "implementation_agent_id",
    "plan_review_agent_id",
    "independent_review_agent_id",
    "implementation_route",
    "plan_review_route",
    "independent_review_route",
    "review_rounds",
    "review_coverage",
    "review_impact_audit",
    "implementation_history",
    "plan_review_history",
    "independent_review_history",
    "review_resolution",
)
_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL = "blockers"
_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_RE = re.compile(r"^status:\s*needs_escalation\b", re.MULTILINE)

# `plan-impl-executor`が`run_in_background=true`を明示して自己起動した宣言と、
# `changed`欄の未消化項目（`- [ ]`）が共起するかの判定パターン（FB[3]）。
_PLAN_IMPL_EXECUTOR_BACKGROUND_LAUNCH_RE = re.compile(r"run_in_background\s*=\s*true|バックグラウンドで?並列起動")
_PLAN_IMPL_EXECUTOR_UNCHECKED_CHANGED_ITEM_RE = re.compile(r"^-\s*\[\s\]", re.MULTILINE)
_PLAN_IMPL_EXECUTOR_STATUS_COMPLETED_RE = re.compile(
    r"^status:\s*completed(?:_with_review_cap)?\b",
    re.MULTILINE,
)
_PLAN_IMPL_EXECUTOR_FINAL_FINDINGS_RE = re.compile(r"^計画準拠系(\d+)件・独立系(\d+)件$")
# `status: needs_escalation`で許容する`review_status`の固定値。
# `agent-toolkit/agents/plan-impl-executor.md`「出力」節が正本であり、
# `agent_definitions_test.py`が同節の記載と本定数の一致を検査する。
PLAN_IMPL_EXECUTOR_REVIEW_CAP_STATUS = "上限到達後の既知指摘修正済み（再レビューなし）"
PLAN_IMPL_EXECUTOR_SCOPE_EXPANSION_STATUS = "対象拡大により中断（指摘反映済み・再レビューなし）"
PLAN_IMPL_EXECUTOR_SKIPPED_STATUS = "レビューは実施しない（ユーザー指示）"
PLAN_IMPL_EXECUTOR_INCOMPLETE_STATUS = "レビュー未完了"

# `changed:`欄本文（次の主要ラベル行直前まで）を抽出する境界パターン（FB[3]）。
# `PLAN_IMPL_EXECUTOR_REQUIRED_LABELS`・`_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL`と同じラベル集合を
# 境界として使い、`verification`・`blockers`等の他欄に含まれるチェックボックス様の記述を誤検出しない。
PLAN_IMPL_EXECUTOR_ALL_LABELS: tuple[str, ...] = PLAN_IMPL_EXECUTOR_REQUIRED_LABELS + (
    _PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL,
)
_PLAN_IMPL_EXECUTOR_CHANGED_SECTION_RE = re.compile(
    r"^changed:\s*\n((?:(?!^(?:" + "|".join(re.escape(label) for label in PLAN_IMPL_EXECUTOR_ALL_LABELS) + r"):).*\n?)*)",
    re.MULTILINE,
)


def _extract_changed_section_body(text: str) -> str:
    """完了報告本文の`changed:`欄本文（次の主要ラベル行直前まで）を抽出する（FB[3]）。

    `changed:`欄が存在しない場合は空文字列を返す。
    """
    match = _PLAN_IMPL_EXECUTOR_CHANGED_SECTION_RE.search(text)
    return match.group(1) if match else ""


def _extract_report_field(text: str, label: str) -> str:
    """完了報告の欄本文を次の主要ラベル直前まで抽出する。"""
    labels = "|".join(re.escape(item) for item in PLAN_IMPL_EXECUTOR_ALL_LABELS)
    pattern = re.compile(
        rf"^{re.escape(label)}:[ \t]*(.*(?:\n(?!(?:{labels}):).*)*)",
        re.MULTILINE,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _extract_report_first_line(text: str, label: str) -> str:
    """完了報告の欄本文から先頭行を返す。空欄では空文字列を返す。"""
    lines = _extract_report_field(text, label).splitlines()
    return lines[0] if lines else ""


def _is_none_value(value: str) -> bool:
    """欄が空または「なし」だけであるかを返す。"""
    return not value or value == "なし"


def _inspect_skipped_review_fields(final_findings: str, skip_instruction: str, rounds: int, resolution: str) -> list[str]:
    """ユーザー指示によるレビュー省略で、引数に取る4欄の違反を返す。

    省略は`completed`と`needs_escalation`のどちらでも起こり、実施していない事実は同じである。
    `status`ごとに要求が異なると、同じ省略状況の報告が一方でだけ通過する。
    `review_caller_verification`は`status`により求める確認の対象が変わるため本関数の対象外とし、
    呼び出し側が個別に検査する。
    """
    violations: list[str] = []
    if final_findings != "対象外":
        violations.append("review_final_findings must be 対象外 when review is skipped")
    if _is_none_value(skip_instruction):
        violations.append("review_skip_instruction must preserve the user instruction when review is skipped")
    if rounds != 0:
        violations.append("review_rounds must be 0 when review is skipped")
    if not _is_none_value(resolution):
        violations.append("review_resolution must be なし when review is skipped")
    return violations


def _inspect_plan_impl_executor_review_values(text: str) -> list[str]:
    """完了報告のstatusと二系統レビュー欄の値整合違反を返す。"""
    status = _extract_report_first_line(text, "status")
    violations: list[str] = []
    allowed_statuses = {"completed", "completed_with_review_cap", "needs_escalation"}
    if status not in allowed_statuses:
        violations.append("status must be completed, completed_with_review_cap, or needs_escalation")
        return violations

    review_status = _extract_report_first_line(text, "review_status")
    final_findings = _extract_report_first_line(text, "review_final_findings")
    skip_instruction = _extract_report_field(text, "review_skip_instruction")
    caller_verification = _extract_report_first_line(text, "review_caller_verification")
    rounds_text = _extract_report_first_line(text, "review_rounds")
    try:
        rounds = int(rounds_text)
    except ValueError:
        rounds = -1

    all_tracks = ("implementation", "plan_review", "independent_review")
    review_tracks = ("plan_review", "independent_review")
    routes = {track: _extract_report_first_line(text, f"{track}_route") for track in all_tracks}
    threads = {track: _extract_report_field(text, f"{track}_thread_id") for track in all_tracks}
    agent_ids = {track: _extract_report_field(text, f"{track}_agent_id") for track in all_tracks}
    histories = {track: _extract_report_field(text, f"{track}_history") for track in all_tracks}
    resolution = _extract_report_field(text, "review_resolution")
    coverage = _extract_report_field(text, "review_coverage")
    impact_audit = _extract_report_field(text, "review_impact_audit")

    def inspect_track_identity(track: str, allowed_routes: set[str]) -> None:
        route = routes[track]
        if route not in allowed_routes:
            expected = "codex or claude" if allowed_routes == {"codex", "claude"} else " or ".join(sorted(allowed_routes))
            violations.append(f"{track}_route must be {expected}")
            return
        if route == "codex":
            if _is_none_value(threads[track]):
                violations.append(f"{track}_thread_id must not be なし for codex route")
            if not _is_none_value(agent_ids[track]):
                violations.append(f"{track}_agent_id must be なし for codex route")
        elif route == "claude":
            if not _is_none_value(threads[track]):
                violations.append(f"{track}_thread_id must be なし for claude route")
            if _is_none_value(agent_ids[track]):
                violations.append(f"{track}_agent_id must not be なし for claude route")
        else:
            if not _is_none_value(threads[track]):
                violations.append(f"{track}_thread_id must be なし for {route} route")
            if not _is_none_value(agent_ids[track]):
                violations.append(f"{track}_agent_id must be なし for {route} route")

    if status == "completed" and review_status.startswith("実施完了"):
        inspect_track_identity("implementation", {"codex", "claude"})
        if _PLAN_IMPL_EXECUTOR_FINAL_FINDINGS_RE.fullmatch(final_findings) is None:
            violations.append("review_final_findings must contain two non-negative finding counts")
        if not _is_none_value(skip_instruction):
            violations.append("review_skip_instruction must be なし for completed review")
        if caller_verification != "不要":
            violations.append("review_caller_verification must be 不要 for completed review")
        if rounds not in range(1, 6):
            violations.append("review_rounds must be between 1 and 5 for completed review")
        if _is_none_value(resolution):
            violations.append("review_resolution must not be なし for completed review")
        for track in review_tracks:
            inspect_track_identity(track, {"codex", "claude"})
            if _is_none_value(histories[track]):
                violations.append(f"{track}_history must not be なし for completed review")
        if _is_none_value(coverage):
            violations.append("review_coverage must not be なし for completed review")
        if _is_none_value(impact_audit):
            violations.append("review_impact_audit must not be なし for completed review")
    elif status == "completed_with_review_cap":
        inspect_track_identity("implementation", {"codex", "claude"})
        if review_status != PLAN_IMPL_EXECUTOR_REVIEW_CAP_STATUS:
            violations.append("review_status must show fixed known findings after review cap")
        if _PLAN_IMPL_EXECUTOR_FINAL_FINDINGS_RE.fullmatch(final_findings) is None:
            violations.append("review_final_findings must contain two non-negative finding counts")
        if not _is_none_value(skip_instruction):
            violations.append("review_skip_instruction must be なし after review cap")
        if caller_verification != "不要":
            violations.append("review_caller_verification must be 不要 after review cap")
        if rounds != 5:
            violations.append("review_rounds must be 5 after review cap")
        if _is_none_value(resolution):
            violations.append("review_resolution must not be なし after review cap")
        if _is_none_value(coverage):
            violations.append("review_coverage must not be なし after review cap")
        if _is_none_value(impact_audit):
            violations.append("review_impact_audit must not be なし after review cap")
        for track in review_tracks:
            inspect_track_identity(track, {"codex", "claude"})
            if _is_none_value(histories[track]):
                violations.append(f"{track}_history must not be なし after review cap")
    elif status == "completed" and review_status == PLAN_IMPL_EXECUTOR_SKIPPED_STATUS:
        inspect_track_identity("implementation", {"codex", "claude"})
        violations.extend(_inspect_skipped_review_fields(final_findings, skip_instruction, rounds, resolution))
        if caller_verification != "ユーザー指示原文との照合が必要":
            violations.append("review_caller_verification must request user instruction verification when review is skipped")
        for track in review_tracks:
            inspect_track_identity(track, {"not_started"})
            if not _is_none_value(histories[track]):
                violations.append(f"{track}_history must be なし when review is skipped")
        if not _is_none_value(coverage):
            violations.append("review_coverage must be なし when review is skipped")
        if not _is_none_value(impact_audit):
            violations.append("review_impact_audit must be なし when review is skipped")
    elif status == "completed":
        inspect_track_identity("implementation", {"codex", "claude"})
        violations.append("review_status must show completed review or user-directed skip")
    elif status == "needs_escalation":
        inspect_track_identity("implementation", {"codex", "claude", "not_started", "unavailable"})
        # レビュー工程の到達状況で3通りに分ける。到達しないまま返す場合だけ未確定を求め、
        # レビューを完了または中断した場合は実測値を残させる。実測値を`レビュー未完了`と
        # `未確定`へ置き換えると、呼び出し元は完了済みのレビュー工程を再実行する。
        # 起動を試みていない場合だけ`not_started`となるため、レビュー実測値を伴う区分では
        # 起動済みのrouteだけを許す。未起動のrouteに履歴と件数がある報告は実測と矛盾する。
        review_route_candidates = {"codex", "claude", "not_started", "unavailable"}
        if review_status == PLAN_IMPL_EXECUTOR_INCOMPLETE_STATUS:
            # レビュー工程へ到達していない以上、実測を残す欄に値がある報告は矛盾する。
            review_route_candidates = {"not_started", "unavailable"}
            if final_findings != "未確定":
                violations.append("review_final_findings must be 未確定 when review is not completed")
            if not _is_none_value(skip_instruction):
                violations.append("review_skip_instruction must be なし when review is not completed")
            if rounds != 0:
                violations.append("review_rounds must be 0 when review is not completed")
            for label, value in (
                ("review_resolution", resolution),
                ("review_coverage", coverage),
                ("review_impact_audit", impact_audit),
            ):
                if not _is_none_value(value):
                    violations.append(f"{label} must be なし when review is not completed")
            for track in review_tracks:
                if not _is_none_value(histories[track]):
                    violations.append(f"{track}_history must be なし when review is not completed")
        elif review_status == PLAN_IMPL_EXECUTOR_SKIPPED_STATUS:
            # 省略時の欄要求は`status`に依らず`completed`側と同一に保つ。
            review_route_candidates = {"not_started"}
            violations.extend(_inspect_skipped_review_fields(final_findings, skip_instruction, rounds, resolution))
            for label, value in (("review_coverage", coverage), ("review_impact_audit", impact_audit)):
                if not _is_none_value(value):
                    violations.append(f"{label} must be なし when review is skipped")
            for track in review_tracks:
                if not _is_none_value(histories[track]):
                    violations.append(f"{track}_history must be なし when review is skipped")
        elif review_status.startswith("実施完了") or review_status in {
            PLAN_IMPL_EXECUTOR_REVIEW_CAP_STATUS,
            PLAN_IMPL_EXECUTOR_SCOPE_EXPANSION_STATUS,
        }:
            review_route_candidates = {"codex", "claude"}
            if _PLAN_IMPL_EXECUTOR_FINAL_FINDINGS_RE.fullmatch(final_findings) is None:
                violations.append(
                    "review_final_findings must contain two non-negative finding counts when review results exist"
                )
            if not _is_none_value(skip_instruction):
                violations.append("review_skip_instruction must be なし when review results exist")
            # 実施済みのラウンドがある以上、その実測を残す欄が「なし」の報告は実測と矛盾する。
            # 上限到達は5ラウンドの到達そのものを表すため、`completed_with_review_cap`と同じ値を求める。
            if review_status == PLAN_IMPL_EXECUTOR_REVIEW_CAP_STATUS:
                if rounds != 5:
                    violations.append("review_rounds must be 5 after review cap")
            elif rounds not in range(1, 6):
                violations.append("review_rounds must be between 1 and 5 when review results exist")
            for label, value in (
                ("review_resolution", resolution),
                ("review_coverage", coverage),
                ("review_impact_audit", impact_audit),
            ):
                if _is_none_value(value):
                    violations.append(f"{label} must not be なし when review results exist")
            for track in review_tracks:
                if _is_none_value(histories[track]):
                    violations.append(f"{track}_history must not be なし when review results exist")
        else:
            violations.append("review_status must be one of the values defined for needs_escalation")
        if caller_verification != "未完了事項の確認が必要":
            violations.append("review_caller_verification must request pending-item verification for needs_escalation")
        for track in review_tracks:
            inspect_track_identity(track, review_route_candidates)
    return violations


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


def _resolve_payload_agent_id(payload: dict) -> str | None:
    """SubagentStop入力から対象サブエージェントの識別子を返す。"""
    agent_id = payload.get("agent_id")
    if isinstance(agent_id, str) and agent_id:
        return agent_id
    agent_id = _extract_transcript_agent_id(payload.get("agent_transcript_path"))
    if agent_id is not None:
        return agent_id
    # 旧版入力との互換性を保つ。現行仕様の`transcript_path`は親セッションを指す。
    return _extract_transcript_agent_id(payload.get("transcript_path"))


def _inspect_plan_impl_executor_report_format(
    payload: dict,
) -> tuple[list[str], bool, list[str], tuple[str, str] | None]:
    """完了報告のラベル、background起動宣言、レビュー値を状態変更なしで検査する。"""
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return [], False, [], None
    agent_id = _resolve_payload_agent_id(payload)
    if agent_id is None:
        return [], False, [], None
    state = read_state(session_id)
    active = state.get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
    if not isinstance(active, dict) or agent_id not in active:
        return [], False, [], None

    token = (session_id, agent_id)
    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        return list(PLAN_IMPL_EXECUTOR_REQUIRED_LABELS), False, [], token
    required = list(PLAN_IMPL_EXECUTOR_REQUIRED_LABELS)
    if _PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_RE.search(text):
        required.append(_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL)
    missing = [label for label in required if re.search(rf"^{re.escape(label)}:", text, re.MULTILINE) is None]
    violation = _detect_plan_impl_executor_background_parallel_violation(text)
    review_value_violations = [] if missing else _inspect_plan_impl_executor_review_values(text)
    return missing, violation, review_value_violations, token


def _consume_plan_impl_executor_active_entry(token: tuple[str, str]) -> None:
    """承認したexecutor完了報告に対応する登録エントリを消費する。"""
    session_id, agent_id = token

    def _drop_entry(current_state: dict, aid: str = agent_id) -> dict | None:
        current_active = current_state.get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
        if not isinstance(current_active, dict) or aid not in current_active:
            return None
        del current_active[aid]
        current_state[_PLAN_IMPL_EXECUTOR_ACTIVE_KEY] = current_active
        return current_state

    update_state(session_id, _drop_entry)


def main(payload_text: str) -> int:
    """SubagentStop hookのエントリポイント。"""
    try:
        payload = json.loads(payload_text or "{}")
    except json.JSONDecodeError:
        return 0

    text = payload.get("last_assistant_message")
    missing_labels, has_background_parallel_violation, review_value_violations, active_token = (
        _inspect_plan_impl_executor_report_format(payload)
    )

    agent_transcript_path = payload.get("agent_transcript_path")
    if not isinstance(agent_transcript_path, str) or not agent_transcript_path:
        # `agent_transcript_path`を持たない旧版入力との互換性を保つ。
        agent_transcript_path = payload.get("transcript_path")
    session_id = payload.get("session_id")
    has_pending = isinstance(agent_transcript_path, str) and has_pending_agent_launches(
        agent_transcript_path, session_id if isinstance(session_id, str) else ""
    )

    # 再帰呼び出し時は強制承認する。登録済みexecutorの正しい最終報告であり、
    # 未消化の子起動が無い場合に限って登録エントリも消費する。
    if payload.get("stop_hook_active") is True:
        if (
            active_token is not None
            and not missing_labels
            and not has_background_parallel_violation
            and not review_value_violations
            and not has_pending
        ):
            _consume_plan_impl_executor_active_entry(active_token)
        print(json.dumps({"decision": "approve"}, ensure_ascii=False))
        return 0

    # 登録済みexecutorの書式違反は、未消化の子起動があっても先に差し戻す。
    if missing_labels:
        reason = _llm_notice(
            "blocked: `plan-impl-executor` completion report is missing required labels:"
            f" {', '.join(missing_labels)}."
            " See `agent-toolkit/skills/plan-mode/references/plan-impl-caller-reception.md`"
            " '完了報告の検収' section for the required format."
            " When resubmitting, restate the entire original completion report with the missing labels added"
            " (the main agent does not retain the body across this hook's block).",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    if review_value_violations:
        reason = _llm_notice(
            "blocked: `plan-impl-executor` completion report has inconsistent review values:"
            f" {'; '.join(review_value_violations)}."
            " See `agent-toolkit/skills/plan-mode/references/plan-impl-caller-reception.md`"
            " '完了報告の検収' section for the required value combinations."
            " When resubmitting, restate the entire original completion report with consistent values"
            " (the main agent does not retain the body across this hook's block).",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    if has_background_parallel_violation:
        reason = _llm_notice(
            "blocked: `plan-impl-executor` completion report declares a self-initiated background parallel"
            " subagent launch (`run_in_background=true`) while the `changed` section still has unchecked"
            " (`- [ ]`) items. This violates `agent-toolkit/rules/02-claude-code.md`"
            " 'サブエージェント運用' section."
            " `plan-impl-executor`は`run_in_background`を省略して起動し、"
            "実際の受領経路を実行結果から判定する必要があります。"
            "未完了項目がある状態で`run_in_background=true`を指定した自己起動は行わないでください。",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    if has_pending:
        # 正しい中間報告は承認するが、最終報告ではないため登録エントリを消費しない。
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

    if active_token is not None:
        _consume_plan_impl_executor_active_entry(active_token)
    return 0
