"""SubagentStop hook: 完了報告の構造的欠落と登録済みexecutorの書式を検査する。

公式仕様の`last_assistant_message`を直参照し、
当該サブエージェント自身の`agent_transcript_path`に未消化の孫エージェント起動
（`has_pending_agent_launches`）が構造的に実在する場合は、登録済みexecutorの書式検査後に承認する。
登録対象外は完了報告本文の内容によらず承認する。
未消化の孫エージェント起動が無い場合に限り、`is_empty_completion_report`で実質空またはSkill呼び出し
単独の構造的欠落を検出する。
`stop_hook_active`真の再呼び出し時は再blockせずapproveを返す。
登録済みexecutorは親SessionEndまでactive entryを保持し、`SendMessage`再開後も同じ検査を適用する。

`plan-impl-executor`完了報告（`agent_id`が
`plan_impl_executor_active_subagent_sessions`辞書のキーと一致する場合のみ発火）は、
主要欄ラベルの欠落検査、二系統レビュー値の整合検査、
background並列起動宣言・`changed`欄未消化項目の矛盾検査（FB[3]）を行う。
書式不備・矛盾を検出しblockした場合はエントリを保持し、是正後の再試行でも検査を再発火させる。
適合報告と未消化の孫起動の有無にかかわらず、登録は親SessionEndまで保持する。

縮退表明の本文検査はAskUserQuestionとStopへ集約し、本hookでは実施しない。

"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import TypeGuard

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _scope_escalation import is_empty_completion_report  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
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
    "implementation_route_evidence",
    "plan_review_route",
    "independent_review_route",
    "review_rounds",
    "review_coverage",
    "review_impact_audit",
    "implementation_history",
    "plan_review_history",
    "independent_review_history",
    "review_resolution",
    "blockers",
)

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
PLAN_IMPL_EXECUTOR_ALL_LABELS: tuple[str, ...] = PLAN_IMPL_EXECUTOR_REQUIRED_LABELS
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


_BLOCKER_TYPES = frozenset(
    {
        "missing_input",
        "user_decision",
        "destructive_action",
        "repeated_failure",
        "route_unavailable",
        "repository_change",
        "recovery_failure",
        "target_expansion",
    }
)
_BLOCKER_TERMINAL_STATES = frozenset(
    {"not_started", "awaiting_confirmation", "failed", "unavailable", "changed", "threshold_reached"}
)
_BLOCKER_REQUIRED_EVIDENCE_FIELDS = frozenset(
    {"operation_key", "attempt_number", "evidence_id", "tool_use_id", "input", "result", "terminal_state"}
)
_BLOCKER_EXPECTED_TERMINAL_STATES = {
    "missing_input": "not_started",
    "user_decision": "awaiting_confirmation",
    "destructive_action": "awaiting_confirmation",
    "repeated_failure": "failed",
    "route_unavailable": "unavailable",
    "repository_change": "changed",
    "recovery_failure": "failed",
    "target_expansion": "threshold_reached",
}


def _is_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    """外部構造の値が文字列キーの辞書であるかを返す。"""
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _is_object_dict_list(value: object) -> TypeGuard[list[dict[str, object]]]:
    """外部構造の値が文字列キー辞書のリストであるかを返す。"""
    return isinstance(value, list) and all(_is_object_dict(item) for item in value)


def _load_report_yaml_field(text: str, label: str) -> object:
    """YAML形式の完了報告欄を構造化値として返す。"""
    body = _extract_report_field(text, label)
    if not body:
        return None
    try:
        return yaml.safe_load(body)
    except yaml.YAMLError:
        return None


def _is_none_list(value: object) -> bool:
    """「なし」だけを表すscalarまたは単一リストであるかを返す。"""
    return value in ("なし", ["なし"])


def _inspect_target_expansion_evidence(evidence: dict[str, object]) -> list[str]:
    """対象拡大証跡の3集合と閾値を検査する。"""
    violations: list[str] = []
    input_value = evidence.get("input")
    result_value = evidence.get("result")
    if not _is_object_dict(input_value) or not _is_object_dict(result_value):
        return ["target_expansion input and result must be mappings"]
    previous = input_value.get("previous_paths")
    added = input_value.get("current_added_paths")
    deduplicated = result_value.get("deduplicated_paths")
    if not isinstance(previous, list) or not isinstance(added, list) or not isinstance(deduplicated, list):
        return ["target_expansion path sets must be string lists"]
    if not all(isinstance(path, str) for value in (previous, added, deduplicated) for path in value):
        return ["target_expansion path sets must be string lists"]
    expected = sorted(set(previous) | set(added))
    if deduplicated != expected:
        violations.append("target_expansion deduplicated_paths must be the sorted union of both input sets")
    if len(deduplicated) < 5:
        violations.append("target_expansion requires at least 5 deduplicated paths")
    return violations


def _inspect_structured_blockers(text: str) -> list[str]:
    """statusに対応するblockerの型、試行証跡、境界値を検査する。"""
    status = _extract_report_first_line(text, "status")
    blockers = _load_report_yaml_field(text, "blockers")
    if status in {"completed", "completed_with_review_cap"}:
        return [] if _is_none_list(blockers) else ["blockers must contain only なし for completed status"]
    if status != "needs_escalation":
        return []
    if not _is_object_dict_list(blockers) or not blockers:
        return ["needs_escalation blockers must be a non-empty structured list"]

    violations: list[str] = []
    blocker_keys: set[tuple[str, str]] = set()
    pending_confirmations = _extract_report_field(text, "pending_confirmations")
    implementation_route = _extract_report_first_line(text, "implementation_route")
    review_status = _extract_report_first_line(text, "review_status")
    routes = {
        _extract_report_first_line(text, "implementation_route"),
        _extract_report_first_line(text, "plan_review_route"),
        _extract_report_first_line(text, "independent_review_route"),
    }
    for index, blocker in enumerate(blockers, start=1):
        blocker_type = blocker.get("blocker_type")
        operation = blocker.get("blocker_operation")
        evidence_items = blocker.get("blocker_evidence")
        attempts = blocker.get("blocker_attempts")
        prefix = f"blockers[{index}]"
        if not isinstance(blocker_type, str) or blocker_type not in _BLOCKER_TYPES:
            violations.append(f"{prefix}.blocker_type must be one of the defined 8 types")
            continue
        if not isinstance(operation, str) or not operation:
            violations.append(f"{prefix}.blocker_operation must be a non-empty string")
            continue
        blocker_key = (blocker_type, operation)
        if blocker_key in blocker_keys:
            violations.append(f"{prefix} duplicates blocker_type and blocker_operation")
        blocker_keys.add(blocker_key)
        if not _is_object_dict_list(evidence_items) or not evidence_items:
            violations.append(f"{prefix}.blocker_evidence must be a non-empty structured list")
            continue
        seen: set[tuple[str, int, str]] = set()
        attempts_by_operation: dict[str, list[int]] = {}
        for evidence_index, evidence in enumerate(evidence_items, start=1):
            evidence_prefix = f"{prefix}.blocker_evidence[{evidence_index}]"
            missing = _BLOCKER_REQUIRED_EVIDENCE_FIELDS - evidence.keys()
            if missing:
                violations.append(f"{evidence_prefix} is missing {', '.join(sorted(missing))}")
                continue
            operation_key = evidence.get("operation_key")
            attempt_number = evidence.get("attempt_number")
            evidence_id = evidence.get("evidence_id")
            terminal_state = evidence.get("terminal_state")
            if not isinstance(operation_key, str) or not operation_key:
                violations.append(f"{evidence_prefix}.operation_key must be a non-empty string")
                continue
            if not isinstance(attempt_number, int) or isinstance(attempt_number, bool) or attempt_number < 1:
                violations.append(f"{evidence_prefix}.attempt_number must be a positive integer")
                continue
            if not isinstance(evidence_id, str) or not evidence_id:
                violations.append(f"{evidence_prefix}.evidence_id must be a non-empty string")
                continue
            composite = (operation_key, attempt_number, evidence_id)
            if composite in seen:
                violations.append(f"{evidence_prefix} duplicates an attempt key")
            seen.add(composite)
            attempts_by_operation.setdefault(operation_key, []).append(attempt_number)
            if terminal_state not in _BLOCKER_TERMINAL_STATES:
                violations.append(f"{evidence_prefix}.terminal_state is undefined")
            elif terminal_state != _BLOCKER_EXPECTED_TERMINAL_STATES[blocker_type]:
                violations.append(f"{evidence_prefix}.terminal_state does not match {blocker_type}")
            if blocker_type == "repeated_failure" and evidence.get("tool_use_id") == "なし":
                violations.append(f"{evidence_prefix}.tool_use_id is required for repeated_failure")
            if blocker_type == "target_expansion":
                violations.extend(_inspect_target_expansion_evidence(evidence))
        for operation_key, numbers in attempts_by_operation.items():
            if sorted(numbers) != list(range(1, len(numbers) + 1)):
                violations.append(f"{prefix} attempt_number must be contiguous from 1 for {operation_key}")
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts != len(seen):
            violations.append(f"{prefix}.blocker_attempts must equal the unique evidence count")
        if blocker_type == "repeated_failure" and len(seen) < 2:
            violations.append(f"{prefix} repeated_failure requires at least 2 attempts")
        if blocker_type in {"user_decision", "destructive_action"} and _is_none_value(pending_confirmations):
            violations.append(f"{prefix} requires the same item in pending_confirmations")
        if blocker_type == "missing_input" and implementation_route != "not_started":
            violations.append(f"{prefix} missing_input requires implementation_route: not_started")
        if blocker_type == "route_unavailable" and "unavailable" not in routes:
            violations.append(f"{prefix} route_unavailable requires an unavailable route")
        if blocker_type == "target_expansion" and review_status != PLAN_IMPL_EXECUTOR_SCOPE_EXPANSION_STATUS:
            violations.append(f"{prefix} target_expansion requires the scope-expansion review_status")
    return violations


def _iter_transcript_tool_pairs(
    transcript_path: str,
) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    """JSONLからtool useと対応result本文を識別子別に収集する。"""
    uses: dict[str, dict[str, object]] = {}
    results: dict[str, str] = {}
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return uses, results
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not _is_object_dict(block):
                continue
            if block.get("type") == "tool_use":
                tool_use_id = block.get("id")
                if isinstance(tool_use_id, str):
                    uses[tool_use_id] = block
            elif block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if isinstance(tool_use_id, str):
                    results[tool_use_id] = _content_text(block.get("content"))
    return uses, results


def _content_text(content: object) -> str:
    """Tool result contentを照合可能な文字列へ正規化する。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif _is_object_dict(item):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _contains_execution_track(value: object, track: str) -> bool:
    """Tool inputの再帰構造内に指定execution_trackがあるかを返す。"""
    if isinstance(value, str):
        return re.search(rf"(?m)^execution_track:\s*{re.escape(track)}\s*$", value) is not None
    if isinstance(value, dict):
        return any(_contains_execution_track(item, track) for item in value.values())
    if isinstance(value, list):
        return any(_contains_execution_track(item, track) for item in value)
    return False


