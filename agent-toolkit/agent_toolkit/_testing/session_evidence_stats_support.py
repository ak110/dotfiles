"""セッション振り返りの試験（session_evidence_stats_support）。"""

from __future__ import annotations

import json
import pathlib


def _codex_token_count_entry(timestamp: str, usage: dict[str, int], cumulative: dict[str, int] | None = None) -> dict:
    """Codexの`token_count`エントリを作成する。

    `usage`はそのリクエストの実消費（`last_token_usage`）、`cumulative`はセッション累積
    （`total_token_usage`）とする。`cumulative`を省略した場合は同じ値を与える。
    """
    return {
        "type": "response_item",
        "timestamp": timestamp,
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": usage if cumulative is None else cumulative, "last_token_usage": usage},
        },
    }


def _write_rollout(codex_home: pathlib.Path, thread_id: str, usages: list[tuple[str, dict[str, int]]]) -> None:
    """`CODEX_HOME`配下へthreadIdに対応するrolloutを書き込む。

    `usages`の各要素はそのリクエストの実消費（`last_token_usage`）とし、
    `total_token_usage`にはそこまでの累積合計を与える。
    """
    rollout_dir = codex_home / "sessions" / "2026" / "08" / "19"
    rollout_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    cumulative: dict[str, int] = {}
    for timestamp, usage in usages:
        for key, value in usage.items():
            cumulative[key] = cumulative.get(key, 0) + value
        entries.append(
            {
                "timestamp": timestamp,
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": dict(cumulative), "last_token_usage": usage},
                },
            }
        )
    (rollout_dir / f"rollout-test-{thread_id}.jsonl").write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n",
        encoding="utf-8",
    )
