"""テスト用の共通ヘルパー。"""

import json
import pathlib
import subprocess
import typing
from collections.abc import Callable

from pytools._internal import claude_common as _claude_common

FakeRunFunc = Callable[..., subprocess.CompletedProcess[str] | None]


class _FakeResult:
    """subprocess.CompletedProcess の軽量な代替。"""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _plugin_list_json(*entries: dict[str, object]) -> str:
    """テスト用の `claude plugin list --json` 出力を組み立てる。"""
    return json.dumps(list(entries), ensure_ascii=False)


def make_fresh_install_fake(calls: list[list[str]]) -> typing.Callable[..., _FakeResult]:
    """未インストール環境からの新規導入を模した `claude` CLI フェイクを返す。

    `plugin list`/`marketplace list`は空リスト、`marketplace add`/`plugin install`は成功、
    それ以外のコマンドは失敗を返す。install_claude_plugins 系テストの新規導入シナリオで共用する。
    """

    def fake_run(cmd: list[str], **_kwargs: object) -> _FakeResult:
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
        if cmd[:3] == ["claude", "plugin", "list"]:
            return _FakeResult(
                returncode=0,
                stdout=_plugin_list_json(
                    {"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "user"},
                    {"id": "sample-plugin@ak110-dotfiles", "version": "1.0.0", "scope": "user"},
                ),
            )
        if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
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
    install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
    assert [
        "claude",
        "plugin",
        "install",
        "agent-toolkit@ak110-dotfiles",
        "--scope=user",
    ] in install_calls
    assert [
        "claude",
        "plugin",
        "install",
        "sample-plugin@ak110-dotfiles",
        "--scope=user",
    ] in install_calls


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
