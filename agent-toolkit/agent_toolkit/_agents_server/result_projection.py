from __future__ import annotations

import datetime
import json
import logging
import pathlib
import re
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import logging_config, turn_state
from agent_toolkit._common import runtime_inserted, session_registry

RESEND_AFTER_WAIT_NEXT_ACTION = "`atk agents wait`で終了を観測してから`send_message`を再送する"
"""turnが中断中または未終了のため継続要求を受け付けない場合の次の操作。MCP層と各backendが共有する。"""


# レビューを目的とするsessionのlabelの末尾。`start`のlabelの凡例と、`<役割名>.subagent.md`から生成するlabelがこの末尾を持つ。
REVIEW_LABEL_SUFFIX = "-review"


REVIEW_RESULT_NEXT_ACTION = (
    "レビューの指摘を受領した。採否を確定する前に`agent-basics:review-standards`を起動し、"
    "同スキルの`references/reviewee.md`に従って採否と修正を確定する。"
    "ユーザーの合意を見送りの根拠にする場合は、合意を示すユーザー発話を特定してから根拠にする"
)


# 読者別探索の結果はレビュー担当が受領し、同担当向けの契約で採否を確定する。
READER_FIT_REVIEW_RESULT_NEXT_ACTION = (
    "読者別探索の指摘を受領した。採否を確定する前に`agent-basics:review-standards`を起動し、"
    "`reader-fit-review.parent.md`「受領」に従って、指摘ごとの採否と根拠を確定する"
)

_REVIEW_STANDARDS_DOCUMENT = "agent-basics:review-standards/SKILL.md"
_REVIEWEE_DOCUMENT = "agent-basics:review-standards/references/reviewee.md"
_READER_FIT_RECEIPT_DOCUMENT = "agent-toolkit:atk-mode/roles/reader-fit-review.parent.md#「受領」"
_REVIEW_RECEIPT_DOCUMENTS = frozenset((_REVIEW_STANDARDS_DOCUMENT, _REVIEWEE_DOCUMENT, _READER_FIT_RECEIPT_DOCUMENT))
_LANE_RECEIPT_DOCUMENT = "agent-toolkit:atk-mode/playbooks/lane-receipt.md#「実測と判断の検収」"
_LANE_RECEIPT_ACTION = (
    "`lane-receipt.md`「実測と判断の検収」を読み、担当の結論が原要求・証拠・現行の方針に合うか確かめてから受理・報告する。"
)


IMPROVEMENT_RESULT_NEXT_ACTION = (
    "`agent_message`の`気付いた改善点:`で始まる全行（箇条書きの項目記号の後に標識がある行を含む）を既存の送達成功記録と比べる。"
    "メインエージェントは未送達または新しい観測だけを次のユーザーへの発話へ転記する。"
    "委譲先は成功記録を確認して未送達と新しい観測を上流へ逐語で中継し、完了返却には原文を各1回残す。"
    "転記する各行は字下げを除く行頭に`気付いた改善点:`を原文のまま置き、標識の前と、標識とコロンの間へ報告元などの語を入れない。"
    "報告元と確かめた範囲は標識行の原文の後ろか別の行に添える。"
    "同じ送達先へ成功確認済みで原文・対象・観測が同じ行は再通知しない。送信失敗・成否不明・記録欠落を既報として抑制しない。"
    "委譲先が観測していない主張は、委譲先の主張として出所を添えて転記する"
)


def append_result_next_action(result: dict[str, Any], next_action: str) -> dict[str, Any]:
    result = dict(result)
    existing = result.get("next_action")
    if isinstance(existing, str) and existing:
        if next_action not in existing:
            result["next_action"] = f"{existing}\n{next_action}"
    else:
        result["next_action"] = next_action
    return result


def with_result_next_action(result: dict[str, Any], label: str | None) -> dict[str, Any]:
    if is_finalized_wait_statement(result):
        result = _without_review_receipt(result)
    guidance = result_guidance(result, label)
    for action, _documents in guidance:
        result = append_result_next_action(result, action)
    documents = list(dict.fromkeys(document for _action, paths in guidance for document in paths))
    if documents:
        result["read_requirements"] = documents
    return result


