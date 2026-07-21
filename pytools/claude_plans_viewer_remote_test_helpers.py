"""pytools.claude_plans_viewer のリモート関連テストが共有するフェイク・ヘルパー。

`claude_plans_viewer_remote_host_test.py`と`claude_plans_viewer_remote_watcher_test.py`の
双方が同一のSSH実行器フェイク・stdin/proc フェイク・状態注入ヘルパーを必要とするため集約する。
本モジュール自体はテスト専用であり、`pytools.claude_plans_viewer`配布パッケージには含めない。
"""

import base64
import json
import typing

from pytools.claude_plans_viewer import _remote, _state


class _FakeSshRunner:
    """テスト用の擬似SSH実行器。`read`オペレーションのレスポンスを辞書で注入できる。

    `(host, rel_path)` → `body`または`(body, mtime)`のマッピングを受け取り、
    呼び出し結果をJSONとして返す。`failing_hosts`に含まれるhostは`RuntimeError`を送出する。
    """

    def __init__(
        self,
        *,
        read_responses: dict[tuple[str, str], str | tuple[str, float | None]] | None = None,
        failing_hosts: set[str] | None = None,
    ) -> None:
        self._read_responses = read_responses or {}
        self._failing_hosts = failing_hosts or set()
        self.calls: list[tuple[str, str, list[str]]] = []

    async def __call__(self, host: str, op: str, args: list[str]) -> str:
        self.calls.append((host, op, list(args)))
        if host in self._failing_hosts:
            raise RuntimeError(f"ssh failed for {host}")
        if op == "read":
            rel = base64.b64decode(args[0]).decode("utf-8")
            entry = self._read_responses[(host, rel)]
            if isinstance(entry, tuple):
                body, mtime = entry
            else:
                body, mtime = entry, 1_000.0
            payload: dict[str, typing.Any] = {
                "data": base64.b64encode(body.encode("utf-8")).decode("ascii"),
            }
            # `mtime_epoch`が`None`のときはキー自体を含めず、ヘルパー側が`mtime_epoch`を
            # 付与せずに応答するケースを再現する（`fetch_remote_file`はキャッシュをバイパスする）。
            if mtime is not None:
                payload["mtime_epoch"] = mtime
            return json.dumps(payload, ensure_ascii=False)
        raise ValueError(f"unknown op: {op}")


async def aiter_lines(lines: list[str]) -> typing.AsyncIterator[str]:
    """インメモリーの行リストを`RemoteWatcher._process_stream`へ供給するためのヘルパー。"""
    for line in lines:
        yield line


def seed_remote_cache(state: _state.BroadcastState, host: str, items: list[dict[str, typing.Any]]) -> None:
    """テスト用に`state.remote_files`へ直接エントリを書き込む。

    `RemoteWatcher._process_stream`を経由せずに`/api/files`merge挙動を検証するための土台。
    """
    state.remote_files[host] = [_state.make_file_entry(host, item) for item in items]


class _FakeStdin:
    """テスト用の擬似`StreamWriter`。`write`/`drain`/`is_closing`のみ実装する。

    `RemoteWatcher.request`が呼び出すstdin APIを最小限満たす。
    送出されたバイト列は`buffer`に蓄積し、テストから解析できる。
    """

    def __init__(self) -> None:
        self.buffer: list[bytes] = []
        self._closing = False

    def write(self, data: bytes) -> None:
        self.buffer.append(data)

    async def drain(self) -> None:
        return

    def is_closing(self) -> bool:
        return self._closing

    def mark_closing(self) -> None:
        self._closing = True


class _FakeProc:
    """テスト用の擬似`asyncio.subprocess.Process`。stdinのみ提供する。"""

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stdout = None
        self.stderr = None
        self.returncode = None


def attach_fake_connection(watcher: "_remote.RemoteWatcher") -> _FakeProc:
    """`RemoteWatcher`を擬似的に接続済みにする。

    `_connect`を経由せずに`_proc`/`_connected`を直接設定し、
    RPCテストを最小限の依存で記述できるようにする。
    SSH/subprocess起動を伴う公開経路（`run()`）では単体テスト内で接続状態を注入できないため、
    引数注入では到達不能なグローバル状態として直接設定する。
    """
    proc = _FakeProc()
    watcher._proc = typing.cast(typing.Any, proc)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH接続状態の直接注入）
    watcher._connected = True  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH接続状態の直接注入）
    return proc
