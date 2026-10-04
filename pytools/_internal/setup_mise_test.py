"""pytools._internal.setup_mise のテスト。"""

import json
import os
import subprocess
import typing
from pathlib import Path

import httpx
import pytest

from pytools._internal import claude_common, winutils
from pytools._internal import setup_mise as _setup_mise

# 無人セットアップのために`_run_mise`から注入される環境変数の期待値。
_EXPECTED_ENV_OVERRIDES = {
    "MISE_YES": "1",
    "CI": "1",
    "MISE_FETCH_REMOTE_VERSIONS_TIMEOUT": "120s",
}
# 作業ツリーの`mise.lock`を書き戻さないよう、`install`だけへ加えるlockedモードの環境変数。
_LOCKED_ENV = {"MISE_LOCKED": "1", "MISE_LOCKED_SCOPES": "project"}
_EXPECTED_INSTALL_ENV_OVERRIDES = _EXPECTED_ENV_OVERRIDES | _LOCKED_ENV


def _expected_env(record: dict[str, typing.Any]) -> dict[str, str]:
    """呼び出しの種類ごとの期待する環境変数を返す。lockedモードは`install`だけに与える。"""
    return _EXPECTED_INSTALL_ENV_OVERRIDES if record["args"] == ["install"] else _EXPECTED_ENV_OVERRIDES


