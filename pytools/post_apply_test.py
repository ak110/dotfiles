"""pytools.post_apply のテスト。

各ステップが順に呼ばれること、途中ステップが例外を送出しても他が継続すること、
失敗時の exit code を検証する。
"""

import io
import logging
import re
import threading
import time
from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal import post_apply_outcome

# 配布先cleanup契約の定数を直接検証する。
# pylint: disable=protected-access


def test_configure_logging_preserves_cp932_record_with_unencodable_character(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CP932で表現できない文字を代替表現へ変換し、ログレコードを保持する。"""
    stdout_buffer = io.BytesIO()
    stderr_buffer = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_buffer, encoding="cp932")
    stderr = io.TextIOWrapper(stderr_buffer, encoding="cp932")
    monkeypatch.setattr(post_apply.sys, "stdout", stdout)
    monkeypatch.setattr(post_apply.sys, "stderr", stderr)
    root_logger = logging.getLogger()
    previous_handlers, previous_level = post_apply._configure_logging()  # noqa: SLF001
    try:
        logging.getLogger("cp932-test").info("符号化不能文字: ✓")
        stdout.flush()
        stderr.flush()
        output = stdout_buffer.getvalue().decode("cp932") + stderr_buffer.getvalue().decode("cp932")
    finally:
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)

    assert stdout.encoding == "cp932"
    assert "符号化不能文字" in output
    assert r"\u2713" in output
    assert "--- Logging error ---" not in output


def test_removed_session_review_skill_paths_cover_claude_and_codex() -> None:
    """旧個人スキルをClaude CodeとCodexの両配布先からcleanupする。"""
    relative = Path("skills/session-review-dotfiles")
    assert relative in post_apply._REMOVED_PATHS[Path.home() / ".claude"]  # noqa: SLF001
    assert relative in post_apply._REMOVED_PATHS[Path.home() / ".codex"]  # noqa: SLF001


def test_session_review_reference_is_not_cleanup_target() -> None:
    """新しい参照文書を旧資産の後始末対象へ含めない。"""
    for paths in post_apply._REMOVED_PATHS.values():  # noqa: SLF001
        assert Path("references/session-review-dotfiles.md") not in paths


def test_removes_legacy_plans_viewer_config_and_shim() -> None:
    """旧計画ビューアーの設定・CLI・Windowsスタートアップ用shimを配布先から除去する。"""
    assert Path("pytools/claude-plans-viewer.toml") in post_apply._REMOVED_PATHS[Path.home() / ".config"]  # noqa: SLF001
    local_bin = post_apply._REMOVED_PATHS[Path.home() / ".local" / "bin"]  # noqa: SLF001
    assert Path("claude-plans-viewer") in local_bin
    assert Path("claude-plans-viewer.exe") in local_bin
    startup = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    assert Path("claude-plans-viewer.cmd") in post_apply._REMOVED_PATHS_IF_CONTENT[startup]  # noqa: SLF001
    # 旧systemd unitは停止と無効化を経てから削除するため、この一括削除の対象へ含めない。
    for paths in post_apply._REMOVED_PATHS.values():  # noqa: SLF001
        assert not [path for path in paths if "claude-plans-viewer.service" in path.name]


def test_removed_ipython_profile_is_limited_to_profile_default() -> None:
    """旧IPythonプロファイルのcleanup対象に利用中のprofile_ipyを含めない。"""
    paths = post_apply._REMOVED_PATHS[Path.home() / ".ipython"]  # noqa: SLF001
    assert Path("profile_default/startup/README") in paths
    assert not any(path.is_relative_to("profile_ipy") for path in paths)


def _redirect_removed_paths_to(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """旧配布物削除先を一時ホームへ限定し、IPython配布ファイルだけを登録する。"""
    home_dir = tmp_path / "home"
    ipython_dir = home_dir / ".ipython"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setattr(
        post_apply,
        "_REMOVED_PATHS",
        {ipython_dir: [Path("profile_default/startup/README")]},
    )
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS_IF_CONTENT", {})
    return ipython_dir


def test_removed_ipython_profile_cleanup_removes_empty_parents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """配布済みREADMEの削除後、空になった親ディレクトリだけを深い順で除去する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths()  # noqa: SLF001

    assert changed is True
    assert not (ipython_dir / "profile_default").exists()


def test_removed_ipython_profile_cleanup_preserves_user_file_in_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """startupに利用者ファイルが残る場合はprofile_defaultまで保持する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")
    user_script = default_readme.parent / "00-user.py"
    user_script.write_text("print('user')\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths()  # noqa: SLF001

    assert changed is True
    assert not default_readme.exists()
    assert user_script.read_text(encoding="utf-8") == "print('user')\n"
    assert (ipython_dir / "profile_default/startup").is_dir()
    assert (ipython_dir / "profile_default").is_dir()


def test_removed_ipython_profile_cleanup_preserves_user_file_in_profile_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """profile_default直下に利用者ファイルが残る場合はルートだけを保持する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")
    user_config = ipython_dir / "profile_default/ipython_config.py"
    user_config.write_text("c = get_config()\n", encoding="utf-8")
    active_config = ipython_dir / "profile_ipy/ipython_config.py"
    active_config.parent.mkdir(parents=True)
    active_config.write_text("active\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths()  # noqa: SLF001

    assert changed is True
    assert not default_readme.exists()
    assert not (ipython_dir / "profile_default/startup").exists()
    assert user_config.read_text(encoding="utf-8") == "c = get_config()\n"
    assert (ipython_dir / "profile_default").is_dir()
    assert active_config.read_text(encoding="utf-8") == "active\n"


def test_removed_ipython_profile_cleanup_skips_missing_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """配布済みファイルも親ディレクトリも存在しない場合は変更なしとする。"""
    _redirect_removed_paths_to(monkeypatch, tmp_path)

    changed = post_apply._cleanup_removed_paths()  # noqa: SLF001

    assert changed is False


def test_removed_ipython_profile_cleanup_preserves_symlink_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """profile_defaultが外部リンクの場合はリンク先の空ディレクトリを削除しない。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    outside_profile = tmp_path / "outside-profile"
    outside_startup = outside_profile / "startup"
    outside_startup.mkdir(parents=True)
    ipython_dir.mkdir(parents=True)
    profile_link = ipython_dir / "profile_default"
    profile_link.symlink_to(outside_profile, target_is_directory=True)

    changed = post_apply._cleanup_removed_paths()  # noqa: SLF001

    assert changed is False
    assert profile_link.is_symlink()
    assert outside_startup.is_dir()


def _make_step(name: str, calls: list[str], changed: bool = False):
    """呼び出し記録を残すステップ関数を返すヘルパー。"""

    def fn() -> bool:
        calls.append(name)
        return changed

    return fn


def _make_broken_step(name: str, calls: list[str], message: str = "boom"):
    """例外を送出するステップ関数を返すヘルパー。"""

    def fn() -> bool:
        calls.append(name)
        raise RuntimeError(message)

    return fn


def _make_plugin_step(recommendations: list[str]):
    """推奨コマンドリストを返すステップ関数を返すヘルパー。"""

    def fn() -> tuple[bool, list[str]]:
        return True, recommendations

    return fn


def _make_outcome_step(*, changed: bool, notices: tuple[post_apply_outcome.PostApplyNotice, ...]):
    """構造化したpost-apply結果を返すステップ関数を返す。"""

    def fn() -> post_apply_outcome.PostApplyOutcome:
        return post_apply_outcome.PostApplyOutcome(changed=changed, notices=notices)

    return fn


class TestRun:
    """post_apply.run() の振る舞い。"""

    def test_all_steps_succeed(self):
        """全ステップ成功時、ok=True のリストが返る。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Claude 設定", _make_step("claude", calls, changed=True)),
            ("VSCode 設定", _make_step("vscode", calls)),
            ("SSH config", _make_step("ssh", calls)),
            ("旧配布物の削除", _make_step("cleanup", calls)),
            ("npm/pnpm サプライチェーン対策", _make_step("npmrc", calls, changed=True)),
            ("mise セットアップ", _make_step("mise", calls)),
            ("Claude Code plugin のインストール", _make_step("plugins", calls)),
            ("旧Codex User scope MCP登録の移行", _make_step("codex-migration", calls)),
            ("libarchive (Windows)", _make_step("libarchive", calls)),
            ("atk serve 再起動 (Linux)", _make_step("atk-serve-restart-linux", calls)),
        ]

        results, recommendations = post_apply.run(steps=steps)

        assert calls == [
            "claude",
            "vscode",
            "ssh",
            "cleanup",
            "npmrc",
            "mise",
            "plugins",
            "codex-migration",
            "libarchive",
            "atk-serve-restart-linux",
        ]
        assert all(r.ok for r in results)
        assert [r.changed for r in results] == [
            True,
            False,
            False,
            False,
            True,
            False,
            False,
            False,
            False,
            False,
        ]
        assert not recommendations

    def test_failing_step_does_not_stop_others(self):
        """途中ステップが例外を送出しても後続は実行される。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Claude 設定", _make_step("claude", calls)),
            ("VSCode 設定", _make_step("vscode", calls)),
            ("SSH config", _make_broken_step("broken", calls)),
            ("旧配布物の削除", _make_step("cleanup", calls)),
            ("npm/pnpm サプライチェーン対策", _make_step("npmrc", calls)),
            ("mise セットアップ", _make_step("mise", calls)),
            ("Claude Code plugin のインストール", _make_step("plugins", calls)),
            ("旧Codex User scope MCP登録の移行", _make_step("codex-migration", calls)),
            ("libarchive (Windows)", _make_step("libarchive", calls)),
            ("atk serve 再起動 (Linux)", _make_step("atk-serve-restart-linux", calls)),
        ]

        results, _ = post_apply.run(steps=steps)

        assert calls == [
            "claude",
            "vscode",
            "broken",
            "cleanup",
            "npmrc",
            "mise",
            "plugins",
            "codex-migration",
            "libarchive",
            "atk-serve-restart-linux",
        ]
        ok_flags = [r.ok for r in results]
        assert ok_flags == [True, True, False, True, True, True, True, True, True, True]

    def test_foreground_completion_reports_step_duration(self, caplog: pytest.LogCaptureFixture) -> None:
        """前景ステップの完了行へステップ本体の所要時間を表示する。"""

        def foreground() -> bool:
            time.sleep(0.3)
            return False

        caplog.set_level(logging.INFO)
        post_apply.run([post_apply._StepSpec("前景", foreground)])  # noqa: SLF001

        completion = next(record.getMessage() for record in caplog.records if record.getMessage().startswith("[1/1] 前景 ("))
        match = re.fullmatch(r"\[1/1] 前景 \(([0-9]+\.[0-9])秒\)", completion)
        assert match is not None
        assert float(match.group(1)) >= 0.2

    def test_background_step_overlaps_foreground_and_appends_result_after_it(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """背景ログを結合時まで保持し、前景結果の後へ投入順で追加する。"""
        started = threading.Event()
        released = threading.Event()

        def background() -> bool:
            started.set()
            time.sleep(0.3)
            released.set()
            logging.getLogger("background-test").info("背景ログ")
            return True

        def foreground() -> bool:
            assert started.wait(timeout=2)
            assert released.wait(timeout=2)
            logging.getLogger("foreground-test").info("前景ログ")
            return False

        caplog.set_level(logging.INFO)
        results, _ = post_apply.run(
            [
                post_apply._StepSpec("背景", background, background=True),  # noqa: SLF001
                post_apply._StepSpec("前景", foreground),  # noqa: SLF001
            ]
        )

        assert [result.name for result in results] == ["前景", "背景"]
        messages = [record.getMessage() for record in caplog.records]
        background_completion = next(message for message in messages if message.startswith("[1/2] 背景 ("))
        match = re.fullmatch(r"\[1/2] 背景 \(([0-9]+\.[0-9])秒\)", background_completion)
        assert match is not None
        assert float(match.group(1)) >= 0.2
        assert messages.index("前景ログ") < messages.index(background_completion) < messages.index("背景ログ")

    def test_background_step_failure_does_not_discard_other_results(self) -> None:
        """背景ステップの例外を当該結果へ局所化し、他の結果を保持する。"""
        calls: list[str] = []
        results, recommendations = post_apply.run(
            [
                post_apply._StepSpec("背景", _make_broken_step("background", calls), background=True),  # noqa: SLF001
                post_apply._StepSpec("前景", _make_step("foreground", calls, changed=True)),  # noqa: SLF001
            ]
        )

        assert sorted(calls) == ["background", "foreground"]
        assert [(result.name, result.ok, result.changed) for result in results] == [
            ("前景", True, True),
            ("背景", False, False),
        ]
        assert not recommendations

    def test_main_exits_1_on_failure(self):
        """失敗があれば main() は SystemExit(1) で終了する。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("ok", _make_step("ok", calls)),
            ("broken", _make_broken_step("broken", calls)),
        ]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        assert exc_info.value.code == 1

    def test_cli_install_failure_marks_step_failed_continues_and_exits_1(self):
        """CLI導入失敗を失敗結果へ変換し、後続実行後に終了コード1とする。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Codex CLI の導入と更新", _make_broken_step("codex", calls)),
            ("後続ステップ", _make_step("later", calls)),
        ]

        results, _ = post_apply.run(steps=steps)

        assert calls == ["codex", "later"]
        assert [(result.ok, result.changed) for result in results] == [(False, False), (True, False)]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: (results, []))
        assert exc_info.value.code == 1

    def test_statusline_development_failure_marks_step_failed_continues_and_exits_1(self):
        """statusline開発版の導入失敗を失敗結果へ変換し、後続実行後に終了コード1とする。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("claude-statusline バイナリの取得", _make_broken_step("statusline", calls)),
            ("後続ステップ", _make_step("later", calls)),
        ]

        results, _ = post_apply.run(steps=steps)

        assert calls == ["statusline", "later"]
        assert [(result.ok, result.changed) for result in results] == [(False, False), (True, False)]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: (results, []))
        assert exc_info.value.code == 1

    def test_main_exits_0_on_success(self):
        """全て成功なら main() は SystemExit(0) で正常終了する。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("ok", _make_step("ok", calls)),
        ]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        assert exc_info.value.code == 0

    def test_main_splits_info_and_errors_between_streams(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """正常な状態表示はstdout、失敗一覧はstderrへ出力する。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("ok", _make_step("ok", calls)),
            ("broken", _make_broken_step("broken", calls)),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 1
        assert calls == ["ok", "broken"]
        captured = capsys.readouterr()
        assert "完了: 更新 0 件 / スキップ 1 件 / 失敗 1 件" in captured.out
        assert "失敗したステップ" not in captured.out
        assert "失敗したステップ: broken" in captured.err
        assert "完了:" not in captured.err

    def test_structured_outcome_preserves_changed_and_notices(self) -> None:
        """構造化結果の変更有無と案内をステップ結果へ保持する。"""
        notice = post_apply_outcome.PostApplyNotice("Codex pluginを更新しました。")
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Codex plugin", _make_outcome_step(changed=True, notices=(notice,))),
        ]

        results, recommendations = post_apply.run(steps=steps)

        assert not recommendations
        assert results[0].changed is True
        assert results[0].notices == (notice,)

    def test_main_prints_deduplicated_notice_without_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        """commandを持たない重複案内をstderrへ1回表示する。"""
        notice = post_apply_outcome.PostApplyNotice("Codex pluginを更新しました。")
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("first", _make_outcome_step(changed=True, notices=(notice,))),
            ("second", _make_outcome_step(changed=True, notices=(notice,))),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "codex app-server daemon restart" not in captured.out
        assert captured.err.count("Codex pluginを更新しました。") == 1
        assert captured.err.splitlines()[-1].endswith("Codex pluginを更新しました。")

    def test_main_keeps_notice_on_later_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """案内の発生後に後続が失敗しても非0終了と案内を両立する。"""
        calls: list[str] = []
        notice = post_apply_outcome.PostApplyNotice("Codex pluginを更新しました。")
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Codex plugin", _make_outcome_step(changed=True, notices=(notice,))),
            ("broken", _make_broken_step("broken", calls)),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "失敗したステップ: broken" in captured.err
        assert captured.err.splitlines()[-1].endswith("Codex pluginを更新しました。")

    def test_main_keeps_notice_from_failed_codex_step(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Codex plugin処理が案内後に失敗しても案内と後続実行を保持する。"""
        calls: list[str] = []
        notice = post_apply_outcome.PostApplyNotice(
            "外部pluginを更新しました。",
            "codex app-server daemon restart",
        )
        assert notice.command is not None
        failure = f"local pluginの確定に失敗\n{notice.message}\n{notice.command}"
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Codex plugin", _make_broken_step("codex", calls, failure)),
            ("later", _make_step("later", calls)),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 1
        assert calls == ["codex", "later"]
        captured = capsys.readouterr()
        assert captured.err.count(notice.message) == 1
        assert captured.err.count(notice.command) == 1


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
        state: str,
        expected: str,
    ) -> None:
        """延期と失敗を最終案内へ表示する。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", state)
        monkeypatch.delenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: ([], []))

        assert exc_info.value.code == 0
        assert expected in capsys.readouterr().err

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


