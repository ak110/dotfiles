"""pytools.post_apply のテスト。

各ステップが順に呼ばれること、途中ステップが例外を送出しても他が継続すること、
失敗時の exit code を検証する。
"""

# `_StepResult` など、パッケージ内部クラス (先頭 _) を
# テストするため protected-access を全体で許可する。
# pylint: disable=protected-access

import pytest

from pytools import _install_claude_plugins, post_apply


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


class TestRun:
    """post_apply.run() の振る舞い。"""

    def test_all_steps_succeed(self):
        """全ステップ成功時、ok=True のリストが返る。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], bool]]] = [
            ("Claude 設定", _make_step("claude", calls, changed=True)),
            ("VSCode 設定", _make_step("vscode", calls)),
            ("SSH config", _make_step("ssh", calls)),
            ("旧配布物の削除", _make_step("cleanup", calls)),
            ("npm/pnpm サプライチェーン対策", _make_step("npmrc", calls, changed=True)),
            ("mise セットアップ", _make_step("mise", calls)),
            ("Claude Code plugin のインストール", _make_step("plugins", calls)),
        ]

        results = post_apply.run(steps=steps)

        assert calls == ["claude", "vscode", "ssh", "cleanup", "npmrc", "mise", "plugins"]
        assert all(r.ok for r in results)
        assert [r.changed for r in results] == [True, False, False, False, True, False, False]

    def test_failing_step_does_not_stop_others(self):
        """途中ステップが例外を送出しても後続は実行される。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], bool]]] = [
            ("Claude 設定", _make_step("claude", calls)),
            ("VSCode 設定", _make_step("vscode", calls)),
            ("SSH config", _make_broken_step("broken", calls)),
            ("旧配布物の削除", _make_step("cleanup", calls)),
            ("npm/pnpm サプライチェーン対策", _make_step("npmrc", calls)),
            ("mise セットアップ", _make_step("mise", calls)),
            ("Claude Code plugin のインストール", _make_step("plugins", calls)),
        ]

        results = post_apply.run(steps=steps)

        assert calls == ["claude", "vscode", "broken", "cleanup", "npmrc", "mise", "plugins"]
        ok_flags = [r.ok for r in results]
        assert ok_flags == [True, True, False, True, True, True, True]

    def test_main_exits_1_on_failure(self):
        """失敗があれば _main() は SystemExit(1) で終了する。"""
        fake_results = [
            post_apply._StepResult(name="ok", ok=True, changed=False),
            post_apply._StepResult(name="broken", ok=False, changed=False),
        ]
        with pytest.raises(SystemExit) as exc_info:
            post_apply._main(runner=lambda: fake_results)
        assert exc_info.value.code == 1

    def test_main_exits_0_on_success(self):
        """全て成功なら _main() は正常終了 (SystemExit を送出しない)。"""
        fake_results = [
            post_apply._StepResult(name="ok", ok=True, changed=False),
        ]
        # SystemExit が出ないことを確認
        post_apply._main(runner=lambda: fake_results)  # pylint: disable=protected-access


class TestPluginRecommendations:
    """`_install_claude_plugins.consume_recommendations()` による推奨コマンド案内出力。"""

    def test_prints_recommendations_when_present(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
        """推奨コマンドがある場合は見出しと各コマンド行を出力する。"""
        monkeypatch.setattr(
            _install_claude_plugins,
            "_LAST_RECOMMENDATIONS",
            ["claude plugin install a --scope user", "claude plugin disable b --scope user"],
        )
        fake_results = [post_apply._StepResult(name="ok", ok=True, changed=False)]
        with caplog.at_level("INFO", logger=post_apply.logger.name):
            post_apply._main(runner=lambda: fake_results)
        messages = [record.getMessage() for record in caplog.records]
        assert any("推奨プラグイン設定" in m for m in messages)
        assert any("claude plugin install a --scope user" in m for m in messages)
        assert any("claude plugin disable b --scope user" in m for m in messages)
        # consume 後は空になっている (ワンショット契約)
        assert not _install_claude_plugins.consume_recommendations()

    def test_no_output_when_no_recommendations(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
        """推奨コマンドが空なら案内を出力しない。"""
        monkeypatch.setattr(_install_claude_plugins, "_LAST_RECOMMENDATIONS", [])
        fake_results = [post_apply._StepResult(name="ok", ok=True, changed=False)]
        with caplog.at_level("INFO", logger=post_apply.logger.name):
            post_apply._main(runner=lambda: fake_results)
        messages = [record.getMessage() for record in caplog.records]
        assert not any("推奨プラグイン設定" in m for m in messages)
