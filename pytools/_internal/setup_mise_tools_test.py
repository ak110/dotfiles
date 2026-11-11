# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""setup_miseのtoolsの試験。"""

# pylint: disable=protected-access

import subprocess
from pathlib import Path

import pytest

from pytools._internal import setup_mise as _setup_mise
from pytools._internal.setup_mise_test_helpers import (
    _EXPECTED_ENV_OVERRIDES,
    _EXPECTED_INSTALL_ENV_OVERRIDES,
    _LOCKED_ENV,
    _MISE_INSTALL_FAILURE_STDERR,
    _completed,
    _expected_env,
    _ls_response,
    _MiseSubprocessStub,
)
from pytools._internal.setup_mise_test_helpers import _mise_stub as _mise_stub
from pytools._internal.setup_mise_test_helpers import _windows_env as _windows_env


class TestRunTrustsWorkingTree:
    """`CHEZMOI_WORKING_TREE` と `mise.toml` 有無で `mise trust` 呼び出し有無が分かれる。"""

    def test_trust_invoked_when_toml_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        mise_stub: _MiseSubprocessStub,
    ):
        mise_toml = tmp_path / "mise.toml"
        mise_toml.write_text("[tools]\n", encoding="utf-8")
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{"version": "24"}]})

        assert _setup_mise.run().changed is True

        trust_calls = mise_stub.calls_for("trust")
        assert trust_calls and trust_calls[0]["args"] == ["trust", str(mise_toml)]
        # 全 mise CLI 呼び出しで非対話化 env_overrides が注入されていること
        for record in mise_stub.records:
            assert record["env_overrides"] == _expected_env(record)

    def test_trust_skipped_without_env(self, mise_stub: _MiseSubprocessStub):
        """CHEZMOI_WORKING_TREE が未設定のとき trust をスキップする。"""
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        _setup_mise.run()
        assert not mise_stub.calls_for("trust")

    def test_trust_skipped_when_toml_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        mise_stub: _MiseSubprocessStub,
    ):
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))  # mise.toml は配置しない
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        _setup_mise.run()
        assert not mise_stub.calls_for("trust")

    def test_trust_failure_does_not_block_install(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        mise_stub: _MiseSubprocessStub,
    ):
        (tmp_path / "mise.toml").write_text("", encoding="utf-8")
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))
        mise_stub.handlers[("trust",)] = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        _setup_mise.run()
        assert mise_stub.calls_for("install")


class TestRunNodeProvisioning:
    """`mise ls --global --json` 結果に応じて `use --global node@lts` 発行が決まる。"""

    @pytest.mark.parametrize(
        ("ls_payload", "use_expected"),
        [
            ({}, True),
            ([], True),
            ({"node": [{"version": "24"}]}, False),
            ({"tools": {"node": [{}]}}, False),
            ({"tools": {"python": [{}]}}, True),
            ([{"name": "node"}], False),
            ([{"name": "python"}, {"name": "node"}], False),
            ([{"name": "python"}], True),
            ([{"noname": "x"}], True),
        ],
    )
    def test_use_emitted_based_on_ls_payload(
        self,
        ls_payload: object,
        use_expected: bool,  # noqa: FBT001
        mise_stub: _MiseSubprocessStub,
    ):
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response(ls_payload)
        _setup_mise.run()
        use_calls = mise_stub.calls_for("use", "--global")
        if use_expected:
            assert len(use_calls) == 1
            assert use_calls[0]["args"] == ["use", "--global", "node@lts"]
        else:
            assert not use_calls

    def test_ls_failure_skips_use(self, mise_stub: _MiseSubprocessStub):
        mise_stub.handlers[("ls", "--global", "--json")] = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="boom"
        )
        _setup_mise.run()
        assert not mise_stub.calls_for("use", "--global")

    def test_invalid_json_skips_use(self, mise_stub: _MiseSubprocessStub):
        mise_stub.handlers[("ls", "--global", "--json")] = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr=""
        )
        _setup_mise.run()
        assert not mise_stub.calls_for("use", "--global")

    def test_use_failure_does_not_block_install(self, mise_stub: _MiseSubprocessStub):
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({})
        mise_stub.handlers[("use", "--global", "node@lts")] = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="fail"
        )
        assert _setup_mise.run().changed is True
        assert mise_stub.calls_for("install")


