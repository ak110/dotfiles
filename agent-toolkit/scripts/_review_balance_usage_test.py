"""_review_balance_usage のテスト。"""

from __future__ import annotations

import json
from pathlib import Path

import _review_balance_usage
import pytest


@pytest.fixture(autouse=True)
def _redirect_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """状態ファイルの参照先をテスト用の一時ディレクトリへ差し替える。"""
    monkeypatch.setattr(_review_balance_usage, "_CLAUDE_USAGE_PATH", tmp_path / "claude-usage.json")
    monkeypatch.setattr(
        _review_balance_usage,
        "_CODEX_USAGE_CACHE_PATH",
        tmp_path / "codex-usage-cache.json",
    )
    monkeypatch.setattr(
        _review_balance_usage,
        "_FLAG_PATH",
        tmp_path / "review-balance-mode.claude-heavy",
    )


def test_snapshot_reflects_flag_and_usage_files(tmp_path: Path) -> None:
    """状態ファイルが存在する場合、値を反映したスナップショットを返すこと。"""
    (tmp_path / "claude-usage.json").write_text(
        json.dumps(
            {
                "five_hour_used_pct": 15.0,
                "seven_day_used_pct": 40.0,
                "seven_day_resets_at_unix": 1783000000,
                "pay_as_you_go": False,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "codex-usage-cache.json").write_text(
        json.dumps(
            {
                "five_hour_used_pct": 25.0,
                "seven_day_used_pct": 28.0,
                "seven_day_resets_at_unix": 1784000000,
                "pay_as_you_go": False,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "review-balance-mode.claude-heavy").write_bytes(b"")

    result = _review_balance_usage.snapshot()

    assert result["claude_seven_day_pct"] == 40.0
    assert result["claude_seven_day_resets_at_unix"] == 1783000000
    assert result["codex_seven_day_pct"] == 28.0
    assert result["codex_seven_day_resets_at_unix"] == 1784000000
    assert result["mode"] == "claude-heavy"


def test_snapshot_defaults_when_files_missing() -> None:
    """状態ファイルが存在しない場合、Noneとcodex-heavyを返すこと。"""
    result = _review_balance_usage.snapshot()

    assert result["claude_seven_day_pct"] is None
    assert result["codex_pay_as_you_go"] is None
    assert result["mode"] == "codex-heavy"


def test_snapshot_ignores_corrupt_json(tmp_path: Path) -> None:
    """JSON解析に失敗した場合はNoneへフォールバックすること。"""
    (tmp_path / "claude-usage.json").write_text("{invalid", encoding="utf-8")

    result = _review_balance_usage.snapshot()

    assert result["claude_seven_day_pct"] is None
