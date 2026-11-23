"""PreToolUseの警告一覧を一つのadditionalContextへ整形する。"""

from __future__ import annotations

import re
from collections.abc import Sequence
lazy import hashlib

from agent_toolkit._common import message_format as _message_format
from agent_toolkit._common.next_action import recover_from_failure
from agent_toolkit._common.persisted_output_reference import CODEX_NOTICE_REFERENCE_PREFIX
lazy from agent_toolkit._atk import managed_temp
lazy from agent_toolkit._common.atomic_file import atomic_write
lazy from agent_toolkit._hooks import notice

_COUNT_HEADER_RE = re.compile(r"\A警告: \d+件\n\n")
_NOTICE_PREFIX_RE = re.compile(rf'(?m)^<(?:{_message_format.AUTO_ELEMENT_NAME_PATTERN})\b[^>]*\bkind="warn"[^>]*>')
# Codexのデフォルト約2,500トークンに対し、byte単位まで分割される入力と標識の余裕を見込む。
# 通知全体のUTF-8バイト数で保守的に抑え、モデルごとのtokenizerへ依存しない。
CODEX_CONTEXT_BYTES = 2000


def format_warning_context(warnings: Sequence[str]) -> str:
    if not warnings:
        return ""
    bodies = [_COUNT_HEADER_RE.sub("", warning, count=1) for warning in warnings]
    body = "\n\n".join(bodies)
    count = sum(max(1, len(_NOTICE_PREFIX_RE.findall(warning))) for warning in bodies)
    if count == 1:
        return body
    return f"警告: {count}件\n\n{body}"


def bounded_codex_context(context: str, *, session_id: str, reserved_bytes: int = 0, agent_id: str = "main") -> str:
    encoded = context.encode("utf-8")
    limit = CODEX_CONTEXT_BYTES - reserved_bytes
    if len(encoded) <= limit:
        return context
    receipts = notice.delivery_receipts(context)
    read_manual = "agent-toolkit:atk-mode/playbooks/managed-temp.md"
    with notice.notice_cycle(session_id, "codex-pretooluse-context", agent_id):
        format_notice = notice.formatter("pretooluse")
        if not session_id:
            return format_notice(
                "通知の詳細を保存するsession_idを取得できなかった。元の通知は未配送である。",
                tag="warn",
                removable_cause=False,
                fix=(
                    recover_from_failure(
                        component="通知の保存",
                        evidence="診断と未配送の通知",
                        first_step=f"`{read_manual}` を読み、入力のsession_idを確認する",
                        retry="入力を修復してから、止められた元の操作を再試行する",
                    )
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
                    recover_from_failure(
                        component="通知の保存",
                        evidence="診断と未配送の通知",
                        first_step=f"`{read_manual}` を読み、保存先の権限と状態を確認する",
                        retry="保存先を修復してから、止められた元の操作を再試行する",
                    )
                ),
            )
        reference = format_notice(
            f"{CODEX_NOTICE_REFERENCE_PREFIX}{path}",
            tag="warn",
            removable_cause=False,
            fix="保存した通知を取得し、必要な対処を行え。",
        )
        if len(reference.encode("utf-8")) > limit:
            return format_notice(
                "通知詳細の保存先が通知枠を超える長さになった。元の通知は未配送である。",
                tag="warn",
                removable_cause=False,
                fix=(
                    recover_from_failure(
                        component="通知の配送",
                        evidence="診断と未配送の通知",
                        first_step=f"`{read_manual}` を読み、絶対パスが通知枠に収まる配置を確認する",
                        retry="配置を修復してから、止められた元の操作を再試行する",
                    )
                ),
            )
        for receipt in receipts:
            notice.stage_delivery(reference, receipt["session_id"], receipt["field"], receipt["key"], receipt["value"])
        return reference
