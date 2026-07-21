"""pytools._internal.setup_review_balance_mode のテスト。

ホスト名判定・フラグ生成の詳細ロジックは`claude_common_test.py`の
`TestIsTargetHost`・`TestEnsureFlagFilePresent`で網羅済みのため、本ファイルでは
`run()`が対象ホスト判定とフラグ生成ヘルパーへ正しく配線されているかのみを確認する。
"""

from pathlib import Path

import pytest

from pytools._internal import setup_review_balance_mode


@pytest.mark.parametrize(
    ("hostname", "expected_return", "expected_flag_exists_after"),
    [
        pytest.param("stheno", True, True, id="target-host-flag-absent"),
        pytest.param("other-host", False, False, id="non-target-host-flag-absent"),
    ],
)
def test_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    hostname: str,
    expected_return: bool,
    expected_flag_exists_after: bool,
) -> None:
    """対象ホストか否かに応じて`ensure_flag_file_present`への配線が正しく動作する。"""
    flag = tmp_path / ".config" / "agent-toolkit" / "review-balance-mode.claude-heavy"
    monkeypatch.setattr(setup_review_balance_mode.socket, "gethostname", lambda: hostname)
    monkeypatch.setattr(setup_review_balance_mode, "_FLAG_PATH", flag)

    result = setup_review_balance_mode.run()

    assert result is expected_return
    assert flag.exists() is expected_flag_exists_after
