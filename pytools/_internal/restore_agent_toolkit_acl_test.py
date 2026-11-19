"""pytools._internal.restore_agent_toolkit_acl のテスト。"""

import pathlib

import pytest
from agent_toolkit._common import state_paths, windows_security

from pytools._internal import log_format, post_apply_outcome
from pytools._internal import restore_agent_toolkit_acl as _step


def _repair(monkeypatch: pytest.MonkeyPatch, result: windows_security.InheritanceRepair) -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []

    def restore(root: pathlib.Path) -> windows_security.InheritanceRepair:
        roots.append(root)
        return result

    monkeypatch.setattr(_step.windows_security, "restore_inheritance", restore)
    return roots


def test_repairs_state_directory_and_reports_change(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """状態ディレクトリを対象に修復し、戻したディレクトリがあれば更新と数える。"""
    roots = _repair(monkeypatch, windows_security.InheritanceRepair(restored=(tmp_path / "services",)))
    assert _step.run() == post_apply_outcome.PostApplyOutcome(changed=True)
    assert roots == [state_paths.state_dir()]


def test_nothing_to_repair_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """修復対象が無ければ変更なしで終える。"""
    _repair(monkeypatch, windows_security.InheritanceRepair())
    assert _step.run() == post_apply_outcome.PostApplyOutcome()


@pytest.mark.parametrize("elevated", [False, True])
def test_blocked_directories_fail_with_next_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, elevated: bool
) -> None:
    """戻せないディレクトリは理由とともに失敗とし、非昇格の実行では昇格した`update-dotfiles`の実行を案内する。"""
    first, second = tmp_path / "services", tmp_path / "managed-temp"
    _repair(
        monkeypatch,
        windows_security.InheritanceRepair(
            restored=(tmp_path / "run-skill",), blocked=((first, "アクセスが拒否されました。"), (second, "x"))
        ),
    )
    monkeypatch.setattr(_step.windows_security, "is_elevated", lambda: elevated)
    outcome = _step.run()
    assert outcome.changed
    assert outcome.failure is not None
    assert f"{log_format.home_short(first)}（アクセスが拒否されました。）" in outcome.failure
    assert f"{log_format.home_short(second)}（x）" in outcome.failure
    assert outcome.next_action is not None
    assert ("管理者として起動した端末で`update-dotfiles`" in outcome.next_action) is not elevated
