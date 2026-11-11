# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""post_applyの基本動作の試験。"""

# pylint: disable=protected-access

import dataclasses
import logging
import logging.handlers
import os
import subprocess
import typing
from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal import common, post_apply_outcome
from pytools._internal.post_apply_test_helpers import _isolate_sync_report as _isolate_sync_report
from pytools._internal.post_apply_test_helpers import _isolate_update_log as _isolate_update_log
from pytools._internal.post_apply_test_helpers import _make_step


@pytest.mark.parametrize(("argv", "exit_code"), [(["--help"], 0), (["--unknown"], 2)])
def test_main_rejects_nondefault_arguments_before_side_effects(
    argv: list[str],
    exit_code: int,
    capsys: pytest.CaptureFixture[str],
    sync_report_path: Path,
) -> None:
    """確認引数と未知引数は更新処理や永続記録へ到達しない。"""
    called = False

    def runner() -> tuple[list[post_apply._StepResult], list[str]]:  # noqa: SLF001
        nonlocal called
        called = True
        return [], []

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(argv, runner=runner)

    assert exc_info.value.code == exit_code
    assert not called
    assert not post_apply._UPDATE_LOG_PATH.exists()  # noqa: SLF001
    assert not sync_report_path.exists()
    captured = capsys.readouterr()
    if exit_code == 0:
        assert "usage:" in captured.out
        assert captured.err == ""
    else:
        assert "usage:" in captured.err
        assert "unrecognized arguments" in captured.err


def test_main_help_without_runner_skips_default_steps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sync_report_path: Path
) -> None:
    """公開CLIと同じ引数形で、工程を指定しない場合の`DEFAULT_STEPS`を起動しない。"""

    def unexpected_run() -> tuple[list[post_apply._StepResult], list[str]]:  # noqa: SLF001
        pytest.fail("--helpで_DEFAULT_STEPSの実行へ到達した")

    monkeypatch.setattr(post_apply, "run", unexpected_run)
    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(["--help"])

    assert exc_info.value.code == 0
    assert "usage:" in capsys.readouterr().out
    assert not post_apply._UPDATE_LOG_PATH.exists()  # noqa: SLF001
    assert not sync_report_path.exists()


def test_linked_worktree_is_rejected_before_any_step_or_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sync_report_path: Path,
) -> None:
    """複製作業ツリーで工程を指定せずに実行しても、全段と永続記録へ到達しない。"""
    root = tmp_path / "linked"
    canonical_root = tmp_path / "main"
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)

    def fake_git(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd == ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"]
        assert not kwargs
        return subprocess.CompletedProcess(cmd, 0, f"{canonical_root / '.git'}\n", "")

    def unexpected_run() -> tuple[list[post_apply._StepResult], list[str]]:  # noqa: SLF001
        pytest.fail("linked worktreeで_DEFAULT_STEPSの実行へ到達した")

    monkeypatch.setattr(common, "run_subprocess", fake_git)
    monkeypatch.setattr(post_apply, "run", unexpected_run)

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main([])

    assert exc_info.value.code == 2
    assert not post_apply._UPDATE_LOG_PATH.exists()  # noqa: SLF001
    assert not sync_report_path.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(root) in captured.err
    assert str(canonical_root) in captured.err


def test_prune_only_rejects_linked_worktree_before_pruning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """prune-onlyも通常更新と同じ正規root保護を先に通す。"""
    root = tmp_path / "linked"
    canonical_root = tmp_path / "main"
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(
        common,
        "run_subprocess",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 0, f"{canonical_root / '.git'}\n", ""),
    )
    monkeypatch.setattr(post_apply.prune_installation, "run", lambda: pytest.fail("pruneへ到達した"))

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(["--prune-only"])

    assert exc_info.value.code == 2


