"""setup_mise試験の共通fixture・入力と補助関数。"""

import json
import subprocess
import typing
from pathlib import Path

import pytest

from pytools._internal import common, setup_mise_common, winutils
from pytools._internal import setup_mise as _setup_mise

_EXPECTED_ENV_OVERRIDES = {
    "MISE_YES": "1",
    "CI": "1",
    "MISE_FETCH_REMOTE_VERSIONS_TIMEOUT": "120s",
}

_LOCKED_ENV = {"MISE_LOCKED": "1", "MISE_LOCKED_SCOPES": "project"}

_EXPECTED_INSTALL_ENV_OVERRIDES = _EXPECTED_ENV_OVERRIDES | _LOCKED_ENV


def _expected_env(record: dict[str, typing.Any]) -> dict[str, str]:
    """呼び出しの種類ごとの期待する環境変数を返す。lockedモードは`install`だけに与える。"""
    return _EXPECTED_INSTALL_ENV_OVERRIDES if record["args"] == ["install"] else _EXPECTED_ENV_OVERRIDES


class _MiseSubprocessStub:
    """`common.run_subprocess` を差し替えるスタブ。

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
        monkeypatch.setattr(common, "run_subprocess", self._fake_run_subprocess)

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
    monkeypatch.setattr(setup_mise_common, "is_windows", lambda: False)
    monkeypatch.delenv("CHEZMOI_WORKING_TREE", raising=False)
    return stub


_WINGET_MISE = Path(r"C:\Users\user\AppData\Local\Microsoft\WinGet\Links\mise.exe")

_WINGET_UPGRADE_ARGS = [
    "upgrade",
    "--id",
    "jdx.mise",
    "--exact",
    "--accept-package-agreements",
    "--accept-source-agreements",
    "--disable-interactivity",
]


def _version_response(version: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=f"{version} linux-x64 (2026-10-26)\n", stderr="")


_GCLOUD_PLUGIN_INSTALL = ("plugins", "install", "gcloud", "https://github.com/jdx/vfox-gcloud")


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


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
    monkeypatch.setattr(setup_mise_common, "is_windows", lambda: True)

    localappdata = tmp_path / "AppData" / "Local"
    shims_dir = localappdata / "mise" / "shims"
    shims_dir.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

    state: dict[str, typing.Any] = {
        "existing": ("", _WinregFake.REG_SZ),
        "system": "",
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

    def fake_read_system(name: str) -> tuple[str | None, int]:
        del name
        return state["system"], _WinregFake.REG_EXPAND_SZ

    monkeypatch.setattr(winutils, "read_user_env_var", fake_read)
    monkeypatch.setattr(winutils, "read_system_env_var", fake_read_system)
    monkeypatch.setattr(winutils, "write_user_env_var", fake_write)
    monkeypatch.setattr(winutils, "broadcast_environment_change", fake_broadcast)
    monkeypatch.setattr(winutils, "import_winreg", lambda: _WinregFake)
    return state


_MISE_INSTALL_FAILURE_STDERR = (
    "\x1b[2mmise\x1b[0m \x1b[2mby @jdx\x1b[0m – installing 2 tools\n"
    "\x1b[33mmise\x1b[0m \x1b[33mWARN\x1b[0m  mise-versions endpoint=version_list tool=jq outcome=failed "
    'status=0 fallback=true error="error sending request"\n'
    "\x1b[2mmise\x1b[0m \x1b[38;5;9m✗\x1b[0m \x1b[34mnode\x1b[0m@24.22.1                   12ms · failed: "
    "error sending request\n"
    "\x1b[2mmise\x1b[0m \x1b[36m\x1b[0m\x1b[31m████\x1b[0m\x1b[2m░░\x1b[0m 1/2 · 3.0s\n"
    "  \x1b[34mgcloud\x1b[0m@590.0.0                 installing  3.0s\n"
    "\x1b[31mmise\x1b[0m \x1b[31mERROR\x1b[0m Failed to install tools: core:node@lts, vfox:jdx/vfox-gcloud@latest\n"
    "\n"
    "core:node@lts: error sending request\n"
    "\n"
    "vfox:jdx/vfox-gcloud@latest: error sending request for url "
    "(https://storage.googleapis.com/cloud-sdk-release/google-cloud-sdk-590.0.0-linux-x86_64.tar.gz)\n"
)
