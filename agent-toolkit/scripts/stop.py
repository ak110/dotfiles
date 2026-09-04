#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py[linkify]>=4.0.0", "platformdirs>=4.0", "pyyaml"]
# ///
"""Stopイベントの判定を順に実行し、単一の応答へ集約する共通入口。

各判定の例外は判定単位で隔離し、残りの判定を継続する。遮断理由と通知本文は判定順を
保って空行で連結する。遮断が1件以上ある場合は通知も遮断理由へ含め、通知だけの場合は
`hookSpecificOutput.additionalContext`へ集約する。

委譲先での実行可否: 各判定モジュールが個別に適用可否を判断するため、共通入口自体は除外せず実行できる。
"""

import importlib
import json
import sys

CHECK_MODULE_NAMES = (
    "autonomous_exit",
    "plan_save_advisor",
    "agents_server_session_advisor",
    "pending_question_advisor",
)


def evaluate(payload_text: str) -> dict[str, object]:
    """各Stop判定を実行し、集約したhook応答を返す。"""
    block_reasons: list[str] = []
    notifications: list[str] = []
    for module_name in CHECK_MODULE_NAMES:
        try:
            module = importlib.import_module(module_name)
            decision, body = module.evaluate(payload_text)
        except Exception as exc:  # noqa: BLE001 -- 1判定の故障で他の終了判定を失わないため広範に捕捉
            print(
                f"[stop/{module_name}] 想定外エラー: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue
        if decision == "block":
            block_reasons.append(body)
        elif decision == "notify":
            notifications.append(body)

    if block_reasons:
        return {"decision": "block", "reason": "\n\n".join([*block_reasons, *notifications])}
    if notifications:
        return {
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": "\n\n".join(notifications),
            }
        }
    return {}


def _approve() -> None:
    """空のapprove応答を返す。"""
    print(json.dumps({}, ensure_ascii=False))


def main(payload_text: str) -> int:
    """Stop判定の集約結果を標準出力へ返す。"""
    print(json.dumps(evaluate(payload_text), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin.read()))