class TestRunInstallStep:
    """`mise install` の結果は後続を止めず、タイムアウトと env_overrides が注入される。"""

    @pytest.mark.parametrize(
        "install_response",
        [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom"),
            None,
        ],
    )
    def test_install_outcome_does_not_block_run(
        self,
        install_response: subprocess.CompletedProcess[str] | None,
        mise_stub: _MiseSubprocessStub,
    ):
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        mise_stub.handlers[("install",)] = install_response
        assert _setup_mise.run().changed is True
        install_calls = mise_stub.calls_for("install")
        assert len(install_calls) == 1
        assert install_calls[0]["args"] == ["install"]
        # インストール処理は通常コマンドより長いタイムアウト（600 秒）で呼ばれる
        assert install_calls[0]["timeout"] == 600
        assert install_calls[0]["env_overrides"] == _EXPECTED_INSTALL_ENV_OVERRIDES
        # working treeが無い場合は実行位置を指定しない（従来どおりglobal設定だけが対象）
        assert install_calls[0]["cwd"] is None

    def test_install_runs_in_working_tree_when_config_exists(
        self,
        mise_stub: _MiseSubprocessStub,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        """working treeに`mise.toml`がある場合、そこを実行位置として`install`を呼ぶ。

        miseは実行位置から設定を探索するため、実行位置を指定しないと
        working treeにだけ定義されたツールが恒久的に未導入のまま残る。
        """
        (tmp_path / "mise.toml").write_text("[tools]\n", encoding="utf-8")
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        assert _setup_mise.run().changed is True
        install_calls = mise_stub.calls_for("install")
        assert len(install_calls) == 1
        assert install_calls[0]["cwd"] == tmp_path
        assert install_calls[0]["env_overrides"] == _EXPECTED_INSTALL_ENV_OVERRIDES

    def test_locked_install_failure_is_not_retried_without_lock(
        self,
        mise_stub: _MiseSubprocessStub,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """lockedモードの`install`が失敗しても、lockを書き戻すlockedなしの再実行をしない。"""
        (tmp_path / "mise.toml").write_text("[tools]\n", encoding="utf-8")
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        mise_stub.handlers[("install",)] = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="jq@latest is not in the lockfile"
        )

        assert _setup_mise.run().changed is True

        install_calls = mise_stub.calls_for("install")
        assert len(install_calls) == 1
        assert install_calls[0]["env_overrides"] == _EXPECTED_INSTALL_ENV_OVERRIDES
        assert mise_stub.calls_for("reshim")
        others = [record for record in mise_stub.records if record["args"][0] != "install"]
        assert others
        assert all(not set(_LOCKED_ENV) & set(record["env_overrides"]) for record in others)

    def test_install_omits_working_tree_without_config(
        self,
        mise_stub: _MiseSubprocessStub,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        """working treeに`mise.toml`が無い場合は実行位置を指定しない。"""
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        assert _setup_mise.run().changed is True
        assert mise_stub.calls_for("install")[0]["cwd"] is None

    def test_run_reshims_after_install(self, mise_stub: _MiseSubprocessStub) -> None:
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})

        _setup_mise.run()

        assert len(mise_stub.calls_for("prune", "-y")) == 1
        assert len(mise_stub.calls_for("reshim")) == 1
        commands = [record["args"] for record in mise_stub.records]
        assert commands.index(["prune", "-y"]) > next(i for i, command in enumerate(commands) if command[0] == "install")
        assert commands.index(["reshim", "--force"]) > commands.index(["prune", "-y"])

    def test_run_does_not_reshim_when_prune_fails(self, mise_stub: _MiseSubprocessStub) -> None:
        """prune失敗時は未参照実体を残したままshimを再構築しない。"""
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        mise_stub.handlers[("prune", "-y")] = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="prune failed"
        )

        _setup_mise.run()

        assert len(mise_stub.calls_for("prune", "-y")) == 1
        assert not mise_stub.calls_for("reshim")


