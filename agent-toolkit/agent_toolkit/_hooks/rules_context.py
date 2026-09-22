"""実行主体ごとの規範を起動時の文脈へ追加するhandler。

委譲先の判定は`_common.delegated_session`を正本とする。

出力する本文は区分ごとに境界を持つ。注記は`agent-toolkit-hook-message`、規範は`normative-context`、
常駐処理が渡した追加指示は`forwarded-user-input`で囲む。受信側が区分ごとに生成主体と種別を
判別できるようにするためであり、区分ごとに囲んだ本文を全体で重ねて囲まない。

Claude Codeはhook 1件の出力を10,000文字で切り詰める。条文の欠落を防ぐため、
最大構成を同じ上限へ収める契約テストを置く。
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import message_format
from agent_toolkit._common.delegated_session import is_delegated
from agent_toolkit._hooks.message_format import xml_message
from agent_toolkit._hooks.notice import formatter as _notice_formatter

_HOOK_ID = "agent-toolkit/rules_context"
_llm_notice = _notice_formatter(_HOOK_ID)

RESPONSE_LANGUAGE_NOTICE = "ユーザーへ向けた地の文は、最初の応答の1文目から日本語で書く。"
QUALITY_CHECKPOINT_NOTICE = (
    "目的・利用場面を明示し、最小設計を選ぶ。会話限定指示を成果物へ混入させない。"
    "要件未達と無根拠な代替・旧・互換経路を拒み、規範を正本とする。"
)
ASK_USER_QUESTION_CHECKLIST = (
    "確認質問は本文だけで判断材料を完結させる。未確定な対象・範囲・時点・区分を分け、"
    "選択肢ごとの外部可視の結果と副作用を示す。独立した事項を束ねず、推奨案と根拠、回答単位を明記する。"
)

SHARE_DIR = pathlib.Path(__file__).resolve().parents[2] / "share"
MAIN_RULES_PATH = SHARE_DIR / "rules-main.md"
MAIN_RULES_CLAUDE_CODE_PATH = SHARE_DIR / "rules-main.claude-code.md"
SUBAGENT_RULES_PATH = SHARE_DIR / "rules-subagent.md"
SUBAGENT_RULES_CLAUDE_CODE_PATH = SHARE_DIR / "rules-subagent.claude-code.md"
CLAUDE_CODE_OUTPUT_LIMIT = 10_000
SESSION_TEMP_PREFIX = "session"
# `atk wi process-loop instruct`が保持し、常駐処理がセッション起動時に渡す本文。
PROCESS_LOOP_INSTRUCTION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION"
PROCESS_LOOP_INSTRUCTION_ELEMENT = message_format.FORWARDED_USER_INPUT_ELEMENT
# 実行主体へ常時読み込ませる規範の境界。種別で読み手を区別する。
NORMATIVE_ELEMENT = "normative-context"
NORMATIVE_SOURCE = "agent-toolkit"
NORMATIVE_KIND_MAIN = "rules-main"
NORMATIVE_KIND_SUBAGENT = "rules-subagent"


def compose_session_start(source: str, *, delegated: bool, host: str) -> str | None:
    """SessionStartへ追加する本文を構成する。

    使用言語の規定は`rules-main.md`「ユーザー向け発話ルール」が定めるが、当該条文は本文の末尾寄りに
    位置するため、最初の応答を生成する時点では冒頭の記述より参照から漏れやすい。同じ規定を冒頭の1文へ
    置き、応答の生成より前に判断入力へ入る位置を確保する。委譲先は当該規定の対象外のため追加しない。

    常駐処理が渡した追加指示も、メインだけが受け取る入力として先頭へ置く。委譲先は元の作業の一部を
    担うに過ぎず、この指示の宛先ではない。
    """
    parts: list[str] = []
    if not delegated:
        instruction = os.environ.get(PROCESS_LOOP_INSTRUCTION_ENV, "").strip()
        if instruction:
            parts.append(
                xml_message(
                    PROCESS_LOOP_INSTRUCTION_ELEMENT,
                    instruction,
                    {
                        "from": "agent-toolkit/process-loop",
                        "origin": "user",
                        "source": "atk wi process-loop instruct",
                        "scope": "element body",
                    },
                )
            )
    normative_parts: list[str] = []
    if not delegated:
        parts.append(_llm_notice(RESPONSE_LANGUAGE_NOTICE))
    if source == "compact":
        parts.append(_llm_notice(QUALITY_CHECKPOINT_NOTICE))
    if not delegated:
        parts.append(_llm_notice(ASK_USER_QUESTION_CHECKLIST))
        normative_parts.append(MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip("\n"))
        if host == "claude":
            normative_parts.append(MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip("\n"))
    if normative_parts:
        parts.append(_normative_context("\n\n".join(normative_parts), kind=NORMATIVE_KIND_MAIN))
    return "\n\n".join(parts) or None


def _normative_context(body: str, *, kind: str) -> str:
    """規範本文へ生成主体と種別を持つ境界を付ける。"""
    return xml_message(NORMATIVE_ELEMENT, body, {"source": NORMATIVE_SOURCE, "kind": kind})


def compose_subagent_start(*, host: str) -> str:
    """SubagentStartへ追加する本文を返す。"""
    parts = [SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip("\n")]
    if host == "claude":
        parts.append(SUBAGENT_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip("\n"))
    return _normative_context("\n\n".join(parts), kind=NORMATIVE_KIND_SUBAGENT)


def _parse_payload(payload_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError("フック入力JSONの解析に失敗した") from exc
    if not isinstance(payload, dict):
        raise ValueError("フック入力はJSON objectである必要がある")
    return payload


def main(payload_text: str, *, host: str = "claude") -> int:
    """SessionStart又はSubagentStartのpayloadを処理する。"""
    payload = _parse_payload(payload_text)
    event_name = payload.get("hook_event_name")
    if event_name == "SessionStart":
        source = payload.get("source")
        if not isinstance(source, str):
            raise ValueError("sourceは文字列である必要がある")
        content = compose_session_start(source, delegated=is_delegated(os.environ), host=host)
        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            try:
                session_temp = managed_temp.create_managed_temp(
                    SESSION_TEMP_PREFIX,
                    session_id=session_id,
                )
            except (managed_temp.ManagedTempError, OSError):
                pass
            else:
                temp_context = _llm_notice(f"このセッションの管理対象一時領域: {session_temp}")
                content = f"{content}\n\n{temp_context}" if content else temp_context
    elif event_name == "SubagentStart":
        content = compose_subagent_start(host=host)
    else:
        raise ValueError("hook_event_nameはSessionStart又はSubagentStartである必要がある")

    if content is not None:
        # 本文は区分ごとに境界を持つため、全体を重ねて囲まない。
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event_name,
                        "additionalContext": content,
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0