def _json_result_id(result_text: str, *keys: str) -> str | None:
    """JSON resultの指定キー階層から文字列識別子を返す。"""
    try:
        value: object = json.loads(result_text)
    except (json.JSONDecodeError, ValueError):
        return None
    for key in keys:
        if not _is_object_dict(value):
            return None
        value = value.get(key)
    return value if isinstance(value, str) and value else None


def _tool_result_route_id(tool_name: str, result_text: str) -> str | None:
    """tool種別に応じて結果本文からroute識別子を抽出する。"""
    if tool_name in {"mcp__codex__codex", "mcp__codex__codex-reply"}:
        return _json_result_id(result_text, "threadId")
    if tool_name in {"Agent", "Task"}:
        match = re.search(r"(?m)^agentId:\s*(\S+)\s*$", result_text)
        return match.group(1) if match else None
    if tool_name == "SendMessage":
        return _json_result_id(result_text, "pin", "id")
    return None


def _inspect_implementation_route_evidence(text: str, transcript_path: str | None) -> list[str]:
    """申告した実装経路をexecutor JSONLのtool use/resultと照合する。"""
    route = _extract_report_first_line(text, "implementation_route")
    expected_id = (
        _extract_report_field(text, "implementation_thread_id")
        if route == "codex"
        else _extract_report_field(text, "implementation_agent_id")
    )
    evidence = _load_report_yaml_field(text, "implementation_route_evidence")
    if route in {"not_started", "unavailable"}:
        return [] if _is_none_list(evidence) else ["implementation_route_evidence must be なし for an inactive route"]
    if not _is_object_dict_list(evidence) or not evidence:
        return ["implementation_route_evidence must be a non-empty structured list"]
    if not isinstance(transcript_path, str) or not transcript_path:
        return ["agent_transcript_path is required to verify implementation_route_evidence"]
    uses, results = _iter_transcript_tool_pairs(transcript_path)
    violations: list[str] = []
    initial_found = False
    allowed_names = {"mcp__codex__codex", "mcp__codex__codex-reply", "Agent", "Task", "SendMessage"}
    for index, item in enumerate(evidence, start=1):
        tool_name = item.get("tool_name")
        tool_use_id = item.get("tool_use_id")
        route_id = item.get("route_id")
        prefix = f"implementation_route_evidence[{index}]"
        if (
            not isinstance(tool_name, str)
            or tool_name not in allowed_names
            or not isinstance(tool_use_id, str)
            or not isinstance(route_id, str)
        ):
            violations.append(f"{prefix} must contain a supported tool_name, tool_use_id, and route_id")
            continue
        tool_use = uses.get(tool_use_id)
        result_text = results.get(tool_use_id)
        if tool_use is None or result_text is None:
            violations.append(f"{prefix}.tool_use_id does not resolve to a tool use/result pair")
            continue
        if tool_use.get("name") != tool_name:
            violations.append(f"{prefix}.tool_name does not match the transcript")
            continue
        tool_input = tool_use.get("input")
        actual_id = _tool_result_route_id(tool_name, result_text)
        if route_id != expected_id or actual_id != expected_id:
            violations.append(f"{prefix}.route_id does not match the implementation identity")
        if tool_name == "mcp__codex__codex":
            initial_found = True
            if route != "codex" or not _contains_execution_track(tool_input, "implementation"):
                violations.append(f"{prefix} is not an implementation-track Codex initial call")
        elif tool_name == "mcp__codex__codex-reply":
            if not _is_object_dict(tool_input) or tool_input.get("threadId") != expected_id:
                violations.append(f"{prefix} does not continue the implementation thread")
        elif tool_name in {"Agent", "Task"}:
            initial_found = True
            if route != "claude" or not _contains_execution_track(tool_input, "implementation"):
                violations.append(f"{prefix} is not an implementation-track Claude initial call")
        elif tool_name == "SendMessage":
            if not _is_object_dict(tool_input) or tool_input.get("to") != expected_id:
                violations.append(f"{prefix} does not continue the implementation Agent")
    if not initial_found:
        violations.append("implementation_route_evidence requires an initial implementation tool call")

    review_ids: set[str] = set()
    for tool_use_id, tool_use in uses.items():
        if not any(_contains_execution_track(tool_use.get("input"), track) for track in ("plan_review", "independent_review")):
            continue
        result_text = results.get(tool_use_id)
        if result_text is not None:
            review_id = _tool_result_route_id(str(tool_use.get("name", "")), result_text)
            if review_id is not None:
                review_ids.add(review_id)
    if expected_id in review_ids:
        violations.append("implementation identity must not be reused from a review track")
    return violations


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
) -> tuple[list[str], bool, list[str], list[str]]:
    """完了報告のラベル、background起動宣言、レビュー値を状態変更なしで検査する。"""
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return [], False, [], []
    agent_id = _resolve_payload_agent_id(payload)
    if agent_id is None:
        return [], False, [], []
    state = read_state(session_id)
    active = state.get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
    if not isinstance(active, dict) or agent_id not in active:
        return [], False, [], []

    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        return list(PLAN_IMPL_EXECUTOR_REQUIRED_LABELS), False, [], []
    required = list(PLAN_IMPL_EXECUTOR_REQUIRED_LABELS)
    missing = [label for label in required if re.search(rf"^{re.escape(label)}:", text, re.MULTILINE) is None]
    violation = _detect_plan_impl_executor_background_parallel_violation(text)
    review_value_violations = [] if missing else _inspect_plan_impl_executor_review_values(text)
    contract_violations: list[str] = []
    if not missing:
        contract_violations.extend(_inspect_structured_blockers(text))
        transcript_path = payload.get("agent_transcript_path")
        if not isinstance(transcript_path, str) or not transcript_path:
            transcript_path = payload.get("transcript_path")
        contract_violations.extend(_inspect_implementation_route_evidence(text, transcript_path))
    return missing, violation, review_value_violations, contract_violations


def main(payload_text: str) -> int:
    """SubagentStop hookのエントリポイント。"""
    try:
        payload = json.loads(payload_text or "{}")
    except json.JSONDecodeError:
        return 0

    # 再帰呼び出し時は最初に強制承認する。active登録は親SessionEndまで保持する。
    if payload.get("stop_hook_active") is True:
        print(json.dumps({"decision": "approve"}, ensure_ascii=False))
        return 0

    text = payload.get("last_assistant_message")
    missing_labels, has_background_parallel_violation, review_value_violations, contract_violations = (
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
    if contract_violations:
        reason = _llm_notice(
            "blocked: `plan-impl-executor` completion report has invalid blocker or implementation-route evidence:"
            f" {'; '.join(contract_violations)}."
            " See `agent-toolkit/skills/plan-mode/references/plan-impl-caller-reception.md`"
            " '完了報告の検収' section for the structured contract."
            " When resubmitting, restate the entire original completion report with corrected evidence"
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

    return 0
