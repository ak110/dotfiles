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
主要欄ラベルの欠落検査、構造化blockerと実装経路の根拠検査、
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
    "plan_check",
    "commit_sha",
    "review_status",
    "review_rounds",
    "review_routes",
    "review_targets",
    "review_findings",
    "review_resolution",
    "pending_confirmations",
    "plan_gaps",
    "applied_instructions",
    "blockers",
)

# `plan-impl-executor`が`run_in_background=true`を明示して自己起動した宣言と、
# `changed`欄の未消化項目（`- [ ]`）が共起するかの判定パターン（FB[3]）。
_PLAN_IMPL_EXECUTOR_BACKGROUND_LAUNCH_RE = re.compile(r"run_in_background\s*=\s*true|バックグラウンドで?並列起動")
_PLAN_IMPL_EXECUTOR_UNCHECKED_CHANGED_ITEM_RE = re.compile(r"^-\s*\[\s\]", re.MULTILINE)
_PLAN_IMPL_EXECUTOR_STATUS_COMPLETED_RE = re.compile(r"^status:\s*completed\b", re.MULTILINE)

# `changed:`欄本文（次の主要ラベル行直前まで）を抽出する境界パターン（FB[3]）。
# `PLAN_IMPL_EXECUTOR_REQUIRED_LABELS`・`_PLAN_IMPL_EXECUTOR_NEEDS_ESCALATION_LABEL`と同じラベル集合を
# 境界として使い、`verification`・`blockers`等の他欄に含まれるチェックボックス様の記述を誤検出しない。
PLAN_IMPL_EXECUTOR_ALL_LABELS: tuple[str, ...] = PLAN_IMPL_EXECUTOR_REQUIRED_LABELS
_PLAN_CHECK_REQUIRED_ITEMS = ("計画ファイル", "計画着手前SHA", "終了コード", "警告件数")
_PLAN_CHECK_ITEM_RE = re.compile(r"^\s*-\s*([^:：]+)[:：]\s*(.+?)\s*$")
_PLAN_CHECK_NOT_EXECUTED = "未実施"
_PLAN_CHECK_INTEGER_RE = re.compile(r"^[0-9]+$")
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


def _inspect_plan_check(text: str, *, status: str, review_status: str) -> str | None:
    """`plan_check`欄の本文だけを構造的に検査し、阻害理由があれば返す。

    欄本文の抽出には既存の`_extract_report_field`を用いる。完了報告全体を検索すると、
    他欄に同形式の行がある場合に無関係な値で通過するため採らない。
    """
    body = _extract_report_field(text, "plan_check")
    if not body:
        return "plan_check欄が空である。計画ファイル・計画着手前SHA・終了コード・警告件数を記載すること。"
    items: dict[str, str] = {}
    for line in body.splitlines():
        match = _PLAN_CHECK_ITEM_RE.match(line)
        if match is not None:
            items[match.group(1).strip()] = match.group(2).strip()
    missing = [name for name in _PLAN_CHECK_REQUIRED_ITEMS if name not in items]
    if missing:
        return "plan_check欄に必須項目が無い: " + "・".join(missing)
    review_incomplete = status == "needs_escalation" and review_status == "needs_escalation"
    not_executed = [name for name in _PLAN_CHECK_REQUIRED_ITEMS if items[name] == _PLAN_CHECK_NOT_EXECUTED]
    if review_incomplete:
        if len(not_executed) == len(_PLAN_CHECK_REQUIRED_ITEMS):
            return None
        return "レビュー未完了のplan_check欄は4項目すべてを未実施とし、実行結果と混在させないこと。"
    if not_executed:
        return "レビュー到達後のplan_check欄に未実施の項目がある: " + "・".join(not_executed)
    numeric_items = ("終了コード", "警告件数")
    nonnumeric = [name for name in numeric_items if _PLAN_CHECK_INTEGER_RE.fullmatch(items[name]) is None]
    if nonnumeric:
        return "plan_check欄の終了コードと警告件数は0以上の整数で記載すること: " + "・".join(nonnumeric)
    if items["終了コード"] != "0" or items["警告件数"] != "0":
        return (
            "plan_checkの終了コードまたは警告件数が0でない。"
            "同じ実装経路へ委譲して計画ファイルを是正し、check_plan_file.pyを再実行すること。"
        )
    return None


def _inspect_blockers(text: str) -> list[str]:
    """完了状態とblockers欄の最小整合だけを検査する。"""
    status = _extract_report_first_line(text, "status")
    body = _extract_report_field(text, "blockers")
    normalized = [line.strip().removeprefix("-").strip() for line in body.splitlines() if line.strip()]
    is_none = normalized == ["なし"]
    if status == "completed":
        return [] if is_none else ["blockers must contain only なし for completed status"]
    if status == "needs_escalation":
        return [] if normalized and not is_none else ["needs_escalation requires a concrete blocker"]
    return ["status must be completed or needs_escalation"]


