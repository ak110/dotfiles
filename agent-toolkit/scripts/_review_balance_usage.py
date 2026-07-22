"""レビューバランスモードの使用量スナップショットを読み取るヘルパー。

`rust/claude-statusline`側（`balance_mode.rs`）が出力する状態ファイルを読み取るだけの
薄いラッパーであり、Codexのrolloutログ解析ロジックはRust側に一本化する
（Python側での二重実装を避ける）。

`pytools._internal`配下には依存しない（agent-toolkitプラグインの配布物独立性を保つため）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path.home() / ".config" / "agent-toolkit"
_CLAUDE_USAGE_PATH = _CONFIG_DIR / "claude-usage.json"
_CODEX_USAGE_CACHE_PATH = _CONFIG_DIR / "codex-usage-cache.json"
_FLAG_PATH = _CONFIG_DIR / "review-balance-mode.claude-heavy"


def _load(path: Path) -> dict[str, Any]:
    """JSONファイルをdictとして読み込む。存在しない・不正な場合は空dictを返す。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def snapshot() -> dict[str, Any]:
    """Claude/Codexの使用量観測値と現行モードを1つのdictへまとめて返す。

    `process-loop`のセッション前後観測ログへ`**snapshot()`で展開して記録する用途を想定する。
    """
    claude = _load(_CLAUDE_USAGE_PATH)
    codex = _load(_CODEX_USAGE_CACHE_PATH)
    return {
        "claude_five_hour_pct": claude.get("five_hour_used_pct"),
        "claude_seven_day_pct": claude.get("seven_day_used_pct"),
        "claude_seven_day_resets_at_unix": claude.get("seven_day_resets_at_unix"),
        "claude_pay_as_you_go": claude.get("pay_as_you_go"),
        "codex_five_hour_pct": codex.get("five_hour_used_pct"),
        "codex_seven_day_pct": codex.get("seven_day_used_pct"),
        "codex_seven_day_resets_at_unix": codex.get("seven_day_resets_at_unix"),
        "codex_pay_as_you_go": codex.get("pay_as_you_go"),
        "mode": "claude-heavy" if _FLAG_PATH.is_file() else "codex-heavy",
    }