def _without_review_receipt(result: dict[str, Any]) -> dict[str, Any]:
    """旧版で待機表明に付いたレビュー受領案内だけを、回復して出力するときにも除く。"""
    result = dict(result)
    action = result.get("next_action")
    if isinstance(action, str):
        legacy = {REVIEW_RESULT_NEXT_ACTION, READER_FIT_REVIEW_RESULT_NEXT_ACTION}
        remaining = [line for line in action.split("\n") if line not in legacy]
        if remaining:
            result["next_action"] = "\n".join(remaining)
        else:
            result.pop("next_action", None)
    reading = result.get("read_requirements")
    if isinstance(reading, list):
        other = [item for item in reading if not isinstance(item, str) or item not in _REVIEW_RECEIPT_DOCUMENTS]
        if other:
            result["read_requirements"] = other
        else:
            result.pop("read_requirements", None)
    return result


def is_finalized_wait_statement(result: Mapping[str, Any]) -> bool:
    """完了の形で公開された結果が、再開結果ではなく保留していた待機表明か返す。"""
    error = result.get("error")
    return (
        result.get("status") == "completed"
        and isinstance(error, dict)
        and error.get(turn_state.HELD_RESULT_FINALIZED_KEY) is True
    )


def has_unobserved_children(result: Mapping[str, Any]) -> bool:
    error = result.get("error")
    return (
        result.get("status") in ("completed", "failed", "interrupted")
        and not is_finalized_wait_statement(result)
        and isinstance(error, dict)
        and isinstance(children := error.get("unobservedSessions"), list)
        and bool(children)
    )


def result_guidance(result: Mapping[str, Any], label: str | None) -> list[tuple[str, tuple[str, ...]]]:
    guidance: list[tuple[str, tuple[str, ...]]] = []
    if is_finalized_wait_statement(result):
        guidance.append(("これは再開したturnの結果ではなく待機表明。`show`で現在のturnと待機対象の状態を確かめる", ()))
    elif result.get("status") == "completed" and isinstance(label, str) and label.endswith(REVIEW_LABEL_SUFFIX):
        reader_fit = label.endswith("reader-fit-review")
        guidance.append(
            (
                READER_FIT_REVIEW_RESULT_NEXT_ACTION if reader_fit else REVIEW_RESULT_NEXT_ACTION,
                (
                    _REVIEW_STANDARDS_DOCUMENT,
                    _READER_FIT_RECEIPT_DOCUMENT if reader_fit else _REVIEWEE_DOCUMENT,
                ),
            )
        )
    elif result.get("status") == "completed" and isinstance(label, str) and re.fullmatch(r"lane-[0-9]+-exec", label):
        guidance.append((_LANE_RECEIPT_ACTION, (_LANE_RECEIPT_DOCUMENT,)))
    message = result.get("agent_message")
    if has_unobserved_children(result):
        guidance.append(
            (
                "未回収の子は`unobservedSessions`にある。agents_serverの子の結果が必要なら、"
                "`send_message`で元の委譲先に`atk agents wait`での回収を依頼する",
                (),
            )
        )
    if isinstance(message, str) and runtime_inserted.reported_improvement_lines(message):
        guidance.append(
            (IMPROVEMENT_RESULT_NEXT_ACTION, ("agent-toolkit:atk-mode/playbooks/delegation-receiving.md#「受領後の扱い」",))
        )
    return guidance


