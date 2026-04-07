"""pytools.post_apply のテスト。

各ステップが順に呼ばれること、途中ステップが例外を投げても他が継続すること、
失敗時の exit code を検証する。
"""

# `_update_claude_settings` など、パッケージ内部モジュール (先頭 _) を
# monkeypatch するため protected-access を全体で許可する。
# pylint: disable=protected-access

import pytest

from pytools import post_apply


class TestRun:
    """post_apply.run() の振る舞い。"""

    def test_all_steps_succeed(self, monkeypatch: pytest.MonkeyPatch):
        """全ステップ成功時、ok=True のリストが返る。"""
        calls: list[str] = []

        def make(name: str, changed: bool = False):
            def fn() -> bool:
                calls.append(name)
                return changed

            return fn

        monkeypatch.setattr(post_apply._update_claude_settings, "run", make("claude", True))
        monkeypatch.setattr(post_apply.update_ssh_config, "run", make("ssh"))
        monkeypatch.setattr(post_apply, "_cleanup_removed_paths", make("cleanup"))
        monkeypatch.setattr(post_apply._update_npmrc, "run", make("npmrc", True))
        monkeypatch.setattr(post_apply._setup_mise, "run", make("mise"))
        monkeypatch.setattr(post_apply._install_claude_plugins, "run", make("plugins"))

        results = post_apply.run()

        assert calls == ["claude", "ssh", "cleanup", "npmrc", "mise", "plugins"]
        assert all(r.ok for r in results)
        assert [r.changed for r in results] == [True, False, False, True, False, False]

    def test_failing_step_does_not_stop_others(self, monkeypatch: pytest.MonkeyPatch):
        """途中ステップが例外を投げても後続は走る。"""
        calls: list[str] = []

        def make(name: str):
            def fn() -> bool:
                calls.append(name)
                return False

            return fn

        def broken() -> bool:
            calls.append("broken")
            raise RuntimeError("boom")

        monkeypatch.setattr(post_apply._update_claude_settings, "run", make("claude"))
        monkeypatch.setattr(post_apply.update_ssh_config, "run", broken)
        monkeypatch.setattr(post_apply, "_cleanup_removed_paths", make("cleanup"))
        monkeypatch.setattr(post_apply._update_npmrc, "run", make("npmrc"))
        monkeypatch.setattr(post_apply._setup_mise, "run", make("mise"))
        monkeypatch.setattr(post_apply._install_claude_plugins, "run", make("plugins"))

        results = post_apply.run()

        assert calls == ["claude", "broken", "cleanup", "npmrc", "mise", "plugins"]
        # ssh のみ失敗
        ok_flags = [r.ok for r in results]
        assert ok_flags == [True, False, True, True, True, True]

    def test_main_exits_1_on_failure(self, monkeypatch: pytest.MonkeyPatch):
        """失敗があれば _main() は SystemExit(1) で終了する。"""

        def ok() -> bool:
            return False

        def broken() -> bool:
            raise RuntimeError("boom")

        monkeypatch.setattr(post_apply._update_claude_settings, "run", ok)
        monkeypatch.setattr(post_apply.update_ssh_config, "run", broken)
        monkeypatch.setattr(post_apply, "_cleanup_removed_paths", ok)
        monkeypatch.setattr(post_apply._update_npmrc, "run", ok)
        monkeypatch.setattr(post_apply._setup_mise, "run", ok)
        monkeypatch.setattr(post_apply._install_claude_plugins, "run", ok)

        with pytest.raises(SystemExit) as exc_info:
            post_apply._main()
        assert exc_info.value.code == 1

    def test_main_exits_0_on_success(self, monkeypatch: pytest.MonkeyPatch):
        """全て成功なら _main() は正常終了 (SystemExit を投げない)。"""

        def ok() -> bool:
            return False

        monkeypatch.setattr(post_apply._update_claude_settings, "run", ok)
        monkeypatch.setattr(post_apply.update_ssh_config, "run", ok)
        monkeypatch.setattr(post_apply, "_cleanup_removed_paths", ok)
        monkeypatch.setattr(post_apply._update_npmrc, "run", ok)
        monkeypatch.setattr(post_apply._setup_mise, "run", ok)
        monkeypatch.setattr(post_apply._install_claude_plugins, "run", ok)

        # SystemExit が出ないことを確認
        post_apply._main()
