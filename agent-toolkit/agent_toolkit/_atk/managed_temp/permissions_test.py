"""権限回復の所有境界、保持時の復元と秒精度の掃引を検証する。"""

from __future__ import annotations

import datetime
import os
import pathlib
import stat
import types
import typing

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp import inventory, permissions, registry
from agent_toolkit._common import plugin_root_leases


@pytest.mark.skipif(os.name != "posix", reason="POSIX所有者の拒否をCLIと起動時掃引へ通す")
@pytest.mark.parametrize("route", ["cleanup", "startup"])
def test_foreign_owner_diagnostic_preserves_tree_through_public_entry(
    route: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """所有者の観測だけを差し替え、対象・uid・次操作を示してmodeと登録を保持する。"""
    target = managed_temp.create_managed_temp("foreign-owner")
    locked = target / "foreign"
    locked.mkdir()
    (locked / "payload").write_text("保持", encoding="utf-8")
    older = 1_000_000_000
    for child in (locked / "payload", locked, target / ".agent-toolkit-managed-temp.json", target):
        os.utime(child, (older, older))
    locked.chmod(0)
    require_owner = permissions._require_owner  # pylint: disable=protected-access
    foreign_uid = os.geteuid() + 1

    def foreign_owner(metadata: os.stat_result, path: pathlib.Path | str) -> None:
        if pathlib.Path(path).name == locked.name:
            values = list(metadata)
            values[4] = foreign_uid
            metadata = os.stat_result(values)
        require_owner(metadata, path)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(permissions, "_require_owner", foreign_owner)
            if route == "cleanup":
                assert managed_temp.main(["cleanup", "--path", str(target)]) == 2
            else:
                with pytest.raises(SystemExit) as completed:
                    atk.main(["managed-temp", "list"])
                assert completed.value.code == 0
        error = capsys.readouterr().err
        assert str(locked) in error and f"uid={foreign_uid}" in error
        assert "次の操作:" in error and "所有者" in error
        assert f"所有者uid={foreign_uid}の権限で{locked}を削除" in error
        assert ("失敗:" if route == "cleanup" else "警告:") in error
        assert "Traceback" not in error
        assert stat.S_IMODE(locked.stat().st_mode) == 0
        assert registry._registry_path(target).exists()  # pylint: disable=protected-access
    finally:
        managed_temp.cleanup_managed_temp(target)


@pytest.mark.skipif(os.name != "posix", reason="POSIXの権限ビットと記述子を検証する")
@pytest.mark.parametrize("mode", [0, 0o100, 0o300, 0o500, 0o555])
def test_directory_restores_retained_mode_after_success_and_failure(tmp_path: pathlib.Path, mode: int) -> None:
    """走査中の成功・中断の双方で保持対象の元modeへ戻る。"""
    root = tmp_path / "locked"
    root.mkdir()
    root.chmod(mode)
    try:
        with permissions.directory(root) as opened:
            assert stat.S_IMODE(os.fstat(opened).st_mode) & 0o700 == 0o700
        assert stat.S_IMODE(root.stat().st_mode) == mode
        with pytest.raises(RuntimeError), permissions.directory(root):
            raise RuntimeError("走査の中断")
        assert stat.S_IMODE(root.stat().st_mode) == mode
    finally:
        root.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="POSIXの所有者とsymlinkを検証する")
def test_directory_preserves_foreign_owner_and_symlink_target(tmp_path: pathlib.Path) -> None:
    """所有者不一致とsymlinkの拒否では、対象の権限を変えない。"""
    root = tmp_path / "locked"
    root.mkdir()
    root.chmod(0)
    metadata = list(root.lstat())
    metadata[4] = os.geteuid() + 1
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    try:
        with (
            pytest.raises(managed_temp.ManagedTempError, match="所有者"),
            permissions.directory(root, expected=os.stat_result(metadata)),
        ):
            pytest.fail("所有者の異なる対象を開いた")
        with pytest.raises(managed_temp.ManagedTempError), permissions.directory(link):
            pytest.fail("symlinkを追跡した")
        assert stat.S_IMODE(root.stat().st_mode) == 0
    finally:
        root.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="POSIXのO_PATHを使う置換検証")
def test_permission_recovery_changes_only_the_pinned_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """権限回復と再openの間で差し替わっても、リンク先のmodeを保つ。"""
    root = tmp_path / "locked"
    moved = tmp_path / "moved"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir(mode=0o500)
    root.chmod(0)
    original_chmod = os.chmod
    replaced = False

    def replace_after_chmod(path: typing.Any, mode: int, **kwargs: typing.Any) -> None:
        nonlocal replaced
        original_chmod(path, mode, **kwargs)
        if not replaced and str(path).startswith("/proc/self/fd/"):
            replaced = True
            root.rename(moved)
            root.symlink_to(outside, target_is_directory=True)

    try:
        monkeypatch.setattr(os, "chmod", replace_after_chmod)
        with pytest.raises((OSError, managed_temp.ManagedTempError)), permissions.directory(root):
            pytest.fail("置換後の対象を開いた")
        assert stat.S_IMODE(outside.stat().st_mode) == 0o500
        assert stat.S_IMODE(moved.stat().st_mode) == 0
    finally:
        original_chmod(moved if moved.exists() else root, 0o700)
        original_chmod(outside, 0o700)