class _MiseSubprocessStub:
    """`claude_common.run_subprocess` を差し替えるスタブ。

    `mise <subcommand>` の呼び出しを `records` に蓄積し、登録された
    `handlers` から前方一致でレスポンスを返す。
    """

    def __init__(self) -> None:
        self.records: list[dict[str, typing.Any]] = []
        # 呼び出しごとに応答が変わる場合（修復の前後で異なる`bin-paths`など）は、コマンドを受け取る関数を登録する。
        self.handlers: dict[
            tuple[str, ...],
            subprocess.CompletedProcess[str] | None | typing.Callable[[list[str]], subprocess.CompletedProcess[str] | None],
        ] = {}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(claude_common, "run_subprocess", self._fake_run_subprocess)

    def _fake_run_subprocess(
        self,
        cmd: list[str],
        **kwargs: typing.Any,
    ) -> subprocess.CompletedProcess[str] | None:
        sub_args = tuple(cmd[1:])
        self.records.append(
            {
                "cmd": list(cmd),
                "args": list(sub_args),
                "env_overrides": kwargs.get("env_overrides"),
                "timeout": kwargs.get("timeout"),
                "cwd": kwargs.get("cwd"),
            }
        )
        sorted_keys = list(self.handlers)
        sorted_keys.sort(key=len, reverse=True)
        for key in sorted_keys:
            if sub_args[: len(key)] == key:
                handler = self.handlers[key]
                if handler is None or isinstance(handler, subprocess.CompletedProcess):
                    return handler
                return handler(list(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    def calls_for(self, *prefix: str) -> list[dict[str, typing.Any]]:
        return [r for r in self.records if tuple(r["args"][: len(prefix)]) == prefix]


def _ls_response(payload: object) -> subprocess.CompletedProcess[str]:
    """`mise ls --global --json` 用のレスポンスを生成する。"""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")


@pytest.fixture(name="mise_stub")
def _mise_stub(monkeypatch: pytest.MonkeyPatch) -> _MiseSubprocessStub:
    """個々のテストが差し替えない限り、mise バイナリ検出済み・非 Windows・CHEZMOI_WORKING_TREE 未設定とする。"""
    stub = _MiseSubprocessStub()
    stub.install(monkeypatch)
    monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: Path("/fake/mise"))
    monkeypatch.setattr(_setup_mise, "_is_windows", lambda: False)
    monkeypatch.delenv("CHEZMOI_WORKING_TREE", raising=False)
    return stub


class TestFindMiseBinary:
    """mise実行ファイルのPATH・既知パス探索を検証する。"""

    def test_returns_path_entry_as_absolute_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        mise_path = tmp_path / "bin" / "mise"
        monkeypatch.setattr(_setup_mise.shutil, "which", lambda _name: str(mise_path))

        assert _setup_mise.find_mise_binary() == mise_path

    def test_returns_linux_known_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        mise_path = tmp_path / ".local" / "bin" / "mise"
        mise_path.parent.mkdir(parents=True)
        mise_path.write_bytes(b"mise")
        monkeypatch.setattr(_setup_mise.shutil, "which", lambda _name: None)
        monkeypatch.setattr(_setup_mise, "_is_windows", lambda: False)
        monkeypatch.setattr(_setup_mise.Path, "home", lambda: tmp_path)

        assert _setup_mise.find_mise_binary() == mise_path

    def test_returns_windows_known_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        localappdata = tmp_path / "AppData" / "Local"
        mise_path = localappdata / "Microsoft" / "WinGet" / "Links" / "mise.exe"
        mise_path.parent.mkdir(parents=True)
        mise_path.write_bytes(b"mise")
        monkeypatch.setattr(_setup_mise.shutil, "which", lambda _name: None)
        monkeypatch.setattr(_setup_mise, "_is_windows", lambda: True)
        monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

        assert _setup_mise.find_mise_binary() == mise_path

    def test_returns_none_when_not_found(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(_setup_mise.shutil, "which", lambda _name: None)
        monkeypatch.setattr(_setup_mise, "_is_windows", lambda: False)
        monkeypatch.setattr(_setup_mise.Path, "home", lambda: tmp_path)

        assert _setup_mise.find_mise_binary() is None


class TestRunMiseInstallation:
    """mise未検出時の自動導入と再解決を検証する。"""

    def test_run_installs_mise_then_continues(self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub):
        binaries = iter((None, Path("/fake/mise")))
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: next(binaries))
        installs: list[bool] = []

        def install() -> bool:
            installs.append(True)
            return True

        monkeypatch.setattr(_setup_mise, "_ensure_mise_installed", install)

        assert _setup_mise.run() is True
        assert installs == [True]
        assert mise_stub.calls_for("install")

    def test_run_skips_when_install_does_not_resolve(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub
    ) -> None:
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: None)
        installs: list[bool] = []

        def install() -> bool:
            installs.append(True)
            return False

        monkeypatch.setattr(_setup_mise, "_ensure_mise_installed", install)

        assert _setup_mise.run() is False
        assert installs == [True]
        assert not mise_stub.records

    def test_run_does_not_install_when_mise_is_resolved(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub
    ) -> None:
        monkeypatch.setattr(
            _setup_mise,
            "_ensure_mise_installed",
            lambda: pytest.fail("導入済み環境でインストーラーを呼んだ"),
        )
        assert _setup_mise.run() is True
        assert mise_stub.calls_for("install")

    def test_run_skips_when_installer_download_fails(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub
    ) -> None:
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: None)

        class _FailingClient:
            def get(self, _url: str) -> typing.NoReturn:
                raise httpx.ConnectError("offline")

            def close(self) -> None:
                return None

        monkeypatch.setattr(_setup_mise.httpx, "Client", lambda **_kwargs: _FailingClient())
        assert _setup_mise.run() is False
        assert not mise_stub.records

    def test_run_installs_mise_with_winget_on_windows(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub
    ) -> None:
        binaries = iter((None, Path("/fake/mise")))
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: next(binaries))
        monkeypatch.setattr(_setup_mise, "_is_windows", lambda: True)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)

        assert _setup_mise.run() is True
        assert [record["cmd"] for record in mise_stub.records].count(
            [
                "winget",
                "install",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
                "jdx.mise",
            ]
        ) == 1
        assert mise_stub.calls_for("install")


class TestEnsureMiseUpToDate:
    """`mise self-update -y` の呼び出しと失敗時の後続ステップ継続を検証する。"""

    def test_self_update_invoked(self, mise_stub: _MiseSubprocessStub):
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{"version": "24"}]})

        assert _setup_mise.run() is True

        self_update_calls = mise_stub.calls_for("self-update")
        assert self_update_calls and self_update_calls[0]["args"] == ["self-update", "-y"]
        assert self_update_calls[0]["env_overrides"] == _EXPECTED_ENV_OVERRIDES

    def test_self_update_failure_does_not_block_install(self, mise_stub: _MiseSubprocessStub):
        """パッケージマネージャー経由インストール等で `self-update` が失敗しても後続ステップは継続する。"""
        mise_stub.handlers[("self-update", "-y")] = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not available via package manager"
        )
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})

        _setup_mise.run()

        assert mise_stub.calls_for("install")

    def test_self_update_none_result_does_not_block_install(self, mise_stub: _MiseSubprocessStub):
        """タイムアウト・例外で`None`が返っても後続ステップは継続する。"""
        mise_stub.handlers[("self-update", "-y")] = None
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})

        _setup_mise.run()

        assert mise_stub.calls_for("install")


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

        assert _setup_mise.run() is True

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
        assert _setup_mise.run() is True
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
        assert _setup_mise.run() is True
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
        assert _setup_mise.run() is True
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

        assert _setup_mise.run() is True

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
        assert _setup_mise.run() is True
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


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


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

        assert _setup_mise.run() is True

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


