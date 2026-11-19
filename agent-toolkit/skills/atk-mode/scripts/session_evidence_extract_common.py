"""記録からの証拠イベント抽出（session_evidence_extract_common）。"""

from __future__ import annotations

import contextvars
import re
from typing import Any

from agent_toolkit._atk import session_record_format as _session_record_format
from agent_toolkit._common import message_format as _message_format
from agent_toolkit._common import runtime_inserted as _runtime_inserted
from agent_toolkit._common.runtime_inserted import is_runtime_inserted_text as _is_runtime_inserted_text

# 本文の上限と省略の標識は、`--verbose`を付けない`atk agents logs`のtext表示と共有する。
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
    """hook実行の記録に当たるかを`type`の値で判定する。

    `type`の値は文字列とは限らない。ツール定義を含む記録では
    `attachment.tools[].schema.input_schema.properties`配下に`type`という名前の
    プロパティ定義が現れ、その値はJSON Schemaのdictになる。
    集合に含まれるか判定する前に文字列であることを確かめないと`TypeError`で走査が止まる。
    """
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
        # 識別子・署名
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
        # 時刻
        "timestamp",
        "backupTime",
        # 形式・区分の名称
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
        # 自由形式の本文を保持するフィールド。内部のキー名はユーザーの入力に由来する
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
    """証拠の意味を保ったまま巨大な本文を制限する。"""
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
    """本文の先頭を短縮し、省略区間にある返却の標識行は全文で保持する。"""
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
    """1エントリの詳細出力が共有する残り文字数と、省略の発生有無。

    詳細は`--detail`の指定行ごとに複数の文字列へ分かれるため、上限を文字列単位で適用すると
    1エントリの出力量が指定上限を超える。残り予算を出現順に配分して本文の合計を上限内へ収める。
    省略標識も返す文字数として予算から差し引くため、文字列値の個数が増えても合計は上限を超えない。
    予算が標識の長さに満たない時点以降の本文は空文字列となり、本文が元から空である場合と
    文字列単体では区別できない。この区別のため、省略が1回でも生じたかを`omitted`が保持し、
    呼び出し側がそのエントリのイベントへ標識として付ける。
    """

    def __init__(self, limit: int) -> None:
        self.remaining = limit
        self.omitted = False

    def clip(self, text: str) -> str:
        """残り予算の範囲で本文を制限し、返した文字数を予算から差し引く。

        予算を超える非空の本文は、省略標識を含めて残り予算へ収まる範囲まで切り詰める。
        省略標識を置く余地も無い場合は空文字列を返す。いずれの場合も省略の発生を記録する。
        """
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
    """共通イベントを生成し、アシスタント本文の省略区間の改善点を候補判定まで保つ。"""
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
    """人間入力の抽出で除く生成ユーザーイベントかを、原文由来の標識で返す。

    agents_serverの配送境界に包まれた委譲元の依頼も生成扱いになる。
    委譲元の依頼を必要とする消費側は、配送境界を別に判定する。
    """
    return event.get("runtime_generated") is True or event.get("runtime_inserted") is True


_OfferedOption = tuple[str, str]
"""確認で提示した選択肢1件のlabelとdescription。descriptionを持たない選択肢は空文字列とする。"""
