"""pytools._internal.setup_mise のテスト。"""

import json
import subprocess
import typing
from pathlib import Path

import pytest

from pytools._internal import claude_common, winutils
from pytools._internal import setup_mise as _setup_mise

# 非対話化のために `_run_mise` から注入される環境変数の期待値。
_EXPECTED_ENV_OVERRIDES = {"MISE_YES": "1", "CI": "1"}


class _MiseSubprocessStub:
    """`claude_common.run_subprocess` を差し替えるスタブ。

    `mise <subcommand>` の呼び出しを `records` に蓄積し、登録された
    `handlers` から前方一致でレスポンスを返す。
    """

    def __init__(self) -> None:
        self.records: list[dict[str, typing.Any]] = []
        self.handlers: dict[tuple[str, ...], subprocess.CompletedProcess[str] | None] = {}

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
                "args": list(sub_args),
                "env_overrides": kwargs.get("env_overrides"),
                "timeout": kwargs.get("timeout"),
            }
        )
        sorted_keys = list(self.handlers)
        sorted_keys.sort(key=len, reverse=True)
        for key in sorted_keys:
            if sub_args[: len(key)] == key:
                return self.handlers[key]
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    def calls_for(self, *prefix: str) -> list[dict[str, typing.Any]]:
        return [r for r in self.records if tuple(r["args"][: len(prefix)]) == prefix]


def _ls_response(payload: object) -> subprocess.CompletedProcess[str]:
    """`mise ls --global --json` 用のレスポンスを生成する。"""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")


@pytest.fixture(name="mise_stub")
def _mise_stub(monkeypatch: pytest.MonkeyPatch) -> _MiseSubprocessStub:
    """既定で mise バイナリ検出済み・非 Windows・CHEZMOI_WORKING_TREE 未設定とする。"""
    stub = _MiseSubprocessStub()
    stub.install(monkeypatch)
    monkeypatch.setattr(_setup_mise, "_find_mise_binary", lambda: Path("/fake/mise"))
    monkeypatch.setattr(_setup_mise, "_is_windows", lambda: False)
    monkeypatch.delenv("CHEZMOI_WORKING_TREE", raising=False)
    return stub


class TestRunWithoutMise:
    """mise バイナリ未検出時は何もしない。"""

    def test_skips_when_binary_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_setup_mise, "_find_mise_binary", lambda: None)
        assert _setup_mise.run() is False


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
            assert record["env_overrides"] == _EXPECTED_ENV_OVERRIDES

    def test_trust_skipped_without_env(self, mise_stub: _MiseSubprocessStub):
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
        assert install_calls[0]["timeout"] == _setup_mise._MISE_INSTALL_TIMEOUT  # noqa: SLF001  # pylint: disable=protected-access
        assert install_calls[0]["env_overrides"] == _EXPECTED_ENV_OVERRIDES


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


class TestNonInteractiveEnvInjection:
    """全 mise CLI 呼び出しに `MISE_YES=1` ・ `CI=1` が注入されることを確認する。

    aqua/npm バックエンドの初回ダウンロード時に確認プロンプトでブロックする事象を
    防ぐため、trust・ls・install の各サブコマンドが env_overrides 付きで呼ばれる。
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
            assert record["env_overrides"] == _EXPECTED_ENV_OVERRIDES
