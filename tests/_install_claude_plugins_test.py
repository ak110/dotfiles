"""pytools._install_claude_plugins のテスト。

subprocess.run / shutil.which をモックして、前提条件分岐・marketplace 登録・
plugin install の各パスを検証する。
"""

import json
import subprocess

import pytest

from pytools import _install_claude_plugins


class _FakeResult:
    """subprocess.CompletedProcess の軽量な代替。"""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(name="fake_which_present")
def _fake_which_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """claude と uv の両方が存在する状態に見せかける。"""
    monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")


class TestPrerequisites:
    """前提条件 (claude / uv の存在) のチェック。"""

    def test_missing_claude_skips(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: None if name == "claude" else "/usr/bin/uv")
        assert _install_claude_plugins.run() is False

    def test_missing_uv_skips(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: None if name == "uv" else "/usr/bin/claude")
        assert _install_claude_plugins.run() is False


@pytest.mark.usefixtures("fake_which_present")
class TestRunFlow:
    """メインフローのテスト (前提条件は満たしている状態)。"""

    def test_already_installed_skips(self, monkeypatch: pytest.MonkeyPatch):
        """対象 plugin が既に入っている場合は何もしない。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001 -- subprocess.run 互換シグネチャ
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                # 実機の Claude Code 2.x 出力形式: id が `<name>@<marketplace>`
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"id": "edit-guardrails@ak110-dotfiles", "version": "0.1.0"}]),
                )
            return _FakeResult(returncode=1, stderr="should not be called")

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is False
        # list だけ呼ばれて install は呼ばれないはず
        assert [c for c in calls if "install" in c] == []

    def test_fresh_install_happy_path(self, monkeypatch: pytest.MonkeyPatch):
        """未インストール + marketplace 未登録の場合、add → install の順に呼ぶ。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is True
        # add と install の両方が呼ばれているか
        assert any(c[:4] == ["claude", "plugin", "marketplace", "add"] for c in calls)
        assert any(c[:3] == ["claude", "plugin", "install"] and "edit-guardrails@ak110-dotfiles" in c for c in calls)

    def test_marketplace_already_registered_skips_add(self, monkeypatch: pytest.MonkeyPatch):
        """marketplace が既に登録済みなら add は呼ばず install だけ走る。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                # _MARKETPLACE_NAME が含まれる形で返す
                return _FakeResult(
                    returncode=0,
                    # pylint: disable-next=protected-access
                    stdout=json.dumps([{"name": _install_claude_plugins._MARKETPLACE_NAME}]),
                )
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is True
        # add は呼ばれていないこと
        assert [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "add"]] == []

    def test_plugin_list_failure_skips(self, monkeypatch: pytest.MonkeyPatch):
        """list が失敗したら後続は何もしない。"""
        seen: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            seen.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(returncode=1, stderr="boom")
            return _FakeResult(returncode=0, stdout="[]")

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is False
        # list 以外は呼ばれていないこと (失敗で早期 return)
        assert all(c[:3] == ["claude", "plugin", "list"] for c in seen)

    def test_install_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        """install が失敗しても例外は出ず False を返す。"""

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=1, stderr="install failed")
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is False

    def test_claude_timeout_is_swallowed(self, monkeypatch: pytest.MonkeyPatch):
        """claude CLI のタイムアウトはスキップに丸める (post-apply を落とさない)。"""

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            raise subprocess.TimeoutExpired(cmd, timeout=1)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is False


class TestExtractPluginNames:
    """_list_installed_plugins の各形式パース。"""

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            # 実機 (Claude Code 2.x): list[dict] で `id` が `<name>@<marketplace>` 形式
            (
                [
                    {"id": "edit-guardrails@ak110-dotfiles", "version": "0.1.0"},
                    {"id": "code-review@claude-plugins-official"},
                ],
                {"edit-guardrails", "code-review"},
            ),
            # 旧来形式: list[dict] で `name` フィールド
            ([{"name": "a"}, {"name": "b"}], {"a", "b"}),
            # `id` と `name` が混在しても両方拾う
            ([{"id": "a@x"}, {"name": "b"}], {"a", "b"}),
            # `id` に `@` がない場合はそのまま返す
            ([{"id": "plain"}], {"plain"}),
            # dict with "plugins" key
            ({"plugins": [{"name": "a"}]}, {"a"}),
            # flat dict
            ({"a": {}, "b": {}}, {"a", "b"}),
            # empty
            ([], set()),
            # 未知の形式 → 空集合
            (42, set()),
        ],
    )
    def test_various_shapes(self, data: object, expected: set[str]):
        # pylint: disable-next=protected-access
        assert set(_install_claude_plugins._extract_plugin_names(data)) == expected
