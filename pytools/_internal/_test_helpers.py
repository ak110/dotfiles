"""テスト用の共通ヘルパー。"""

import json
import pathlib
import subprocess
import typing
from collections.abc import Callable
from pathlib import Path

from pytools._internal import claude_common as _claude_common
from pytools._internal.update_claude_settings import update_claude_settings

FakeRunFunc = Callable[..., subprocess.CompletedProcess[str] | None]


class _FakeResult:
    """subprocess.CompletedProcess の軽量な代替。"""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def command_matches(cmd: list[str], expected: list[str]) -> bool:
    """実行ファイルの絶対パスを許容して、コマンド列の先頭部分を比較する。"""
    if len(cmd) < len(expected) or not cmd or not expected:
        return False
    return Path(cmd[0]).stem == expected[0] and cmd[1 : len(expected)] == expected[1:]


def _plugin_list_json(*entries: dict[str, object]) -> str:
    """テスト用の `claude plugin list --json` 出力を組み立てる。"""
    return json.dumps(list(entries), ensure_ascii=False)


def make_fresh_install_fake(calls: list[list[str]], *, version: str = "") -> typing.Callable[..., _FakeResult]:
    """未インストール環境からの新規導入を模した `claude` CLI フェイクを返す。

    `plugin list`は成功した`plugin install`を後続呼び出しへ反映し、`marketplace list`は空リスト、
    `marketplace add`/`plugin install`は成功する。それ以外のコマンドは失敗を返す。
    `version`は導入後の`plugin list`が示す版で、導入後の版の検証まで通すテストでは目標の版を渡す。
    install_claude_plugins 系テストの新規導入シナリオで共用する。
    """
    installed_plugin_ids: set[str] = set()

    def fake_run(cmd: list[str], **_kwargs: object) -> _FakeResult:
        calls.append(cmd)
        if command_matches(cmd, ["claude", "plugin", "list"]):
            entries: list[dict[str, object]] = [
                {"id": plugin_id, "scope": "user", "version": version} for plugin_id in sorted(installed_plugin_ids)
            ]
            return _FakeResult(returncode=0, stdout=_plugin_list_json(*entries))
        if command_matches(cmd, ["claude", "plugin", "marketplace", "list"]):
            return _FakeResult(returncode=0, stdout="[]")
        if command_matches(cmd, ["claude", "plugin", "marketplace", "add"]):
            return _FakeResult(returncode=0)
        if command_matches(cmd, ["claude", "plugin", "install"]):
            installed_plugin_ids.add(cmd[3])
            return _FakeResult(returncode=0)
        return _FakeResult(returncode=1)

    return fake_run