def progress_excerpt(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    if len(normalized) <= 80:
        return normalized
    return f"…{normalized[-80:]}"


_ACTION_DETAIL_LIMIT = 200


def action_detail(payload: Any, exclude: tuple[str, ...] = ()) -> str:
    if not isinstance(payload, Mapping):
        return ""
    parts: list[str] = []
    for key, value in payload.items():
        if key in exclude:
            continue
        rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        parts.append(f"{key}={rendered}")
    normalized = " ".join(" ".join(parts).split())
    if len(normalized) <= _ACTION_DETAIL_LIMIT:
        return normalized
    return f"{normalized[:_ACTION_DETAIL_LIMIT]}…"


def elapsed_seconds(value: str | None) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if timestamp.utcoffset() is None:
        return None
    return max(0, int((datetime.datetime.now(datetime.UTC) - timestamp).total_seconds()))


API_ERROR_USAGE_LIMIT_KEYS = ("limit_type", "resets_at")


API_ERROR_PUBLIC_KEYS = ("type", "http_status", "elapsed_seconds", "retry_at", *API_ERROR_USAGE_LIMIT_KEYS)


def activity_projection(
    *,
    updated_at: str | None,
    output_updated_at: str | None,
    started_at: str | None,
    api_error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """最後の活動からの経過とAPI失敗を公開項目へ射影する。

    停滞の判定入力は活動時刻とする。テキスト出力の時刻を判定入力にすると、
    ツール呼び出しだけを長時間続ける正常なsessionを停滞と判定し、
    委譲元が不要な催促と巻き取りへ進む。
    `show`・`list`・`atk agents wait`・`atk agents list`は本関数を共有する。
    呼び出し手段ごとに判定入力が分かれると、同じsessionへ異なる停滞の印が返る。
    """
    del output_updated_at
    seconds_since_activity = elapsed_seconds(updated_at or started_at)
    if seconds_since_activity is None:
        return {}
    projection: dict[str, Any] = {"seconds_since_activity": seconds_since_activity}
    if api_error is not None:
        first_at = api_error.get("first_at")
        elapsed = elapsed_seconds(first_at) if isinstance(first_at, str) else None
        if elapsed is not None:
            projection["api_error"] = {
                "type": api_error.get("type"),
                "http_status": api_error.get("http_status"),
                "elapsed_seconds": elapsed,
            }
            # 利用上限の解除待ちでは、種類と解除予定時刻を加えてAPI再試行と区別できるようにする。
            for key in (*API_ERROR_USAGE_LIMIT_KEYS, "retry_at"):
                if api_error.get(key) is not None:
                    projection["api_error"][key] = api_error[key]
    return projection


def nonempty_error(error: Any) -> bool:
    return error is not None and error != "" and error != {}


def public_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        key: payload[key]
        for key in (
            "session_id",
            "status",
            "label",
            "agent_message",
            "next_action",
            "read_requirements",
            "recovery",
            "engine",
            "model",
            "effort",
            "model_type",
        )
        if key in payload
    }
    if nonempty_error(payload.get("error")):
        result["error"] = refresh_error_diagnostics(payload["error"])
    if is_finalized_wait_statement(result):
        return with_result_next_action(result, result.get("label"))
    return result


def interruption_error(
    reason: session_registry.InterruptionReason,
    *,
    server_instance: str | None,
    turn_seq: int,
    state_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    return {
        **session_registry.interruption_error(reason),
        "diagnostics": logging_config.diagnostics_reference(
            logging_config.InstanceReference(server_instance, turn_seq), state_root=state_root
        ),
    }


def resolution_interruption_error(
    resolution: session_registry.SessionResolution, *, state_root: pathlib.Path | None = None
) -> dict[str, Any] | None:
    info = resolution.resume_info
    if resolution.interruption is None or info is None:
        return None
    recorded = next((turn for turn in resolution.interrupted_turns if turn.turn_seq == info.turn_seq), None)
    return interruption_error(
        resolution.interruption,
        server_instance=recorded.server_instance if recorded is not None else resolution.server_instance,
        turn_seq=info.turn_seq,
        state_root=state_root,
    )


def interrupted_turns_projection(
    turns: tuple[session_registry.InterruptedTurn, ...], *, state_root: pathlib.Path | None = None
) -> list[dict[str, Any]]:
    return [
        {
            "turn_seq": turn.turn_seq,
            "interruptionReason": turn.reason,
            "diagnostics": logging_config.diagnostics_reference(
                logging_config.InstanceReference(turn.server_instance, turn.turn_seq), state_root=state_root
            ),
        }
        for turn in turns
    ]


def refresh_error_diagnostics(error: Any) -> Any:
    if not isinstance(error, Mapping) or not isinstance(error.get("diagnostics"), Mapping):
        return error
    return {**error, "diagnostics": logging_config.refresh_diagnostics_reference(error["diagnostics"])}


def public_notice(payload: Mapping[str, str]) -> dict[str, str]:
    return {"body": payload["body"]}


_LOG = logging.getLogger("agent-toolkit.agents-server.state")