class _WinregFake:
    """`winutils.import_winreg()` が返すモジュールのスタブ。"""

    REG_SZ = 1
    REG_EXPAND_SZ = 2


@pytest.fixture(name="windows_env")
def _windows_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mise_stub: _MiseSubprocessStub,
) -> dict[str, typing.Any]:
    """Windows 分岐のテスト用 fixture。

    `_ensure_windows_user_path_has_shims` が利用する `winutils` 関数群と
    レジストリの読み書きをスタブで差し替える。``state['existing']`` を
    更新してから ``run()`` を呼び、``state['writes']`` を観測する。
    """
    # 前段（trust・ls・install）の影響を排して PATH 操作のみを観測する
    mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})
    monkeypatch.setattr(_setup_mise, "_is_windows", lambda: True)

    localappdata = tmp_path / "AppData" / "Local"
    shims_dir = localappdata / "mise" / "shims"
    shims_dir.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

    state: dict[str, typing.Any] = {
        "existing": ("", _WinregFake.REG_SZ),
        "writes": [],
        "broadcasts": 0,
        "shims_dir": shims_dir,
    }

    def fake_read(name: str) -> tuple[str | None, int]:
        del name
        return state["existing"]

    def fake_write(name: str, value: str, value_type: int) -> None:
        state["writes"].append({"name": name, "value": value, "type": value_type})

    def fake_broadcast() -> None:
        state["broadcasts"] += 1

    monkeypatch.setattr(winutils, "read_user_env_var", fake_read)
    monkeypatch.setattr(winutils, "write_user_env_var", fake_write)
    monkeypatch.setattr(winutils, "broadcast_environment_change", fake_broadcast)
    monkeypatch.setattr(winutils, "import_winreg", lambda: _WinregFake)
    return state


class TestRunWindowsPathSetup:
    """Windows 分岐: ユーザー PATH への shims 追加と既存検出を `run()` 経由で検証する。"""

    def test_appends_shims_when_missing(self, windows_env: dict[str, typing.Any]):
        windows_env["existing"] = (r"C:\Windows", _WinregFake.REG_SZ)
        _setup_mise.run()
        writes = windows_env["writes"]
        assert len(writes) == 1
        assert writes[0]["name"] == "Path"
        assert writes[0]["value"] == r"C:\Windows;%LOCALAPPDATA%\mise\shims"
        # REG_SZ で保持されていても REG_EXPAND_SZ へ昇格させる
        assert writes[0]["type"] == _WinregFake.REG_EXPAND_SZ
        assert windows_env["broadcasts"] == 1

    def test_avoids_duplicate_separator(self, windows_env: dict[str, typing.Any]):
        windows_env["existing"] = (r"C:\Windows;", _WinregFake.REG_EXPAND_SZ)
        _setup_mise.run()
        assert windows_env["writes"][0]["value"] == r"C:\Windows;%LOCALAPPDATA%\mise\shims"

    def test_appends_from_empty(self, windows_env: dict[str, typing.Any]):
        windows_env["existing"] = ("", _WinregFake.REG_EXPAND_SZ)
        _setup_mise.run()
        assert windows_env["writes"][0]["value"] == r"%LOCALAPPDATA%\mise\shims"

    def test_already_registered_literal_entry(self, windows_env: dict[str, typing.Any]):
        windows_env["existing"] = (
            r"C:\Windows;%LOCALAPPDATA%\mise\shims",
            _WinregFake.REG_EXPAND_SZ,
        )
        _setup_mise.run()
        assert not windows_env["writes"]
        assert windows_env["broadcasts"] == 0

    def test_already_registered_expanded_case_insensitive(self, windows_env: dict[str, typing.Any]):
        # %LOCALAPPDATA% 展開済みかつ大小不一致のエントリを既登録として認識する
        expanded = str(windows_env["shims_dir"]).upper()
        windows_env["existing"] = (f"C:\\WINDOWS;{expanded}", _WinregFake.REG_EXPAND_SZ)
        _setup_mise.run()
        assert not windows_env["writes"]

    def test_not_present_when_other_paths(self, windows_env: dict[str, typing.Any]):
        windows_env["existing"] = (
            r"C:\Windows;C:\Users\x\AppData\Local\Programs\Python",
            _WinregFake.REG_EXPAND_SZ,
        )
        _setup_mise.run()
        assert len(windows_env["writes"]) == 1