def _inspect_completion_contract(text: str) -> list[str]:
    """completed報告のcommit・検証・レビュー完了を検査する。"""
    if _extract_report_first_line(text, "status") != "completed":
        return []
    violations: list[str] = []
    commit_sha = _extract_report_first_line(text, "commit_sha")
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit_sha) is None:
        violations.append("completed status requires a full commit_sha")
    verification = _extract_report_field(text, "verification")
    if not _verification_is_complete(verification):
        violations.append("completed status requires executed verification")
    review_status = _extract_report_first_line(text, "review_status")
    if review_status != "completed":
        violations.append("completed status requires completed review")
    changed = _extract_report_field(text, "changed")
    if _PLAN_IMPL_EXECUTOR_UNCHECKED_CHANGED_ITEM_RE.search(changed):
        violations.append("completed status must not contain unchecked changed items")
    for label in ("pending_confirmations", "plan_gaps"):
        values = [
            line.strip().removeprefix("-").strip() for line in _extract_report_field(text, label).splitlines() if line.strip()
        ]
        if values != ["なし"]:
            violations.append(f"completed status requires {label} to contain only なし")
    findings = _extract_report_field(text, "review_findings")
    resolution = _extract_report_field(text, "review_resolution")
    if any(marker in findings or marker in resolution for marker in ("未解決", "未修正", "要対応", "needs_escalation")):
        violations.append("completed status must not contain unresolved review findings")
    if review_status == "completed":
        rounds = _extract_report_first_line(text, "review_rounds")
        if re.fullmatch(r"[1-5]", rounds) is None:
            violations.append("completed review requires review_rounds from 1 to 5")
        routes = _parse_review_mapping(_extract_report_field(text, "review_routes"))
        targets = _parse_review_mapping(_extract_report_field(text, "review_targets"))
        required_systems = {"計画準拠系", "独立系"}
        if set(routes) != required_systems or any(not value for value in routes.values()):
            violations.append("completed review requires nonempty routes for both review systems")
        elif len(set(routes.values())) != len(required_systems):
            violations.append("completed review requires distinct route/thread values")
        if set(targets) != required_systems or any(commit_sha not in value for value in targets.values()):
            violations.append("completed review targets must identify the final commit for both systems")
        finding_ids = set(re.findall(r"\b[PI]-[0-9]+\b", findings))
        if finding_ids:
            unresolved_ids = [
                identifier for identifier in sorted(finding_ids) if not _resolution_row_is_complete(resolution, identifier)
            ]
            if unresolved_ids:
                violations.append("completed review requires a resolution for every review finding")
        elif "指摘なし" not in findings:
            violations.append("completed review findings must contain finding IDs or 指摘なし")
    return violations


def _parse_review_mapping(body: str) -> dict[str, str]:
    """レビュー系統の箇条書きを名前と値の対応へ変換する。"""
    result: dict[str, str] = {}
    for line in body.splitlines():
        match = re.fullmatch(r"\s*-\s*(計画準拠系|独立系)[:：]\s*(.*?)\s*", line)
        if match is not None:
            result[match.group(1)] = match.group(2)
    return result


def _parse_semicolon_fields(value: str) -> dict[str, str]:
    """セミコロン区切りの`key: value`列を一意な辞書へ変換する。"""
    result: dict[str, str] = {}
    for item in re.split(r"[;；]", value):
        match = re.fullmatch(r"\s*([^:：]+)[:：]\s*(.*?)\s*", item)
        if match is None or not match.group(2) or match.group(1).strip() in result:
            return {}
        result[match.group(1).strip()] = match.group(2)
    return result


def _verification_item_is_complete(line: str) -> bool:
    """検証行が実行コマンド、成功終了、警告0件を構造化しているか返す。"""
    item = line.strip().removeprefix("-").strip()
    fields = _parse_semicolon_fields(item)
    return fields.get("command") not in (None, "なし") and fields.get("exit_code") == "0" and fields.get("warnings") == "0"


def _verification_is_complete(body: str) -> bool:
    """検証欄に構造化した成功実行があり、失敗・未実施の実行が無いか返す。"""
    if any(marker in body for marker in ("未実施", "検証していない")):
        return False
    command_lines = [line for line in body.splitlines() if "command:" in line or "exit_code:" in line or "warnings:" in line]
    return bool(command_lines) and all(_verification_item_is_complete(line) for line in command_lines)


def _resolution_row_is_complete(body: str, finding_id: str) -> bool:
    """指摘IDに対応する単一表行が採否と必要な修正証拠を持つか返す。"""
    rows = []
    for line in body.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == finding_id:
            rows.append(cells)
    if len(rows) != 1 or len(rows[0]) < 6:
        return False
    disposition = rows[0][2]
    if disposition not in {"採用", "採用（計画対応）", "不採用", "重複"}:
        return False
    if disposition in {"不採用", "重複"}:
        return True
    outcome = _parse_semicolon_fields(rows[0][5])
    return outcome.get("修正結果") == "completed" and outcome.get("検証結果") == "completed"


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
) -> tuple[list[str], bool, list[str]]:
    """完了報告のラベル、background起動宣言、構造的契約を状態変更なしで検査する。"""
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return [], False, []
    agent_id = _resolve_payload_agent_id(payload)
    if agent_id is None:
        return [], False, []
    state = read_state(session_id)
    active = state.get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
    if not isinstance(active, dict) or agent_id not in active:
        return [], False, []

    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        return list(PLAN_IMPL_EXECUTOR_REQUIRED_LABELS), False, []
    required = list(PLAN_IMPL_EXECUTOR_REQUIRED_LABELS)
    missing = [label for label in required if re.search(rf"^{re.escape(label)}:", text, re.MULTILINE) is None]
    violation = _detect_plan_impl_executor_background_parallel_violation(text)
    contract_violations: list[str] = []
    if not missing:
        plan_check_violation = _inspect_plan_check(
            text,
            status=_extract_report_first_line(text, "status"),
            review_status=_extract_report_first_line(text, "review_status"),
        )
        if plan_check_violation is not None:
            contract_violations.append(plan_check_violation)
        contract_violations.extend(_inspect_blockers(text))
        contract_violations.extend(_inspect_completion_contract(text))
    return missing, violation, contract_violations


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
    missing_labels, has_background_parallel_violation, contract_violations = _inspect_plan_impl_executor_report_format(payload)

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
            " (`- [ ]`) items. This violates `agent-toolkit/rules/99-claude-code.md`"
            " 'サブエージェント実装' section."
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