def make_installed_two_plugin_fake(
    calls: list[list[str]],
    extra: typing.Callable[[list[str]], _FakeResult | None] | None = None,
    *,
    default_returncode: int = 0,
    default_stderr: str = "",
) -> typing.Callable[..., _FakeResult]:
    """agent-toolkit / sample-plugin が scope=user で導入済みの `claude` CLI フェイクを返す。

    `plugin list`/`marketplace list`の共通応答を担い、それ以外のコマンドは`extra`へ委譲する。
    `extra`が`None`または`None`を返した場合は`default_returncode`/`default_stderr`の`_FakeResult`で応答する。
    """

    def fake_run(cmd: list[str], **_kwargs: object) -> _FakeResult:
        calls.append(cmd)
        if command_matches(cmd, ["claude", "plugin", "list"]):
            return _FakeResult(
                returncode=0,
                stdout=_plugin_list_json(
                    {"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "user"},
                    {"id": "sample-plugin@ak110-dotfiles", "version": "1.0.0", "scope": "user"},
                ),
            )
        if command_matches(cmd, ["claude", "plugin", "marketplace", "list"]):
            return _FakeResult(
                returncode=0,
                stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
            )
        if extra is not None:
            result = extra(cmd)
            if result is not None:
                return result
        return _FakeResult(returncode=default_returncode, stderr=default_stderr)

    return fake_run


def assert_scope_user_install_calls(calls: list[list[str]]) -> None:
    """agent-toolkit / sample-plugin の両方が `--scope=user` で install されたことを検証する。"""
    assert any(
        command_matches(
            command,
            ["claude", "plugin", "install", "agent-toolkit@ak110-dotfiles", "--scope=user"],
        )
        for command in calls
    )
    assert any(
        command_matches(
            command,
            ["claude", "plugin", "install", "sample-plugin@ak110-dotfiles", "--scope=user"],
        )
        for command in calls
    )


def write_known_entry(path: pathlib.Path, entry: dict[str, object]) -> None:
    """known_marketplaces.json に対象 marketplace のエントリを保存する。"""
    path.write_text(
        json.dumps({_claude_common.MARKETPLACE_NAME: entry}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_settings_entry(path: pathlib.Path, entry: dict[str, object]) -> None:
    """settings.json.extraKnownMarketplaces に対象 marketplace のエントリを保存する。"""
    path.write_text(
        json.dumps({"extraKnownMarketplaces": {_claude_common.MARKETPLACE_NAME: entry}}, ensure_ascii=False),
        encoding="utf-8",
    )


def ok_result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    """成功応答の `subprocess.CompletedProcess` を組み立てる。"""
    return subprocess.CompletedProcess([], returncode=returncode, stdout=stdout, stderr="")


def make_static_fake(
    calls: list[list[str]],
    response: subprocess.CompletedProcess[str] | None = None,
) -> FakeRunFunc:
    """全呼び出しに同じレスポンスを返す run_subprocess の差し替え関数を返す。"""
    fixed = response if response is not None else ok_result()

    def fake(
        cmd: list[str],
        *,
        timeout: float | None = None,
        cwd: pathlib.Path | None = None,
        tag: str | None = None,
        **kwargs: typing.Any,
    ) -> subprocess.CompletedProcess[str] | None:
        del timeout, cwd, tag, kwargs
        calls.append(list(cmd))
        return fixed

    return fake


def make_branching_fake(
    calls: list[list[str]],
    create_result: subprocess.CompletedProcess[str],
    read_result: subprocess.CompletedProcess[str],
) -> FakeRunFunc:
    """Script 内容に応じて Save()（生成）と TargetPath 読み取りを切り替える差し替え関数を返す。"""

    def fake(
        cmd: list[str],
        *,
        timeout: float | None = None,
        cwd: pathlib.Path | None = None,
        tag: str | None = None,
        **kwargs: typing.Any,
    ) -> subprocess.CompletedProcess[str] | None:
        del timeout, cwd, tag, kwargs
        calls.append(list(cmd))
        script = " ".join(cmd)
        return create_result if "Save()" in script else read_result

    return fake


def run_update_claude_settings(tmp_path: Path, managed: dict, existing: dict | None = None) -> dict:
    """update_claude_settings でマージしてターゲット結果を返す（テストヘルパー）。

    削除対象引数を空タプルで明示し、実装定数からのテスト隔離を行う。
    """
    managed_path = tmp_path / "managed.json"
    managed_path.write_text(json.dumps(managed, ensure_ascii=False), encoding="utf-8")
    target_path = tmp_path / "target.json"
    if existing is not None:
        target_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    update_claude_settings(
        managed_path,
        target_path,
        removed_hook_substrings=(),
        removed_env_keys=(),
        removed_list_item_substrings=(),
    )
    return json.loads(target_path.read_text(encoding="utf-8"))


class FakeEnvironmentRegistry:
    """`winutils`のレジストリ読み書きを置き換える、ユーザー側とシステム側の環境変数の記憶域。

    `install`で`winutils`の関数を差し替え、`writes`で書き込みの記録を観測する。
    """

    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(self, *, user_path: str = "", system_path: str = "", user_path_type: int = REG_EXPAND_SZ) -> None:
        self.user: dict[str, tuple[str, int]] = {"Path": (user_path, user_path_type)} if user_path else {}
        self.system: dict[str, tuple[str, int]] = {"Path": (system_path, self.REG_EXPAND_SZ)}
        self.writes: list[tuple[str, str, int]] = []

    def install(self, monkeypatch: typing.Any) -> None:
        """`winutils`のレジストリ関数をこの記憶域へ向ける。"""
        from pytools._internal import winutils  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        monkeypatch.setattr(winutils, "import_winreg", lambda: self)
        monkeypatch.setattr(winutils, "read_user_env_var", self.read_user)
        monkeypatch.setattr(winutils, "read_system_env_var", self.read_system)
        monkeypatch.setattr(winutils, "write_user_env_var", self.write_user)
        monkeypatch.setattr(winutils, "broadcast_environment_change", lambda: None)

    def read_user(self, name: str) -> tuple[str | None, int]:
        """ユーザー側の値と値型を返す。"""
        return self.user.get(name, (None, self.REG_SZ))

    def read_system(self, name: str) -> tuple[str | None, int]:
        """システム側の値と値型を返す。"""
        return self.system.get(name, (None, self.REG_SZ))

    def write_user(self, name: str, value: str, reg_type: int) -> None:
        """ユーザー側へ書き込み、記録する。"""
        self.user[name] = (value, reg_type)
        self.writes.append((name, value, reg_type))

    def user_path(self) -> str:
        """ユーザー側の`Path`を返す。"""
        return self.user.get("Path", ("", self.REG_SZ))[0]