class TestRunRepairsMissingBinPaths:
    """導入済みツールの実行ファイルの配置先が欠落した場合に、同じ版を強制再インストールする。

    配置先を変えるツールオプション（`symlink_bins`など）を導入後に加えると、`mise install`は導入済みの版を
    再導入せず成功するため、コマンドを解決できない状態がユーザーの手作業なしには解消しない。
    """

    @staticmethod
    def _layout(tmp_path: Path) -> dict[str, Path]:
        """導入先と配置先の実体を作成する。actionlintだけ配置先`.mise-bins`が欠落している。"""
        installs = tmp_path / "installs"
        paths = {
            "actionlint": installs / "actionlint" / "1.7.12",
            "lychee": installs / "github-lycheeverse-lychee" / "lychee-v0.24.2",
        }
        for install_path in paths.values():
            install_path.mkdir(parents=True)
        (paths["lychee"] / "bin").mkdir()
        return paths

    @staticmethod
    def _ls_installed(paths: dict[str, Path]) -> subprocess.CompletedProcess[str]:
        return _ls_response(
            {
                "actionlint": [{"version": "1.7.12", "install_path": str(paths["actionlint"]), "installed": True}],
                "github:lycheeverse/lychee": [
                    {"version": "lychee-v0.24.2", "install_path": str(paths["lychee"]), "installed": True}
                ],
            }
        )

    def test_no_missing_bin_path_skips_force_install(self, mise_stub: _MiseSubprocessStub, tmp_path: Path) -> None:
        """配置先が全て実在する場合は`install --force`を呼ばない。"""
        paths = self._layout(tmp_path)
        (paths["actionlint"] / ".mise-bins").mkdir()
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        mise_stub.handlers[("bin-paths",)] = _completed(f"{paths['actionlint'] / '.mise-bins'}\n{paths['lychee'] / 'bin'}\n")
        mise_stub.handlers[("ls", "--current", "--installed", "--json")] = self._ls_installed(paths)

        _setup_mise.run()

        assert not mise_stub.calls_for("install", "--force")
        assert mise_stub.calls_for("reshim")

    @pytest.mark.parametrize("with_working_tree", [True, False])
    def test_reinstalls_only_missing_tool_before_prune(
        self,
        mise_stub: _MiseSubprocessStub,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        with_working_tree: bool,
    ) -> None:
        """欠落したツールだけを同じ版で再導入し、`install`と同じ実行位置で`prune -y`より前に呼ぶ。"""
        paths = self._layout(tmp_path / "data")
        working_tree = tmp_path / "wt"
        working_tree.mkdir()
        if with_working_tree:
            (working_tree / "mise.toml").write_text("[tools]\n", encoding="utf-8")
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(working_tree))
        missing = paths["actionlint"] / ".mise-bins"
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        mise_stub.handlers[("bin-paths",)] = _completed(f"{missing}\n{paths['lychee'] / 'bin'}\n")
        mise_stub.handlers[("ls", "--current", "--installed", "--json")] = self._ls_installed(paths)

        def _force_install(cmd: list[str]) -> subprocess.CompletedProcess[str]:
            del cmd  # noqa
            missing.mkdir()
            return _completed()

        mise_stub.handlers[("install", "--force")] = _force_install

        assert _setup_mise.run().changed is True

        force_calls = mise_stub.calls_for("install", "--force")
        assert [call["args"] for call in force_calls] == [["install", "--force", "actionlint@1.7.12"]]
        expected_cwd = working_tree if with_working_tree else None
        assert force_calls[0]["cwd"] == expected_cwd
        assert mise_stub.calls_for("install")[0]["cwd"] == expected_cwd
        assert force_calls[0]["timeout"] == 600
        # lockedモードの`install --force <ツール>@<版>`はlockにURLがあっても失敗するため、lockedの環境変数を与えない。
        assert force_calls[0]["env_overrides"] == _EXPECTED_ENV_OVERRIDES
        commands = [record["args"] for record in mise_stub.records]
        force_index = commands.index(["install", "--force", "actionlint@1.7.12"])
        assert commands.index(["install"]) < force_index < commands.index(["prune", "-y"])

    def test_bin_path_outside_install_paths_is_warned_not_reinstalled(
        self,
        mise_stub: _MiseSubprocessStub,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """どの導入先の配下にも無い欠落（共有ランタイムへのリンクなど）は再導入せず警告する。"""
        paths = self._layout(tmp_path)
        (paths["actionlint"] / ".mise-bins").mkdir()
        shared = tmp_path / "dotnet-root"
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        mise_stub.handlers[("bin-paths",)] = _completed(f"{shared}\n{paths['actionlint'] / '.mise-bins'}\n")
        mise_stub.handlers[("ls", "--current", "--installed", "--json")] = self._ls_installed(paths)

        with caplog.at_level("WARNING"):
            _setup_mise.run()

        assert not mise_stub.calls_for("install", "--force")
        assert str(shared) in caplog.text
        assert mise_stub.calls_for("reshim")

    @pytest.mark.parametrize(
        ("failing", "response"),
        [
            (("install", "--force"), _completed(returncode=1, stderr="boom")),
            (("install", "--force"), None),
            (("install", "--force"), _completed()),
            (("ls", "--current", "--installed", "--json"), _completed(returncode=1, stderr="boom")),
            (("ls", "--current", "--installed", "--json"), _completed("not json")),
            (("bin-paths",), _completed(returncode=1, stderr="boom")),
            (("bin-paths",), None),
        ],
    )
    def test_failures_warn_and_continue(
        self,
        mise_stub: _MiseSubprocessStub,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        failing: tuple[str, ...],
        response: subprocess.CompletedProcess[str] | None,
    ) -> None:
        """再導入・検出の失敗と再導入後も残る欠落は警告し、後続の`prune -y`と`reshim --force`を続ける。

        `install --force`が成功しても配置先が生成されない場合は、手動の復旧コマンドを警告へ含める。
        """
        paths = self._layout(tmp_path)
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
        mise_stub.handlers[("bin-paths",)] = _completed(f"{paths['actionlint'] / '.mise-bins'}\n")
        mise_stub.handlers[("ls", "--current", "--installed", "--json")] = self._ls_installed(paths)
        mise_stub.handlers[failing] = response

        with caplog.at_level("WARNING"):
            _setup_mise.run()

        assert caplog.records
        if failing == ("install", "--force"):
            assert "mise install --force actionlint@1.7.12" in caplog.text
        assert mise_stub.calls_for("prune", "-y")
        assert mise_stub.calls_for("reshim", "--force")


def test_install_failure_is_skipped_warning_with_separate_next_action(
    mise_stub: _MiseSubprocessStub, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`mise install`の失敗は警告で出し、進捗行を除いた失敗理由と次の操作の文を分け、工程をスキップと数える。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_DOTFILES_MODE", "minimal")
    # パッケージマネージャー経由で導入したmiseでは`self-update`が失敗し、更新と数えない。
    mise_stub.handlers[("self-update",)] = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="disabled")
    mise_stub.handlers[("install",)] = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr=_MISE_INSTALL_FAILURE_STDERR
    )
    with caplog.at_level("INFO"):
        outcome = _setup_mise.run()

    assert not outcome.changed
    assert outcome.failure is None
    [record] = [record for record in caplog.records if "`install` に失敗" in record.getMessage()]
    assert record.levelname == "WARNING"
    first_line, *output_lines = record.getMessage().splitlines()
    assert "ネットワーク到達" in first_line
    assert first_line.endswith("へ報告する")
    assert "Failed to install tools" not in first_line
    output = "\n".join(output_lines)
    assert "mise ERROR Failed to install tools: core:node@lts" in output
    assert "core:node@lts: error sending request" in output
    assert "storage.googleapis.com" in output
    for progress in ("installing", "WARN", "1/2", "\x1b"):
        assert progress not in record.getMessage()
