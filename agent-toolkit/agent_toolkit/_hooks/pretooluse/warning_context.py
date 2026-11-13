"""PreToolUseの警告一覧を一つのadditionalContextへ整形する。"""

from __future__ import annotations

import re
from collections.abc import Sequence
lazy import hashlib
lazy import os
lazy import shlex

from agent_toolkit._common import message_format as _message_format
from agent_toolkit._common.persisted_output_reference import CODEX_NOTICE_REFERENCE_PREFIX
lazy from agent_toolkit._atk import managed_temp
lazy from agent_toolkit._common.atomic_file import atomic_write
lazy from agent_toolkit._hooks import notice, plugin_resources, rules_context

_COUNT_HEADER_RE = re.compile(r"\A警告: \d+件\n\n")
_NOTICE_PREFIX_RE = re.compile(rf'(?m)^<(?:{_message_format.AUTO_ELEMENT_NAME_PATTERN})\b[^>]*\bkind="warn"[^>]*>')
# Codexのデフォルト約2,500トークンに対し、byte単位まで分割される入力と標識の余裕を見込む。
# 通知全体のUTF-8バイト数で保守的に抑え、モデルごとのtokenizerへ依存しない。
CODEX_CONTEXT_BYTES = 2000


def format_warning_context(warnings: Sequence[str]) -> str:
    """警告本文を結合し、複数件では総数を先頭へ付ける。"""
    if not warnings:
        return ""
    bodies = [_COUNT_HEADER_RE.sub("", warning, count=1) for warning in warnings]
    body = "\n\n".join(bodies)
    count = sum(max(1, len(_NOTICE_PREFIX_RE.findall(warning))) for warning in bodies)
    if count == 1:
        return body
    return f"警告: {count}件\n\n{body}"


def bounded_codex_context(context: str, *, session_id: str, reserved_bytes: int = 0, agent_id: str = "main") -> str:
    """最終通知を短い参照へ変換し、全文は同じsessionの一時領域へ保存する。"""
    encoded = context.encode("utf-8")
    limit = CODEX_CONTEXT_BYTES - reserved_bytes
    if len(encoded) <= limit:
        return context
    receipts = notice.delivery_receipts(context)
    manual = str(plugin_resources.plugin_root() / "skills/managed-temp/SKILL.md")
    read_manual = (
        "Get-Content -LiteralPath '" + manual.replace("'", "''") + "' -Encoding utf8"
        if os.name == "nt"
        else "cat -- " + shlex.quote(manual)
    )
    # 案内の提示と資料本文の取得は別の記録。参照の提示では本文を取得済みにしない。
    with notice.notice_cycle(session_id, "codex-pretooluse-context", agent_id):
        format_notice = notice.formatter("pretooluse")
        if not session_id:
            return format_notice(
                "通知の詳細を保存するsession_idを取得できなかった。元の通知は未配送である。",
                tag="warn",
                removable_cause=False,
                fix=(
                    f"`{read_manual}`で保存先の解決条件を読め。"
                    "起動入力の`session_id`が不足し通知が未配送であることを委譲元（無ければユーザー）へ報告し、"
                    "入力の修復後に元の操作を再試行せよ。"
                ),
            )
        try:
            directory = managed_temp.resolve_session_temp(session_id)
            path = directory / f"codex-pretooluse-notices-{hashlib.sha256(encoded).hexdigest()}.md"
            atomic_write(path, context)
        except (OSError, managed_temp.ManagedTempError) as error:
            return format_notice(
                f"通知詳細の一時保存に失敗した（{type(error).__name__}）。元の通知は未配送である。",
                tag="warn",
                removable_cause=False,
                fix=(
                    f"`{read_manual}`で保存先と権限の条件を読め。"
                    "通知詳細が未配送であることとこの診断を委譲元（無ければユーザー）へ報告し、"
                    "保存先の修復後に元の操作を再試行せよ。"
                ),
            )
        read_saved = (
            "Get-Content -LiteralPath '" + str(path).replace("'", "''") + "' -Encoding utf8"
            if os.name == "nt"
            else "cat -- " + shlex.quote(str(path))
        )
        reference = format_notice(
            f"{CODEX_NOTICE_REFERENCE_PREFIX}{path}",
            tag="warn",
            removable_cause=False,
            fix=f"`{read_saved}`を実行し、必要な案内を読め。",
        )
        if len(reference.encode("utf-8")) > limit:
            return format_notice(
                "通知詳細の保存先が通知枠を超える長さになった。元の通知は未配送である。",
                tag="warn",
                removable_cause=False,
                fix=(
                    f"`{read_manual}`で保存先の規定を読め。"
                    "保存先の絶対パスを通知枠へ収める配置が必要なことを委譲元（無ければユーザー）へ報告し、"
                    "配置の修復後に元の操作を再試行せよ。"
                ),
            )
        for receipt in receipts:
            if receipt["field"] != rules_context.OPERATION_SKILL_READY_KEY or receipt["key"].startswith("read-guidance:"):
                notice.stage_delivery(reference, receipt["session_id"], receipt["field"], receipt["key"], receipt["value"])
        return reference