@pytest.mark.parametrize("allow_non_canonical", [False, True])
def test_canonical_root_or_explicit_override_runs_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    allow_non_canonical: bool,
) -> None:
    """正規ルートと明示解除では既存の全段実行経路へ進む。"""
    canonical_root = tmp_path / "main"
    root = tmp_path / "linked" if allow_non_canonical else canonical_root
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)

    def fake_git(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd == ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"]
        assert not kwargs
        return subprocess.CompletedProcess(cmd, 0, f"{canonical_root / '.git'}\n", "")

    calls: list[str] = []
    monkeypatch.setattr(common, "run_subprocess", fake_git)
    monkeypatch.setattr(
        post_apply,
        "DEFAULT_STEPS",
        [("first", _make_step("first", calls)), ("second", _make_step("second", calls))],
    )
    argv = ["--allow-non-canonical-root"] if allow_non_canonical else []

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(argv)

    assert exc_info.value.code == 0
    assert sorted(calls) == ["first", "second"]


class TestSubstitutedHome:
    """HOMEを差し替えた実行で、HOMEの外にある実機の共有資源を操作するステップを実行しない契約。

    `systemctl --user`や`/dev/shm`はHOMEで解決されないため、手動観測やテストでHOMEだけを差し替えても
    実機の`atk-serve.service`の再起動や共有メモリー上のファイルの削除が起こる。
    """

    _HOST_RESOURCE_STEPS = (
        "Codex 診断ログの通常ストレージ復元 (Linux)",
        "atk serve 自動起動セットアップ (Linux)",
        "一時ファイルの経過削除タイマー セットアップ (Linux)",
    )

    def _default_steps_with_recorders(self, calls: list[str]) -> list[post_apply._StepSpec]:  # noqa: SLF001
        """`DEFAULT_STEPS`にある3ステップの宣言を保ったまま、`run`だけを呼び出しの記録へ差し替える。"""
        steps = [step for step in post_apply.DEFAULT_STEPS if step.name in self._HOST_RESOURCE_STEPS]  # noqa: SLF001
        assert [step.name for step in steps] == list(self._HOST_RESOURCE_STEPS)
        return [dataclasses.replace(step, after=(), run=_make_step(step.name, calls)) for step in steps]

    def test_substituted_home_skips_host_resource_steps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        systemctl_calls: list[object] = []
        monkeypatch.setattr(subprocess, "run", lambda *args, **_kwargs: systemctl_calls.append(args))
        calls: list[str] = []

        with caplog.at_level(logging.INFO):
            results, _ = post_apply.run(self._default_steps_with_recorders(calls))

        assert not calls
        assert not systemctl_calls
        assert [(result.ok, result.changed) for result in results] == [(True, False)] * 3
        for index, name in enumerate(self._HOST_RESOURCE_STEPS, start=1):
            record = next(r for r in caplog.records if r.getMessage().startswith(f"[{index}/3] {name}: "))
            assert "HOMEが実行ユーザーのホームと異なるため" in record.getMessage()
            # 手動観測の実行者が画面で確認できるよう、画面から外す印を付けない。
            assert not post_apply.log_format.is_log_only(record)

    def test_actual_home_runs_host_resource_steps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import pwd  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error  # POSIX専用でありWindowsには存在しない。

        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        monkeypatch.setenv("HOME", pwd.getpwuid(os.getuid()).pw_dir)
        calls: list[str] = []

        post_apply.run(self._default_steps_with_recorders(calls))

        assert sorted(calls) == sorted(self._HOST_RESOURCE_STEPS)


class TestPytoolsInstallNotices:
    """テンプレートから渡されたpytools再導入状態の最終案内。"""

    def test_unset_state_does_not_print_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """状態が未設定なら案内を表示しない。"""
        monkeypatch.delenv("DOTFILES_PYTOOLS_INSTALL_STATE", raising=False)
        monkeypatch.delenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: ([], []))

        assert exc_info.value.code == 0
        assert "pytoolsの再インストール" not in capsys.readouterr().err

    def test_empty_state_does_not_print_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """状態が空文字列なら案内を表示しない。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", "")
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", "ignored")

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: ([], []))

        assert exc_info.value.code == 0
        assert "pytoolsの再インストール" not in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("deferred", "pytoolsの再インストールを延期しました。"),
            ("failed", "pytoolsの再インストールに失敗しました。"),
        ],
    )
    def test_known_state_prints_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        state: str,
        expected: str,
    ) -> None:
        """延期と失敗を最終案内へ表示する。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", state)
        monkeypatch.delenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: ([], []))

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        (tmp_path / "notice.stdout.txt").write_text(captured.out, encoding="utf-8")
        (tmp_path / "notice.stderr.txt").write_text(captured.err, encoding="utf-8")
        assert expected in captured.err
        assert "update-dotfiles" in captured.err.splitlines()

    def test_notice_includes_detail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """補足がある場合は状態案内の本文へ含める。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", "failed")
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", "uv tool install error")

        with pytest.raises(SystemExit):
            post_apply.main(runner=lambda: ([], []))

        assert "詳細: uv tool install error" in capsys.readouterr().err

    def test_unknown_state_raises_value_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """送信契約にない状態を黙って無視しない。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", "unknown")

        with pytest.raises(ValueError, match="未知のpytools再導入状態"):
            post_apply.main(runner=lambda: ([], []))


def test_step_returning_non_outcome_is_counted_as_failure() -> None:
    """契約に反する戻り値の工程は失敗と数え、後続の工程と集計を止めない。"""

    def legacy_step() -> bool:
        return False

    # 型注釈を持たない呼び出し元を模すため、型検査上は契約どおりの工程として渡す。
    legacy = typing.cast("typing.Callable[[], post_apply_outcome.PostApplyOutcome]", legacy_step)
    results, _ = post_apply.run([("legacy", legacy), ("next", post_apply_outcome.PostApplyOutcome)])

    assert [(result.name, result.ok) for result in results] == [("legacy", False), ("next", True)]
    assert results[0].reason is not None
    assert "PostApplyOutcome" in results[0].reason
