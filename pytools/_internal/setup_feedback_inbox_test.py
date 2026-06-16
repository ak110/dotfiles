"""pytools._internal.setup_feedback_inbox のテスト。

対象ホスト・非対象ホスト・フラグ有無の組み合わせ（同値分割・境界値分析）を網羅する。
"""

from pathlib import Path

import pytest

from pytools._internal import setup_feedback_inbox


@pytest.mark.parametrize(
    ("hostname", "flag_exists_initially", "expected_return", "expected_flag_exists_after"),
    [
        pytest.param("stheno", False, True, True, id="target-host-flag-absent"),
        pytest.param("euryale", True, False, True, id="target-host-flag-present"),
        pytest.param("other-host", True, True, False, id="non-target-host-flag-present"),
        pytest.param("other-host", False, False, False, id="non-target-host-flag-absent"),
        pytest.param("circe.local", False, True, True, id="fqdn-suffix-stripped"),
        pytest.param("STHENO", False, True, True, id="uppercase-hostname"),
        pytest.param("Circe", False, True, True, id="mixed-case-hostname"),
    ],
)
def test_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    hostname: str,
    flag_exists_initially: bool,
    expected_return: bool,
    expected_flag_exists_after: bool,
) -> None:
    """ホスト名とフラグ有無の組み合わせに応じてフラグ生成・削除・戻り値が正しく動作する。"""
    flag = tmp_path / ".config" / "agent-toolkit" / "feedback-inbox.enabled"
    if flag_exists_initially:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_bytes(b"")
    monkeypatch.setattr(setup_feedback_inbox.socket, "gethostname", lambda: hostname)
    monkeypatch.setattr(setup_feedback_inbox, "_FLAG_PATH", flag)

    result = setup_feedback_inbox.run()

    assert result is expected_return
    assert flag.exists() is expected_flag_exists_after
