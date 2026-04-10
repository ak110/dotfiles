"""pytools._install_claude_plugins のテスト。

subprocess.run / shutil.which をモックして、前提条件分岐・marketplace 登録・
plugin install / update の各パスを検証する。
"""

import json
import pathlib
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


@pytest.fixture(name="fake_target_info")
def _fake_target_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """marketplace.json の読み込み結果を固定値に差し替える。

    テストを実際の marketplace.json の内容から切り離すため。
    対象プラグインはハードコードではなく marketplace.json 由来で決まるため、
    複数プラグインが正しくループで処理されることを検証できるよう 2 件返す。
    """
    monkeypatch.setattr(
        _install_claude_plugins,
        "_read_target_info",
        lambda _root: ({"agent-toolkit": "0.2.0", "sample-plugin": "1.0.0"}, set()),
    )


def _plugin_list_json(*entries: dict[str, object]) -> str:
    """テスト用の `claude plugin list --json` 出力を組み立てる。"""
    return json.dumps(list(entries))


class TestPrerequisites:
    """前提条件 (claude / uv の存在) のチェック。"""

    def test_missing_claude_skips(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: None if name == "claude" else "/usr/bin/uv")
        assert _install_claude_plugins.run() is False

    def test_missing_uv_skips(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: None if name == "uv" else "/usr/bin/claude")
        assert _install_claude_plugins.run() is False


@pytest.mark.usefixtures("fake_which_present", "fake_target_info")
class TestRunFlow:
    """メインフローのテスト (前提条件は満たしている状態)。"""

    def test_already_installed_up_to_date_skips(self, monkeypatch: pytest.MonkeyPatch):
        """既に配布 version と一致していれば全プラグインについて update は呼ばない。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001 -- subprocess.run 互換シグネチャ
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "project"},
                        {"id": "sample-plugin@ak110-dotfiles", "version": "1.0.0", "scope": "project"},
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    # pylint: disable-next=protected-access
                    stdout=json.dumps([{"name": _install_claude_plugins._MARKETPLACE_NAME}]),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "update"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1, stderr="should not be called")

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is False
        assert [c for c in calls if c[:3] == ["claude", "plugin", "update"]] == []
        assert [c for c in calls if c[:3] == ["claude", "plugin", "install"]] == []

    def test_already_installed_version_drift_updates(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みでも version が古ければ refresh + update が実行される。

        片方のプラグインだけ version が古いケース。もう片方は最新のため
        update 呼び出しは古い側にのみ発行されることも併せて検証する。
        """
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {"id": "agent-toolkit@ak110-dotfiles", "version": "0.1.0", "scope": "project"},
                        {"id": "sample-plugin@ak110-dotfiles", "version": "1.0.0", "scope": "project"},
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    # pylint: disable-next=protected-access
                    stdout=json.dumps([{"name": _install_claude_plugins._MARKETPLACE_NAME}]),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "update"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "update"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is True
        assert any(c[:4] == ["claude", "plugin", "marketplace", "update"] for c in calls)
        update_calls = [c for c in calls if c[:3] == ["claude", "plugin", "update"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in update_calls)
        # --scope project が渡されていること
        assert any("--scope" in c and "project" in c for c in update_calls)
        # 最新である sample-plugin に対しては update を発行しない
        assert not any("sample-plugin@ak110-dotfiles" in c for c in update_calls)
        # refresh が update よりも先に呼ばれていること (marketplace メタデータを反映させてから update)
        refresh_index = next(i for i, c in enumerate(calls) if c[:4] == ["claude", "plugin", "marketplace", "update"])
        update_index = next(i for i, c in enumerate(calls) if c[:3] == ["claude", "plugin", "update"])
        assert refresh_index < update_index

    def test_fresh_install_happy_path(self, monkeypatch: pytest.MonkeyPatch):
        """未インストール + marketplace 未登録の場合、add → 全プラグイン install の順に呼ぶ。"""
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
        # add が呼ばれ、かつ marketplace.json 由来の全プラグインに対し install が呼ばれていること
        assert any(c[:4] == ["claude", "plugin", "marketplace", "add"] for c in calls)
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)
        assert any("sample-plugin@ak110-dotfiles" in c for c in install_calls)
        # --scope project が渡されていること
        for ic in install_calls:
            assert "--scope" in ic and "project" in ic

    def test_marketplace_already_registered_skips_add(self, monkeypatch: pytest.MonkeyPatch):
        """marketplace が既に登録済みなら add は呼ばず install だけ実行される。"""
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
        # 対象プラグインは全て install されていること
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)
        assert any("sample-plugin@ak110-dotfiles" in c for c in install_calls)

    def test_mixed_installed_and_missing(self, monkeypatch: pytest.MonkeyPatch):
        """片方だけインストール済みの混在状態では、未インストール側のみ install される。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "project"},
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    # pylint: disable-next=protected-access
                    stdout=json.dumps([{"name": _install_claude_plugins._MARKETPLACE_NAME}]),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "update"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is True
        # 既にインストール済みの agent-toolkit は install されない
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        assert not any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)
        # 未インストールの sample-plugin は install される
        assert any("sample-plugin@ak110-dotfiles" in c for c in install_calls)
        # 既にインストール済みの plugin が最新であれば update は呼ばれない
        assert [c for c in calls if c[:3] == ["claude", "plugin", "update"]] == []

    def test_empty_target_versions_skips(self, monkeypatch: pytest.MonkeyPatch):
        """marketplace.json に対象 plugin が無ければ claude コマンドを一切呼ばずにスキップする。"""
        monkeypatch.setattr(_install_claude_plugins, "_read_target_info", lambda _root: ({}, set()))
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is False
        assert not calls

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

    def test_user_scope_ignored_in_version_check(self, monkeypatch: pytest.MonkeyPatch):
        """user scope のエントリは install/update 判定に使われない。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                # agent-toolkit が user scope にのみ存在
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "user"},
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    # pylint: disable-next=protected-access
                    stdout=json.dumps([{"name": _install_claude_plugins._MARKETPLACE_NAME}]),
                )
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "uninstall"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is True
        # user scope のエントリは無視され、project scope に新規 install される
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)
        # user scope の清掃 (uninstall) が呼ばれること
        uninstall_calls = [c for c in calls if c[:3] == ["claude", "plugin", "uninstall"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in uninstall_calls)

    def test_deprecated_plugin_uninstalled(self, monkeypatch: pytest.MonkeyPatch):
        """deprecated プラグインがインストール済みならアンインストールされる。"""
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_target_info",
            lambda _root: ({"agent-toolkit": "0.2.0"}, {"old-plugin"}),
        )
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {"id": "old-plugin@ak110-dotfiles", "version": "0.5.0", "scope": "user"},
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    # pylint: disable-next=protected-access
                    stdout=json.dumps([{"name": _install_claude_plugins._MARKETPLACE_NAME}]),
                )
            if cmd[:3] == ["claude", "plugin", "uninstall"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        assert _install_claude_plugins.run() is True
        # deprecated プラグインのアンインストールが呼ばれること
        uninstall_calls = [c for c in calls if c[:3] == ["claude", "plugin", "uninstall"]]
        assert any("old-plugin@ak110-dotfiles" in c for c in uninstall_calls)
        # 通常プラグインの install も呼ばれること
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)