class TestWindowsProcessPathPriority:
    """現プロセスPATHの既存優先順位を維持する。"""

    def test_appends_shims_after_existing_node_path(
        self,
        windows_env: dict[str, typing.Any],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """既存Nodeのパスをmise shimsより前に保つ。"""
        node_path = r"C:\DATA\Apps\node"
        monkeypatch.setenv("PATH", node_path)

        _setup_mise.run()

        assert os.environ["PATH"] == os.pathsep.join((node_path, str(windows_env["shims_dir"])))

    def test_sets_shims_without_separator_when_path_empty(
        self,
        windows_env: dict[str, typing.Any],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """空PATHではshimsだけを設定する。"""
        monkeypatch.setenv("PATH", "")

        _setup_mise.run()

        assert os.environ["PATH"] == str(windows_env["shims_dir"])


class TestWindowsMiseSubprocessEnvironment:
    """Windowsのmise子プロセスが管理対象の.NET共有ルートを優先することを検証する。"""

    def test_prepends_default_dotnet_root(
        self,
        windows_env: dict[str, typing.Any],
        mise_stub: _MiseSubprocessStub,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        del windows_env
        system_dotnet = r"C:\Program Files\dotnet"
        monkeypatch.delenv("MISE_DOTNET_ROOT", raising=False)
        monkeypatch.delenv("MISE_DATA_DIR", raising=False)
        monkeypatch.setenv("PATH", system_dotnet)
        mise_data_dir = Path(os.environ["LOCALAPPDATA"]) / "mise"
        monkeypatch.setattr(_setup_mise.platformdirs, "user_data_dir", lambda *_args, **_kwargs: str(mise_data_dir))

        _setup_mise.run()

        expected_root = str(mise_data_dir / "dotnet-root")
        for record in mise_stub.records:
            env_overrides = record["env_overrides"]
            assert env_overrides["MISE_DOTNET_ROOT"] == expected_root
            assert env_overrides["PATH"].split(";") == [expected_root, system_dotnet]

    def test_respects_explicit_dotnet_root_without_duplication(
        self,
        windows_env: dict[str, typing.Any],
        mise_stub: _MiseSubprocessStub,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        del windows_env
        custom_root = r"D:\tools\dotnet"
        monkeypatch.setenv("MISE_DOTNET_ROOT", custom_root)
        monkeypatch.setenv("PATH", f"{custom_root};C:\\Program Files\\dotnet;{custom_root.upper()}")

        _setup_mise.run()

        for record in mise_stub.records:
            env_overrides = record["env_overrides"]
            assert env_overrides["MISE_DOTNET_ROOT"] == custom_root
            assert env_overrides["PATH"].split(";") == [custom_root, r"C:\Program Files\dotnet"]


class TestNonInteractiveEnvInjection:
    """全mise CLI呼び出しに無人セットアップ用の環境変数が注入されることを確認する。

    aqua/npm バックエンドの初回ダウンロード時に確認プロンプトでブロックする事象を
    防ぎ、版一覧取得の上限を延長するため、trust・ls・installの各サブコマンドが
    env_overrides付きで呼ばれる。
    """

    def test_all_mise_invocations_receive_env_overrides(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        mise_stub: _MiseSubprocessStub,
    ):
        (tmp_path / "mise.toml").write_text("[tools]\n", encoding="utf-8")
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{"version": "24"}]})

        _setup_mise.run()

        assert mise_stub.calls_for("trust"), "mise trust が呼ばれていない"
        assert mise_stub.calls_for("ls", "--global", "--json"), "mise ls が呼ばれていない"
        assert mise_stub.calls_for("install"), "mise install が呼ばれていない"
        for record in mise_stub.records:
            assert record["env_overrides"] == _expected_env(record)
