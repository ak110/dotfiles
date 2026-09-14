"""check_update_dotfiles_upgrade.pyの単体テスト。"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import types

import pytest

_SCRIPT = pathlib.Path(__file__).with_name("check_update_dotfiles_upgrade.py")


def _load_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("check_update_dotfiles_upgrade", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


upgrade = _load_module()


def test_cli_outputs_japanese_when_default_stream_encoding_is_not_utf8() -> None:
    """非UTF-8の既定ストリームでも日本語のCLI出力を維持する。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--help"],
        check=False,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0
    assert isinstance(result.stdout, bytes)
    assert result.stdout.decode("utf-8").find("約3日前のdotfiles") >= 0
    assert not result.stderr


def test_resolve_old_commit_uses_head_timestamp_minus_72_hours(tmp_path: pathlib.Path) -> None:
    """旧commit探索の基準を現行commit時刻の72時間前に固定する。"""
    calls: list[list[str]] = []

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        output = "1000000\n" if "show" in arguments else "old-oid\n"
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    assert upgrade.resolve_old_commit(tmp_path, "current-oid", runner=runner) == "old-oid"
    assert "--before=@740800" in calls[1]


def test_resolve_old_commit_rejects_missing_history(tmp_path: pathlib.Path) -> None:
    """対象時刻以前の履歴が無い場合は検証不能として失敗する。"""
    outputs = iter(("1000000\n", ""))

    def runner(arguments, **_kwargs):
        return subprocess.CompletedProcess(arguments, 0, stdout=next(outputs), stderr="")

    with pytest.raises(upgrade.UpgradeCheckError, match="値を返さなかった"):
        upgrade.resolve_old_commit(tmp_path, "current-oid", runner=runner)


@pytest.mark.parametrize(
    ("platform_name", "expected"),
    (("linux", "update-dotfiles"), ("windows", "update-dotfiles.cmd")),
)
def test_platform_entrypoint_selects_real_launcher(tmp_path: pathlib.Path, platform_name: str, expected: str) -> None:
    """各OSで公開される実ランチャーを旧checkoutから選択する。"""
    assert pathlib.Path(upgrade.platform_entrypoint(tmp_path, platform_name)[-1]).name == expected


def test_run_propagates_child_failure(tmp_path: pathlib.Path) -> None:
    """子プロセスの失敗を成功として継続しない。"""

    def runner(arguments, **_kwargs):
        return subprocess.CompletedProcess(arguments, 23, stdout="child-out\n", stderr="child-error\n")

    with pytest.raises(upgrade.UpgradeCheckError, match="終了コード23"):
        upgrade._run(  # pylint: disable=protected-access  # noqa: SLF001
            ("failing-command",), cwd=tmp_path, runner=runner
        )


def test_verify_updated_oid_rejects_mismatch() -> None:
    """更新後OIDが検証開始時の現行OIDと異なる場合は失敗する。"""
    with pytest.raises(upgrade.UpgradeCheckError, match="expected=current actual=other"):
        upgrade.verify_updated_oid("other", "current")


def test_create_local_remote_can_advance_checkout(tmp_path: pathlib.Path) -> None:
    """bare remoteが旧・現行commitのobjectを持ち、旧checkoutを更新できる。"""

    def git(*arguments: str | pathlib.Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *map(str, arguments)], check=True, capture_output=True, text=True, encoding="utf-8")

    source_repo = tmp_path / "source"
    bare_repo = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    git("init", source_repo)
    git("-C", source_repo, "config", "user.name", "upgrade-check-test")
    git("-C", source_repo, "config", "user.email", "upgrade-check-test@example.invalid")
    tracked = source_repo / "tracked.txt"
    tracked.write_text("old\n", encoding="utf-8")
    git("-C", source_repo, "add", "tracked.txt")
    git("-C", source_repo, "commit", "-m", "old")
    old_oid = git("-C", source_repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("current\n", encoding="utf-8")
    git("-C", source_repo, "commit", "-am", "current")
    current_oid = git("-C", source_repo, "rev-parse", "HEAD").stdout.strip()

    upgrade.create_local_remote(source_repo, bare_repo, old_oid)
    git("clone", "--branch", "upgrade-check", bare_repo, checkout)
    assert git("-C", checkout, "rev-parse", "HEAD").stdout.strip() == old_oid

    git("--git-dir", bare_repo, "update-ref", "refs/heads/upgrade-check", current_oid)
    git("-C", checkout, "pull", "--ff-only")
    assert git("-C", checkout, "rev-parse", "HEAD").stdout.strip() == current_oid
