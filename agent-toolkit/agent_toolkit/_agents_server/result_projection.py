"""sessionの進捗・活動・終端結果と上り通知を、MCPとCLIの公開応答の形へ射影する。"""

from __future__ import annotations

import datetime
import json
import logging
from collections.abc import Mapping
from typing import Any

RESEND_AFTER_WAIT_NEXT_ACTION = "`atk agents wait`で終端を観測してから`send_message`を再送する"
"""turnが中断中または未終端のため継続要求を受け付けない場合の次の操作。MCP層と各backendが共有する。"""


# レビューを目的とするsessionのlabelの末尾。`start`のlabelの凡例と、`<役割名>.subagent.md`から生成するlabelがこの末尾を持つ。
REVIEW_LABEL_SUFFIX = "-review"


# レビューを目的とするsessionの完了結果を受け取った主体へ示す次の操作。
REVIEW_RESULT_NEXT_ACTION = (
    "レビューの指摘を受領した。採否を確定する前に`agent-toolkit:review-standards`を起動し、"
    "同スキルの`references/reviewee.md`に従って採否と修正を確定する。"
    "ユーザーの合意を見送りの根拠にする場合は、合意を示すユーザー発話を特定してから根拠にする"
)


# 結果はメインと委譲先の双方が受け取るため、両者が実行できる操作を受け取った主体の役割ごとに示す。
IMPROVEMENT_RESULT_NEXT_ACTION = (
    "`agent_message`の`気付いた改善点:`で始まる全行を上流へ渡す。"
    "メインエージェントは次のユーザーへの発話へ転記し、委譲先は自身の返却の末尾へ逐語で引き継ぐ。"
    "転記する各行は字下げを除く行頭に`気付いた改善点:`を原文のまま置き、標識の前と、標識とコロンの間へ報告元などの語を入れない。"
    "報告元と確かめた範囲は標識行の原文の後ろか別の行に添える。"
    "未確認の主張の扱いなど残りの細則は`agent-toolkit:delegation`の`references/receiving.md`「受領後の扱い」に従う"
)


def append_result_next_action(result: dict[str, Any], next_action: str) -> dict[str, Any]:
    """既存の案内を保持して次の操作を併記し、複数の返却処理で同じ案内を重ねない。"""
    result = dict(result)
    existing = result.get("next_action")
    if isinstance(existing, str) and existing:
        if next_action not in existing:
            result["next_action"] = f"{existing}\n{next_action}"
    else:
        result["next_action"] = next_action
    return result


def with_result_next_action(result: dict[str, Any], label: str | None) -> dict[str, Any]:
    """受領時に必要なレビューの採否確定と改善点の転記を、既存の次の操作に併記する。"""
    if result.get("status") == "completed" and isinstance(label, str) and label.endswith(REVIEW_LABEL_SUFFIX):
        result = append_result_next_action(result, REVIEW_RESULT_NEXT_ACTION)
    message = result.get("agent_message")
    if isinstance(message, str) and any(line.lstrip().startswith("気付いた改善点:") for line in message.splitlines()):
        result = append_result_next_action(result, IMPROVEMENT_RESULT_NEXT_ACTION)
    return result


def progress_excerpt(text: str) -> str:
    """テキストを改行なしの末尾80文字へ正規化する。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    if len(normalized) <= 80:
        return normalized
    return f"…{normalized[-80:]}"


# ツール呼び出しの入力を1行へ要約するときの上限文字数。
# statuslineは受け取った説明を表示幅で切り詰めるため、上限は`show`の応答が
# 停滞の原因を判別できる長さとして定める。
_ACTION_DETAIL_LIMIT = 200


def action_detail(payload: Any, exclude: tuple[str, ...] = ()) -> str:
    """ツール呼び出しの入力を、keyの受信順を保った1行の要約へ変換する。

    各項目を`<key>=<値>`の形で並べ、文字列以外の値は区切りに空白を含めないJSONへ直列化する。
    `exclude`には、呼び出し元が別の項目として既に公開しているkeyを渡す。
    引数、コマンド文字列およびパッチ内容を含めるのは、同じツール名を繰り返す区間では
    ツール名だけの表示が変化せず、稼働中と停止中を区別できないためである。
    """
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
    """ISO 8601のタイムゾーン付き時刻から現在までの経過秒を返す。

    解釈できない値とタイムゾーンを持たない値では`None`を返す。
    """
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if timestamp.utcoffset() is None:
        return None
    return max(0, int((datetime.datetime.now(datetime.UTC) - timestamp).total_seconds()))


# `api_error`の公開項目。利用上限の解除待ちだけが後半の2項目を持つ。
API_ERROR_USAGE_LIMIT_KEYS = ("limit_type", "resets_at")


API_ERROR_PUBLIC_KEYS = ("type", "http_status", "elapsed_seconds", *API_ERROR_USAGE_LIMIT_KEYS)


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
            for key in API_ERROR_USAGE_LIMIT_KEYS:
                if api_error.get(key) is not None:
                    projection["api_error"][key] = api_error[key]
    return projection


def nonempty_error(error: Any) -> bool:
    """`error`が公開応答へ含める内容を持つかを返す（`None`、空文字列、空の辞書は持たない）。"""
    return error is not None and error != "" and error != {}


def public_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    """内部の結果保存項目を含めず、回収と継続に使う結果だけを返す。"""
    result = {
        key: payload[key]
        for key in (
            "session_id",
            "status",
            "label",
            "agent_message",
            "next_action",
            "recovery",
            "engine",
            "model",
            "effort",
            "model_type",
        )
        if key in payload
    }
    if nonempty_error(payload.get("error")):
        result["error"] = payload["error"]
    return result


def public_notice(payload: Mapping[str, str]) -> dict[str, str]:
    """整列後の通知本文を返す。送信時刻は保存と整列に残す。"""
    return {"body": payload["body"]}


_LOG = logging.getLogger("agent-toolkit.agents-server.state")