class TestExtractPluginVersionMap:
    """`claude plugin list --json` のパース (project scope フィルタ付き)。"""

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            # project scope のエントリのみ含まれる
            (
                [
                    {"id": "agent-toolkit@ak110-dotfiles", "version": "0.1.0", "scope": "project"},
                    {"id": "other@marketplace", "version": "1.2.3", "scope": "user"},
                ],
                {"agent-toolkit": "0.1.0"},
            ),
            # scope が存在しないエントリは後方互換で含まれる
            ([{"id": "a@x", "version": "1.0"}], {"a": "1.0"}),
            # version 欠落は空文字列扱い
            ([{"id": "a@x", "scope": "project"}], {"a": ""}),
            # user scope のみのエントリは除外される
            ([{"id": "a@x", "version": "1.0", "scope": "user"}], {}),
            # dict with "plugins" key
            ({"plugins": [{"name": "a", "version": "1", "scope": "project"}]}, {"a": "1"}),
            # flat dict (version 不明として空文字列)
            ({"a": {}, "b": {}}, {"a": "", "b": ""}),
            # empty
            ([], {}),
            # 未知の形式 → 空辞書
            (42, {}),
        ],
    )
    def test_various_shapes(self, data: object, expected: dict[str, str]):
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._extract_plugin_version_map(data) == expected


class TestReadTargetInfo:
    """marketplace.json から version / deprecated を読む helper のテスト。"""

    def test_reads_actual_marketplace_json(self):
        """本リポジトリ配下の marketplace.json を読み取れる。"""
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        # pylint: disable-next=protected-access
        targets, deprecated = _install_claude_plugins._read_target_info(repo_root)
        # agent-toolkit は通常プラグインとして含まれる
        assert "agent-toolkit" in targets
        assert targets["agent-toolkit"]  # 空文字列ではない
        # edit-guardrails は deprecated 扱い
        assert "edit-guardrails" in deprecated
        assert "edit-guardrails" not in targets

    def test_missing_file_returns_empty(self, tmp_path: pathlib.Path):
        """marketplace.json がない場合は空辞書・空集合。"""
        # pylint: disable-next=protected-access
        targets, deprecated = _install_claude_plugins._read_target_info(tmp_path)
        assert not targets
        assert not deprecated
