from __future__ import annotations

import contextvars
import re
from typing import Any

from agent_toolkit._atk import session_record_format as _session_record_format
from agent_toolkit._common import message_format as _message_format
from agent_toolkit._common import runtime_inserted as _runtime_inserted
from agent_toolkit._common.runtime_inserted import is_runtime_inserted_text as _is_runtime_inserted_text

_MAX_TEXT_LENGTH = _session_record_format.SUMMARY_TEXT_LIMIT


_OMISSION_MARK = _session_record_format.OMISSION_MARK


# Claude Codeのサブエージェントが報告本文を委譲元へ渡すツールの名前。
_HANDBACK_TOOL = "SubagentHandback"


_LINE_NUMBER_PREFIX = re.compile(r"^\s*\d+\t(.*)$")


_SKILL_INVOCATION_PREFIX = "Base directory for this skill: "


_SELF_SCRIPT_STEM = "session_review_evidence"


_PATH_SEPARATORS = re.compile(r"[/\\]")


_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


_SHELL_NAMES = frozenset({"sh", "bash", "zsh"})


_SCRIPT_RUNNERS = frozenset({"uv", "uvx", "env"})


_HOOK_RECORD_TYPES = frozenset({"hook_additional_context", "hook_system_message", "hook_blocking_error", "hook_success"})


def _is_hook_record(value: dict[str, Any]) -> bool:
    return isinstance(value.get("type"), str) and value["type"] in _HOOK_RECORD_TYPES


_HOOK_NOTICE_MARKER = re.compile(
    rf"(?:<(?:{_message_format.AUTO_ELEMENT_NAME_PATTERN})"
    r'(?=[^>]*\ssource="(?P<hook_xml>[^"]+)")(?=[^>]*\skind="(?P<tag_xml>[^"]+)")[^>]*>|'
    r"\[auto-generated:\s*(?P<hook_legacy>[^\]]*?)\s*\](?:\s*\[(?P<tag_legacy>[^\]]*)\])?)"
)


# 本文の可変部（語頭から始まるパス、session識別子などのUUID、UWI識別子・行番号・トークン数などの数値）。
# 同じ種別が複数の種別キーへ分かれないよう置換する。
# UUIDは英字を含み数値の置換だけでは1件ごとに別の種別へ分かれるため、数値より先に置換する。
# パスは語頭に限定するが、数値列は語頭・語中を問わず置換するため、`github.com/ak110/dotfiles`のような
# 固定の識別子も数値部分が置換される。
_CANDIDATE_VARIABLE = re.compile(
    r"""(?<![^\s(\[<'"`])~?/[^\s`'"]+|\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b|\d+"""
)


_CANDIDATE_VARIABLE_PLACEHOLDER = "<var>"


_HOOK_FAILURE_PREFIX = re.compile(r"^[^\r\n]*?\bhook error:\s*(?:\[[^\r\n]*?\]:\s*)?", re.IGNORECASE)


_FALLBACK_TEXT = (
    "記録は読み込めたが形式を判定できないため抽出証拠を生成できない。"
    "自身のコンテキストにある会話履歴から評価し、記録から取得できない範囲を未検証と明記すること。"
)


_METADATA_KEYS = frozenset(
    {
        "uuid",
        "parentUuid",
        "leafUuid",
        "sessionId",
        "session_id",
        "bridgeSessionId",
        "requestId",
        "promptId",
        "messageId",
        "id",
        "call_id",
        "tool_use_id",
        "toolUseID",
        "sourceToolUseID",
        "sourceToolAssistantUUID",
        "agentId",
        "taskId",
        "ownerAccountUuid",
        "ownerOrganizationUuid",
        "signature",
        "timestamp",
        "backupTime",
        "type",
        "subtype",
        "kind",
        "role",
        "status",
        "stop_reason",
        "stopReason",
        "sessionKind",
        "hookEvent",
        "hookEventName",
        "permissionMode",
        "mode",
        "userType",
        "entrypoint",
        "promptSource",
        # 実行環境・モデル設定
        "version",
        "model",
        "resolvedModel",
        "effort",
        "service_tier",
        "inference_geo",
        "speed",
        "cwd",
        "originalCwd",
        "preEnterOriginalCwd",
        "gitBranch",
        "originalBranch",
        "originalHeadCommit",
        "worktreePath",
        "worktreeName",
        "worktreeBranch",
    }
)