def test_sweep_detects_new_registration_with_identical_directory_stat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rootの時刻とsizeが同じでも、新しい登録集合を掃引ゲートへ反映する。"""
    state = registry._state_root()  # pylint: disable=protected-access
    root = registry._temp_root()  # pylint: disable=protected-access
    original_stat = pathlib.Path.stat
    fixed = {path: original_stat(path) for path in (root, state)}

    def fixed_directory_stat(path: pathlib.Path, **kwargs: typing.Any) -> typing.Any:
        metadata = original_stat(path, **kwargs)
        if path not in fixed:
            return metadata
        value = types.SimpleNamespace(**{key: getattr(metadata, key) for key in dir(metadata) if key.startswith("st_")})
        for key in ("st_mtime_ns", "st_ctime_ns", "st_size", "st_nlink"):
            setattr(value, key, getattr(fixed[path], key))
        return value

    monkeypatch.setattr(pathlib.Path, "stat", fixed_directory_stat)
    now = datetime.datetime.now(datetime.UTC)
    assert not managed_temp.sweep_expired_managed_temp(now=now)
    target = managed_temp.create_managed_temp("same-second")
    older = (now - datetime.timedelta(days=8)).timestamp()
    for child in target.iterdir():
        os.utime(child, (older, older))
    os.utime(target, (older, older))
    assert target in managed_temp.sweep_expired_managed_temp(now=now)
    assert not target.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIXの保持対象のmodeを検証する")
def test_sweep_preserves_restricted_modes_when_nested_content_is_recent() -> None:
    """配下の更新によって保持する領域は走査後に元の権限へ戻る。"""
    target = managed_temp.create_managed_temp("retained-mode")
    locked = target / "locked"
    locked.mkdir()
    (locked / "recent").write_text("使用中", encoding="utf-8")
    now = datetime.datetime.now(datetime.UTC)
    older = (now - datetime.timedelta(days=8)).timestamp()
    os.utime(target / ".agent-toolkit-managed-temp.json", (older, older))
    os.utime(locked, (older, older))
    os.utime(target, (older, older))
    locked.chmod(0)
    try:
        assert target not in managed_temp.sweep_expired_managed_temp(now=now)
        assert stat.S_IMODE(locked.stat().st_mode) == 0
    finally:
        managed_temp.cleanup_managed_temp(target)


@pytest.mark.parametrize("stage", ["snapshot", "isolation", "deletion", "restoration"])
def test_cleanup_cli_reports_oserror_at_every_cleanup_stage(
    stage: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """隔離前から復元までのOS例外を、原因と次の操作を持つCLI失敗へ変える。"""
    target = managed_temp.create_managed_temp("cleanup-error")
    (target / "payload").write_text("保持", encoding="utf-8")

    def fail(*_args: object, **_kwargs: object) -> typing.NoReturn:
        raise PermissionError(f"{stage}の取得拒否")

    with monkeypatch.context() as patch:
        if stage == "snapshot":
            patch.setattr(inventory, "_tree_snapshot", fail)
        elif stage == "isolation":
            patch.setattr(inventory, "_cleanup_posix" if os.name == "posix" else "_cleanup_windows", fail)
        else:
            patch.setattr(inventory, "_clear_directory" if os.name == "posix" else "_remove_windows_quarantine", fail)
            if stage == "restoration":
                patch.setattr(inventory, "_restore_cleanup_state", fail)
        assert managed_temp.main(["cleanup", "--path", str(target)]) == 2
    error = capsys.readouterr().err
    assert str(target) in error
    assert f"{stage}の取得拒否" in error
    assert "失敗:" in error and "次の操作:" in error
    assert "Traceback" not in error
    managed_temp.cleanup_managed_temp(target)


@pytest.mark.skipif(os.name != "nt", reason="WindowsのReadonly属性を実機で検証する")
@pytest.mark.parametrize("force", [False, True])
def test_windows_readonly_tree_is_reclaimed_with_and_without_registration(force: bool) -> None:
    """Readonlyファイルとディレクトリを、通常回収と強制回収で削除する。"""
    target = managed_temp.create_managed_temp("readonly-force")
    nested = target / "nested"
    nested.mkdir()
    payload = nested / "payload"
    payload.write_text("再生成可能", encoding="utf-8")
    os.chmod(payload, stat.S_IREAD)
    os.chmod(nested, stat.S_IREAD)
    if force:
        (target / ".agent-toolkit-managed-temp.json").unlink()
    managed_temp.cleanup_managed_temp(target, force_remove=force)
    assert not target.exists()


def test_force_remove_preserves_children_of_a_live_plugin_root() -> None:
    """配布物の子を指定した強制回収も、親の使用中保護を保持する。"""
    area = managed_temp.create_managed_temp("agents-server-plugin-root-shared")
    held = area / "1.0.0"
    held.mkdir()
    protected_payload = held / "payload"
    protected_payload.write_text("使用中", encoding="utf-8")
    with plugin_root_leases.plugin_root_lock(area):
        plugin_root_leases.register_plugin_root_use(area)
    for target in (area, held):
        with pytest.raises(managed_temp.ManagedTempError, match="使用中"):
            managed_temp.cleanup_managed_temp(target, force_remove=True)
        assert protected_payload.read_text(encoding="utf-8") == "使用中"
