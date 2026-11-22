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
    roots = _repair(monkeypatch, windows_security.InheritanceRepair(restored=(tmp_path / "services",)))
    assert _step.run() == post_apply_outcome.PostApplyOutcome(changed=True)
    assert roots == [state_paths.state_dir()]


def test_nothing_to_repair_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _repair(monkeypatch, windows_security.InheritanceRepair())
    assert _step.run() == post_apply_outcome.PostApplyOutcome()


@pytest.mark.parametrize("elevated", [False, True])
def test_blocked_items_fail_with_next_step(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, elevated: bool) -> None:
    """戻せない項目は理由とともに失敗とし、非昇格の実行では昇格した`update-dotfiles`の実行を案内する。"""
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


def test_many_blocked_items_show_count_and_first_items(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """戻せない項目が多い場合は、全件数と先頭の項目だけを失敗に示す。"""
    blocked = tuple((tmp_path / f"item-{index}", "アクセスが拒否されました。") for index in range(8))
    _repair(monkeypatch, windows_security.InheritanceRepair(blocked=blocked))
    monkeypatch.setattr(_step.windows_security, "is_elevated", lambda: False)
    outcome = _step.run()
    assert outcome.failure is not None
    assert "8件" in outcome.failure
    assert log_format.home_short(tmp_path / "item-4") in outcome.failure
    assert log_format.home_short(tmp_path / "item-5") not in outcome.failure
    assert "ほか3件" in outcome.failure