_BODY_KEYS = frozenset(
    {
        "input",
        "output",
        "arguments",
        "prompt",
    }
)


_TEXT_LIMIT: contextvars.ContextVar[int | None] = contextvars.ContextVar("_TEXT_LIMIT", default=_MAX_TEXT_LENGTH)
"""`_clip`が上限を省略された場合に使う本文の上限。`None`は切り詰めないことを表す。

会話の流れは発話の全文を保持するため、イベントの生成を共有しつつ上限だけを外す。
"""


def _clip(text: str, limit: int | None = None) -> str:
    normalized = text.strip()
    effective = _TEXT_LIMIT.get() if limit is None else limit
    if effective is None or len(normalized) <= effective:
        return normalized
    return normalized[:effective] + _OMISSION_MARK


_PRESERVED_MARKERS = ("続行できない区分:", "続行できない理由:", "想定外事象:")
"""短縮の外でも全文を保持する返却の標識行。

候補の抽出は短縮後の本文を入力にするため、返却末尾の標識行が省略区間に入ると、
続行不能の返却（`session_evidence_candidates.py`の`_is_escalation_return`）や想定外事象が候補から消える。
2,000文字を超える正常形式の返却の末尾に置かれた`続行できない理由:`を見落とした実例がある。
改善点は箇条書きの項目記号付きでも報告され、コード例・引用の中にも同じ標識が現れるため、
`runtime_inserted.reported_improvement_lines`が実報告と判定した行だけを保持する。
"""


def _clip_assistant_text(text: str) -> str:
    normalized = text.strip()
    clipped = _clip(normalized)
    if clipped == normalized:
        return clipped
    boundary = len(clipped) - len(_OMISSION_MARK)
    prefix = normalized[:boundary]
    kept: list[str] = []
    reported = set(_runtime_inserted.reported_improvement_lines(normalized))
    start = 0
    for line in normalized.splitlines(keepends=True):
        end = start + len(line)
        preserved = line.lstrip().startswith(_PRESERVED_MARKERS) or (
            _runtime_inserted.improvement_line(line.rstrip("\r\n")) in reported
        )
        if preserved and end > boundary:
            if start < boundary:
                prefix = normalized[:start]
            kept.append(line.rstrip("\r\n"))
        start = end
    if not kept:
        return clipped
    return prefix + _OMISSION_MARK + "\n" + "\n".join(kept)


class _DetailBudget:
    def __init__(self, limit: int) -> None:
        self.remaining = limit
        self.omitted = False

    def clip(self, text: str) -> str:
        normalized = text.strip()
        if len(normalized) <= self.remaining:
            self.remaining -= len(normalized)
            return normalized
        self.omitted = True
        if self.remaining < len(_OMISSION_MARK):
            return ""
        clipped = normalized[: self.remaining - len(_OMISSION_MARK)] + _OMISSION_MARK
        self.remaining -= len(clipped)
        return clipped


def _event(kind: str, text: str, *, tool: str | None = None) -> dict[str, Any] | None:
    runtime_inserted = kind == "user" and _is_runtime_inserted_text(text)
    clipped = _clip_assistant_text(text) if kind == "assistant" else _clip(text)
    if not clipped:
        return None
    event: dict[str, Any] = {"kind": kind, "text": clipped}
    if kind == "assistant":
        improvements = _runtime_inserted.reported_improvement_lines(text, deduplicate=False)
        if improvements:
            event["reported_improvements"] = improvements
    if kind == "user":
        event["runtime_inserted"] = runtime_inserted
    if tool:
        event["tool"] = tool
    return event


def _generated_user_event(event: dict[str, Any]) -> bool:
    return event.get("runtime_generated") is True or event.get("runtime_inserted") is True


_OfferedOption = tuple[str, str]
"""確認で提示した選択肢1件のlabelとdescription。descriptionを持たない選択肢は空文字列とする。"""