class TestDefaultSteps:
    """`_DEFAULT_STEPS`に想定ステップが登録されていることを検証する。"""

    def test_statusline_binary_step_registered(self):
        """claude-statuslineバイナリ取得ステップが登録され、libarchiveステップの後に続く。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        assert "claude-statusline バイナリの取得" in names
        assert names.index("claude-statusline バイナリの取得") == names.index("libarchive (Windows)") + 1

    def test_codex_plugin_step_order(self):
        """Codex pluginはリンクとClaude pluginの後、旧User scope移行の前に導入する。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        assert names.index("Codex リンクの同期") < names.index("Codex plugin のインストール")
        assert names.index("Claude Code plugin のインストール") < names.index("Codex plugin のインストール")
        assert names.index("Codex plugin のインストール") < names.index("旧Codex User scope MCP登録の移行")
        assert names.index("旧Codex User scope MCP登録の移行") < names.index("Claude 設定")

    def test_codex_logs_step_registered_after_links(self):
        """Codex診断ログの通常ストレージ復元をリンク同期の直後に実行する。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        assert names.index("Codex 診断ログの通常ストレージ復元 (Linux)") == names.index("Codex リンクの同期") + 1

    def test_cli_setup_precedes_dependent_steps(self):
        """CLI本体をplugin、リンク、旧User scope移行より前に準備する。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        remove_name = "Codex の Claude MCP 登録削除"
        codex_name = "Codex CLI の導入と更新"
        claude_name = "Claude Code CLI の導入と更新"
        assert names.count(remove_name) == 1
        assert names.index(remove_name) == names.index(codex_name) + 1
        assert names.index(remove_name) < names.index(claude_name)
        ordered = [
            "npm/pnpm サプライチェーン対策",
            "mise セットアップ",
            codex_name,
            remove_name,
            claude_name,
            "agent-toolkit ルールの同期",
            "Codex リンクの同期",
            "Claude Code plugin のインストール",
            "Codex plugin のインストール",
            "agents_serverのuv環境ウォームアップ",
            "旧Codex User scope MCP登録の移行",
            "Claude 設定",
        ]
        indexes = [names.index(name) for name in ordered]
        assert indexes == sorted(indexes)

    def test_warmup_hook_scripts_follows_codex_plugin_install(self) -> None:
        """hookスクリプトのuv環境ウォームアップをCodex plugin導入の直後に1回登録する。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        warmup_name = "hookスクリプトのuv環境ウォームアップ"
        assert names.count(warmup_name) == 1
        assert names.index(warmup_name) == names.index("agents_serverのuv環境ウォームアップ") + 1

    def test_agents_server_warmup_follows_codex_plugin_install(self) -> None:
        """agents_serverのuv環境ウォームアップをCodex plugin導入直後に登録する。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        warmup_name = "agents_serverのuv環境ウォームアップ"
        assert names.count(warmup_name) == 1
        assert names.index(warmup_name) == names.index("Codex plugin のインストール") + 1

    def test_atk_serve_follows_statusline_before_windows_steps(self) -> None:
        """atk serveセットアップをstatusline取得直後かつWindows固有処理前に1回登録する。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        serve_name = "atk serve 自動起動セットアップ (Linux)"
        assert names.count(serve_name) == 1
        assert names.index(serve_name) == names.index("claude-statusline バイナリの取得") + 1
        assert names.index(serve_name) < names.index("Windowsレジストリ設定")
        # 計画ファイル閲覧を統合したため、旧計画ビューアーの自動起動ステップは登録しない。
        assert not [name for name in names if "claude-plans-viewer" in name]

    def test_dotfiles_autoupdate_follows_atk_serve_before_windows_steps(self) -> None:
        """dotfiles自動更新timerをatk serve直後かつWindows固有処理前に1回登録する。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        timer_name = "dotfiles自動更新タイマー セットアップ (Linux)"
        serve_name = "atk serve 自動起動セットアップ (Linux)"
        assert names.count(timer_name) == 1
        assert names.index(timer_name) == names.index(serve_name) + 1
        assert names.index(timer_name) < names.index("Windowsレジストリ設定")

    def test_plan_migration_follows_autoupdate_before_windows_steps(self) -> None:
        """atk計画移行をLinux自動更新タイマーの直後かつWindows処理前に登録する。"""
        steps = post_apply._DEFAULT_STEPS  # noqa: SLF001
        names = [step.name for step in steps]
        migration = "atk計画の移行"
        assert names.count(migration) == 1
        assert names.index(migration) == names.index("dotfiles自動更新タイマー セットアップ (Linux)") + 1
        assert names.index(migration) < names.index("Windowsレジストリ設定")
        assert [step.name for step in steps if step.background] == [
            "agents_serverのuv環境ウォームアップ",
            "hookスクリプトのuv環境ウォームアップ",
        ]


