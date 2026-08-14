"""pytools.post_apply のテスト。

各ステップが順に呼ばれること、途中ステップが例外を送出しても他が継続すること、
失敗時の exit code を検証する。
"""

from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal import post_apply_outcome

# 配布先cleanup契約の定数を直接検証する。
# pylint: disable=protected-access


def test_removed_session_review_skill_paths_cover_claude_and_codex() -> None:
    """旧個人スキルをClaude CodeとCodexの両配布先からcleanupする。"""
    relative = Path("skills/session-review-dotfiles")
    assert relative in post_apply._REMOVED_PATHS[Path.home() / ".claude"]  # noqa: SLF001
    assert relative in post_apply._REMOVED_PATHS[Path.home() / ".codex"]  # noqa: SLF001


def test_session_review_reference_is_not_cleanup_target() -> None:
    """新しいreferenceを旧資産cleanupへ含めない。"""
    for paths in post_apply._REMOVED_PATHS.values():  # noqa: SLF001
        assert Path("references/session-review-dotfiles.md") not in paths


def test_removed_ipython_profile_is_limited_to_profile_default() -> None:
    """旧IPythonプロファイルのcleanup対象に利用中のprofile_ipyを含めない。"""
    paths = post_apply._REMOVED_PATHS[Path.home() / ".ipython"]  # noqa: SLF001
    assert Path("profile_default/startup/README") in paths
    assert not any(path.is_relative_to("profile_ipy") for path in paths)


def _make_step(name: str, calls: list[str], changed: bool = False):
    """呼び出し記録を残すステップ関数を返すヘルパー。"""

    def fn() -> bool:
        calls.append(name)
        return changed

    return fn


def _make_broken_step(name: str, calls: list[str]):
    """例外を送出するステップ関数を返すヘルパー。"""

    def fn() -> bool:
        calls.append(name)
        raise RuntimeError("boom")

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
            ("codex MCP サーバーの登録", _make_step("codex", calls)),
            ("libarchive (Windows)", _make_step("libarchive", calls)),
            ("claude-plans-viewer 再起動 (Linux)", _make_step("plans-viewer-restart-linux", calls)),
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
            "codex",
            "libarchive",
            "plans-viewer-restart-linux",
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
            ("codex MCP サーバーの登録", _make_step("codex", calls)),
            ("libarchive (Windows)", _make_step("libarchive", calls)),
            ("claude-plans-viewer 再起動 (Linux)", _make_step("plans-viewer-restart-linux", calls)),
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
            "codex",
            "libarchive",
            "plans-viewer-restart-linux",
        ]
        ok_flags = [r.ok for r in results]
        assert ok_flags == [True, True, False, True, True, True, True, True, True, True]

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


class TestDefaultSteps:
    """`_DEFAULT_STEPS`に想定ステップが登録されていることを検証する。"""

    def test_statusline_binary_step_registered(self):
        """claude-statuslineバイナリ取得ステップが登録され、libarchiveステップの後に続く。"""
        names = [name for name, _ in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        assert "claude-statusline バイナリの取得" in names
        assert names.index("claude-statusline バイナリの取得") == names.index("libarchive (Windows)") + 1

    def test_codex_plugin_step_order(self):
        """Codex pluginはリンクとClaude pluginの後、Codex MCPの前に導入する。"""
        names = [name for name, _ in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        assert names.index("Codex リンクの同期") < names.index("Codex plugin のインストール")
        assert names.index("Claude Code plugin のインストール") < names.index("Codex plugin のインストール")
        assert names.index("Codex plugin のインストール") < names.index("codex MCP サーバーの登録")
        assert names.index("codex MCP サーバーの登録") < names.index("Claude 設定")

    def test_codex_logs_step_registered_after_links(self):
        """Codex診断ログの通常ストレージ復元をリンク同期の直後に実行する。"""
        names = [name for name, _ in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        assert names.index("Codex 診断ログの通常ストレージ復元 (Linux)") == names.index("Codex リンクの同期") + 1

    def test_cli_setup_precedes_dependent_steps(self):
        """CLI本体をplugin、リンク、MCPより前に準備する。"""
        names = [name for name, _ in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
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
            "codex MCP サーバーの登録",
            "Claude 設定",
        ]
        indexes = [names.index(name) for name in ordered]
        assert indexes == sorted(indexes)

    def test_atk_serve_follows_plans_viewer_before_windows_steps(self) -> None:
        """atk serveセットアップをplans viewer直後かつWindows固有処理前に1回登録する。"""
        names = [name for name, _ in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        serve_name = "atk serve 自動起動セットアップ (Linux)"
        plans_name = "claude-plans-viewer 自動起動セットアップ (Linux)"
        assert names.count(serve_name) == 1
        assert names.index(serve_name) == names.index(plans_name) + 1
        assert names.index(serve_name) < names.index("Windowsレジストリ設定")


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