class TestPluginRecommendations:
    """``install_claude_plugins.run()`` の推奨コマンド戻り値による案内出力。"""

    def test_prints_single_recommendation_without_continuation(
        self,
        capsys: pytest.CaptureFixture[str],
    ):
        """推奨コマンドが 1 件のみなら && も継続記号も付けず単一行で出力する。"""
        fake_recommendations = ["claude plugin install a --scope=user"]
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert any("推奨プラグイン設定" in line for line in stdout_lines)
        assert "claude plugin install a --scope=user" in stdout_lines
        # コマンド行は cmd.exe での貼り付け失敗を避けるため行頭インデントを付けない。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)
        assert not any("&&" in line for line in stdout_lines)

    def test_prints_multiple_recommendations_bash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """bash 系では && \\ で連結し、最終行のみ継続記号なしで出力する。"""
        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        fake_recommendations = [
            "claude plugin install a --scope=user",
            "claude plugin install b --scope=user",
            "claude plugin disable c --scope=user",
        ]
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert any("推奨プラグイン設定" in line for line in stdout_lines)
        assert "claude plugin install a --scope=user && \\" in stdout_lines
        assert "claude plugin install b --scope=user && \\" in stdout_lines
        assert "claude plugin disable c --scope=user" in stdout_lines
        # コマンド行は cmd.exe での貼り付け失敗を避けるため行頭インデントを付けない。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)

    def test_prints_multiple_recommendations_windows(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ):
        """Windows では && ^ で連結し、最終行のみ継続記号なしで出力する。"""
        monkeypatch.setattr(post_apply.sys, "platform", "win32")
        fake_recommendations = [
            "claude plugin install a --scope=user",
            "claude plugin disable b --scope=user",
        ]
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with caplog.at_level("INFO", logger=post_apply.logger.name), pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert "claude plugin install a --scope=user && ^" in stdout_lines
        assert "claude plugin disable b --scope=user" in stdout_lines
        # cmd.exe では `^` 継続後の行頭空白が解析エラーを起こすため、コマンド行は無インデントとする。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)

    def test_no_output_when_no_recommendations(
        self,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ):
        """推奨コマンドが空なら案内を出力しない。"""
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("ok", _make_step("ok", [], changed=False)),
        ]
        with caplog.at_level("INFO", logger=post_apply.logger.name), pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        messages = [record.getMessage() for record in caplog.records]
        assert not any("推奨プラグイン設定" in m for m in messages)
        stdout_lines = capsys.readouterr().out.splitlines()
        assert not any("claude plugin" in line for line in stdout_lines)
