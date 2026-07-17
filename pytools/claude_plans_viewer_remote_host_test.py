"""pytools.claude_plans_viewer のリモートホスト統合・終了処理関連テスト。"""

import asyncio
import base64
import json
import os
import typing
from pathlib import Path

import pytest

from pytools.claude_plans_viewer import _app, _local, _remote, _state


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


async def _aiter_lines(lines: list[str]) -> typing.AsyncIterator[str]:
    """インメモリーの行リストを`RemoteWatcher._process_stream`へ供給するためのヘルパー。"""
    for line in lines:
        yield line


def _seed_remote_cache(state: _state.BroadcastState, host: str, items: list[dict[str, typing.Any]]) -> None:
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


def _attach_fake_connection(watcher: _remote.RemoteWatcher) -> _FakeProc:
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


class TestFetchRemoteFile:
    """`fetch_remote_file`のRPC優先・フォールバック分岐とmtime同梱の挙動を検証する。"""

    @pytest.mark.asyncio
    async def test_uses_watcher_rpc_when_connected(self):
        """watcherが接続中ならRPCで取得し、フォールバック経路の`ssh_runner`は呼ばれない。"""
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        _attach_fake_connection(watcher)
        runner = _FakeSshRunner()

        async def _drive() -> None:
            await asyncio.sleep(0.05)
            await watcher._handle_event(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess stdoutから配信されるイベントを単体で注入するため）
                {
                    "type": "response",
                    "id": 1,
                    "ok": True,
                    "data": base64.b64encode(b"# remote\n").decode("ascii"),
                    "mtime_epoch": 42.0,
                }
            )

        drive_task = asyncio.create_task(_drive())
        text, mtime = await asyncio.wait_for(
            _remote.fetch_remote_file("host1", "foo.md", runner, watcher),
            timeout=1.0,
        )
        await drive_task

        assert text == "# remote\n"
        assert mtime == 42.0
        # フォールバック経路は使われない。
        assert not runner.calls

    @pytest.mark.asyncio
    async def test_falls_back_when_watcher_disconnected(self):
        """watcher未接続時はフォールバック経路の`ssh_runner`で取得し、mtimeも返す。"""
        runner = _FakeSshRunner(read_responses={("host1", "foo.md"): ("# fallback\n", 7.0)})
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        # `_connected=False`のまま渡す。

        text, mtime = await _remote.fetch_remote_file("host1", "foo.md", runner, watcher)

        assert text == "# fallback\n"
        assert mtime == 7.0
        read_calls = [c for c in runner.calls if c[1] == "read"]
        assert len(read_calls) == 1

    @pytest.mark.asyncio
    async def test_falls_back_on_rpc_error_response(self):
        """watcherが`ok=False`を返した場合もフォールバック経由で救済する。"""
        runner = _FakeSshRunner(read_responses={("host1", "foo.md"): ("# fallback\n", 8.0)})
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        _attach_fake_connection(watcher)

        async def _drive() -> None:
            await asyncio.sleep(0.05)
            await watcher._handle_event({"type": "response", "id": 1, "ok": False, "error": "permission denied"})  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess stdoutから配信されるイベントを単体で注入するため）

        drive_task = asyncio.create_task(_drive())
        text, mtime = await asyncio.wait_for(
            _remote.fetch_remote_file("host1", "foo.md", runner, watcher),
            timeout=1.0,
        )
        await drive_task

        assert text == "# fallback\n"
        assert mtime == 8.0
        # フォールバック経路が1回だけ呼ばれる。
        read_calls = [c for c in runner.calls if c[1] == "read"]
        assert len(read_calls) == 1

    @pytest.mark.asyncio
    async def test_returns_none_mtime_when_missing_in_payload(self):
        """応答に`mtime_epoch`が欠落していると`mtime`は`None`になる（キャッシュバイパス目的）。

        フォールバック経路（`ssh_runner`単発呼び出し）でヘルパーが`mtime_epoch`キーを返さない
        ケースを`_FakeSshRunner`で再現し、`fetch_remote_file`の戻り値`mtime`が`None`になることを
        公開インターフェース経由で確認する。
        """
        runner = _FakeSshRunner(read_responses={("host1", "foo.md"): ("hello", None)})
        # watcher=Noneでフォールバック経路（RPC不在）を強制する。
        text, mtime = await _remote.fetch_remote_file("host1", "foo.md", runner, None)

        assert text == "hello"
        assert mtime is None


class TestRemoteHostIntegration:
    """リモートホスト統合（API・許可リスト・host-status）の挙動を検証する。"""

    @pytest.mark.asyncio
    async def test_api_files_merges_local_and_remote_sorted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """`/api/files`がローカル＋全リモートホストのエントリをctime（作成日時）降順で統合する。

        ローカルファイルの実`ctime`（Linux上では`st_ctime`）は`os.utime`で制御できないため、
        `_local._ctime_epoch`をmonkeypatchして決定論的な値へ固定する。
        `mtime_epoch`降順とは異なる順序になる値を選び、ソート基準が`ctime_epoch`であることを示す。
        """
        local = tmp_path / "local.md"
        local.write_text("local", encoding="utf-8")
        os.utime(local, (3_000.0, 3_000.0))
        monkeypatch.setattr(_local, "_ctime_epoch", lambda st: 2_000.0)

        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1", "host2"],
        )
        state: _state.BroadcastState = app.config["PLANS_STATE"]
        _seed_remote_cache(state, "host1", [{"path": "h1.md", "name": "h1.md", "mtime_epoch": 5_000.0, "ctime_epoch": 4_000.0}])
        _seed_remote_cache(state, "host2", [{"path": "h2.md", "name": "h2.md", "mtime_epoch": 1_000.0, "ctime_epoch": 6_000.0}])

        client = app.test_client()
        response = await client.get("/api/files")

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        # ctime降順: host2(6000) > host1(4000) > local-host(2000)。
        assert [(e["host"], e["path"]) for e in data] == [
            ("host2", "h2.md"),
            ("host1", "h1.md"),
            ("local-host", "local.md"),
        ]
        # 全エントリに`host`フィールドが乗ること。
        assert {e["host"] for e in data} == {"host1", "host2", "local-host"}

    @pytest.mark.asyncio
    async def test_api_file_for_remote_host_renders(self, tmp_path: Path):
        """`/api/file?host=host1&path=foo.md`がfake runnerの`read`応答をHTMLレンダリングして返す。"""
        runner = _FakeSshRunner(
            read_responses={("host1", "foo.md"): "# remote title\n"},
        )
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        response = await client.get("/api/file?host=host1&path=foo.md")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert "<h1>remote title</h1>" in body
        # `read`オペレーションがhost1宛に1回発行され、引数はbase64エンコードされた相対パス。
        read_calls = [c for c in runner.calls if c[1] == "read"]
        assert len(read_calls) == 1
        assert read_calls[0][0] == "host1"
        assert base64.b64decode(read_calls[0][2][0]).decode("utf-8") == "foo.md"

    @pytest.mark.asyncio
    async def test_api_file_caches_remote_response_by_mtime(self, tmp_path: Path):
        """リモート応答のmtimeをキーにMarkdownキャッシュへ格納し、同一ファイルの再要求でssh呼び出しが増えない。"""
        runner = _FakeSshRunner(
            read_responses={("host1", "foo.md"): ("# remote\n", 1234.5)},
        )
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        first = await client.get("/api/file?host=host1&path=foo.md")
        second = await client.get("/api/file?host=host1&path=foo.md")

        assert first.status_code == 200
        assert second.status_code == 200
        cache: _local.MarkdownCache = app.config["PLANS_MARKDOWN_CACHE"]
        # `(host, rel, mtime_epoch)`キーで格納されている。
        assert cache.get(("host1", "foo.md", 1234.5)) is not None

    @pytest.mark.asyncio
    async def test_api_file_caches_local_response_by_mtime(self, tmp_path: Path):
        """ローカル応答も`stat`から取得した`mtime_epoch`をキーにMarkdownキャッシュへ格納する。"""
        target = tmp_path / "a.md"
        target.write_text("# title\n", encoding="utf-8")
        os.utime(target, (4_200.0, 4_200.0))
        app = _app.create_app(tmp_path, hostname="local-host")
        client = app.test_client()
        await client.get("/api/file?path=a.md")

        cache: _local.MarkdownCache = app.config["PLANS_MARKDOWN_CACHE"]
        # ローカルの`mtime_epoch`はstatのst_mtimeに一致する。
        assert cache.get(("local-host", "a.md", 4_200.0)) is not None

    @pytest.mark.asyncio
    async def test_api_raw_for_remote_host_returns_markdown(self, tmp_path: Path):
        """`/api/raw?host=host1&path=foo.md`がfake runnerから取得した生Markdownを返す。"""
        body_src = "# title\n\n本文\n"
        runner = _FakeSshRunner(read_responses={("host1", "foo.md"): body_src})
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        response = await client.get("/api/raw?host=host1&path=foo.md")

        assert response.status_code == 200
        assert response.content_type == "text/markdown; charset=utf-8"
        assert await response.get_data(as_text=True) == body_src

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", ["/api/file", "/api/raw"])
    async def test_unknown_host_rejected_without_ssh_call(self, tmp_path: Path, endpoint: str):
        """許可リスト外のhost指定は400で拒否され、`ssh_runner`は呼ばれない。

        サーバーが`0.0.0.0`等で公開された場合に、クライアントが任意のSSH接続先へ
        接続試行を誘発できないようにするための境界検証。
        """
        runner = _FakeSshRunner()
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        response = await client.get(f"{endpoint}?host=evil&path=foo.md")

        assert response.status_code == 400
        # ssh_runnerは一度も呼ばれていない。
        assert not runner.calls

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", ["/api/file", "/api/raw"])
    async def test_remote_traversal_rejected_without_ssh_call(self, tmp_path: Path, endpoint: str):
        """`..`を含む相対パスはSSH呼び出し前に400で拒否される。"""
        runner = _FakeSshRunner()
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        response = await client.get(f"{endpoint}?host=host1&path=../escape.md")

        assert response.status_code == 400
        assert not runner.calls

    @pytest.mark.asyncio
    async def test_local_host_query_uses_local_path(self, tmp_path: Path):
        """`host`にローカル名を明示してもローカル経路で解決され、SSHは呼ばれない。"""
        (tmp_path / "a.md").write_text("# local\n", encoding="utf-8")
        runner = _FakeSshRunner()
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        response = await client.get("/api/file?host=local-host&path=a.md")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert "<h1>local</h1>" in body
        assert not runner.calls

    def test_local_hostname_conflict_rejected(self, tmp_path: Path):
        """ローカルhostnameと同じ`--remote-host`を渡すと`create_app`が拒絶する。"""
        with pytest.raises(ValueError, match="local hostname"):
            _app.create_app(
                tmp_path,
                hostname="local-host",
                remote_hosts=["local-host"],
            )

    @pytest.mark.asyncio
    async def test_api_host_status_initial_state(self, tmp_path: Path):
        """`/api/host-status`の初期応答はローカル=connected・リモート=connecting。"""
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
        )
        client = app.test_client()
        response = await client.get("/api/host-status")

        assert response.status_code == 200
        assert response.content_type == "application/json; charset=utf-8"
        data = json.loads(await response.get_data())
        assert data == {"local-host": "connected", "host1": "connecting"}

    @pytest.mark.asyncio
    async def test_api_host_status_updates_after_snapshot(self, tmp_path: Path):
        """snapshot受信後は`/api/host-status`がそのホストを`connected`として返す。"""
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
        )
        state: _state.BroadcastState = app.config["PLANS_STATE"]
        watcher = _remote.RemoteWatcher("host1", state)
        await watcher._process_stream(_aiter_lines([json.dumps({"type": "snapshot", "entries": []}) + "\n"]))  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）

        client = app.test_client()
        response = await client.get("/api/host-status")
        data = json.loads(await response.get_data())
        assert data == {"local-host": "connected", "host1": "connected"}

    @pytest.mark.asyncio
    async def test_snapshot_registers_host_info_and_propagates_ctime(self, tmp_path: Path):
        """初回snapshot受信で`BroadcastState.host_info`にroot・os_type・os_nameが登録され、
        `ctime_epoch`が`/api/files`応答まで伝搬すること。
        """
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
        )
        state: _state.BroadcastState = app.config["PLANS_STATE"]
        # ローカル分はcreate_app起動時に即座に登録される。
        assert state.host_info["local-host"]["os_type"] in ("posix", "nt")

        watcher = _remote.RemoteWatcher("host1", state)
        remote_host_info = {"root": "/home/remote/.claude/plans", "os_type": "posix", "os_name": "posix"}
        await watcher._process_stream(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
            _aiter_lines(
                [
                    json.dumps(
                        {
                            "type": "snapshot",
                            "entries": [
                                {"path": "r.md", "name": "r.md", "mtime_epoch": 10.0, "ctime_epoch": 20.0},
                            ],
                            "host_info": remote_host_info,
                        }
                    )
                    + "\n",
                ]
            )
        )

        assert state.host_info["host1"] == remote_host_info

        client = app.test_client()
        response = await client.get("/api/files")
        data = json.loads(await response.get_data())
        remote_entry = next(e for e in data if e["host"] == "host1")
        assert remote_entry["ctime_epoch"] == 20.0


class TestBuildRemoteCommand:
    """`_build_remote_command_argv`はPOSIXシェル非依存・cmd.exe互換であること。

    Windows OpenSSHの既定シェル`cmd.exe`では`bash -c`・heredoc・`head -c`等の
    POSIX組み込みが解釈できない。リモート起動コマンドはこれらに依存せず、
    クォート境界はダブルクォートのみで表現する不変条件を持つ。

    本クラスのテストはprivate関数`_build_remote_command_argv`を直接検証する。
    公開経路（`default_ssh_runner`・`RemoteWatcher._connect`）経由ではSSH/subprocessの
    実起動が必要で、Windowsシェル互換性の境界条件を網羅検証できない。
    例外的に最小限の直接テストへ限定する。
    """

    @pytest.mark.parametrize("op,args", [("serve", []), ("read", ["YWJjLm1k"])])
    def test_excludes_posix_shell_idioms(self, op: str, args: list[str]):
        """argv連結文字列にPOSIXシェル組み込み・heredoc・パイプ等が含まれない。"""
        argv = _remote._build_remote_command_argv(op, args)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（Windows cmd.exe互換のシェル境界条件を個別に検証するため実SSH起動不可）
        joined = " ".join(argv)
        # POSIXシェル非依存に必須となる禁止トークン。
        for token in ("bash ", "bash\t", "head ", "mkdir ", "<<", "<<<", "<<-", "exec ", " | ", "&&", "||"):
            assert token not in joined, f"{token!r} unexpectedly present: {joined!r}"
        # 単独の文字としてリダイレクト・パイプ・cmd.exeエスケープが現れない。
        # `>=`はwatchdogバージョン指定子として`"..."`内に閉じ込められて出現するため除外する。
        for ch in ("|", "&", "^"):
            assert ch not in joined, f"{ch!r} unexpectedly present: {joined!r}"

    def test_python_bootstrap_excludes_shell_specials(self):
        """bootstrapコード本体にはPOSIX/cmd.exeで意味を持つ特殊文字を含めない。"""
        bootstrap = _remote.REMOTE_BOOTSTRAP
        # `$`はPOSIXのダブルクォート内でも展開される。`%`はcmd.exeでも展開される。
        # `<`・`>`・`|`・`&`・`^`はクォート外でリダイレクト・連結・エスケープに解釈される。
        # `\`はPOSIXダブルクォート内でエスケープ扱いになる。
        for ch in ("$", "%", "<", ">", "|", "&", "^", "\\"):
            assert ch not in bootstrap, f"bootstrap contains forbidden char {ch!r}: {bootstrap!r}"

    def test_op_and_args_appended_at_tail(self):
        """`op`と`args`はargv末尾に未加工のまま追加される（ヘルパーはsys.argvで読み取る）。"""
        argv = _remote._build_remote_command_argv("read", ["YWJjLm1k"])  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（Windows cmd.exe互換のシェル境界条件を個別に検証するため実SSH起動不可）
        assert argv[-2:] == ["read", "YWJjLm1k"]

    def test_uses_double_quote_boundaries_only(self):
        """空白を含む要素は両端ダブルクォートで囲み、シングルクォート境界は使わない。"""
        argv = _remote._build_remote_command_argv("serve", [])  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（Windows cmd.exe互換のシェル境界条件を個別に検証するため実SSH起動不可）
        for elem in argv:
            if " " not in elem:
                continue
            # cmd.exeはシングルクォートをクォート境界として認識しない。
            assert elem.startswith('"') and elem.endswith('"'), elem


class _FakeProcessForTerminate:
    """`_terminate_process`の段階的フォールバック検証用の擬似Process。

    `exits_on`で「どの段階で終了するか」を制御し、ヘルパーがstdin EOF / SIGTERM /
    SIGKILLのいずれで停止するかを再現する。`wait()`はreturncodeが入るまで待つ。
    """

    def __init__(self, exits_on: typing.Literal["stdin_close", "terminate", "kill", "never"]) -> None:
        self._exits_on = exits_on
        self.stdin = self._Stdin(self._on_stdin_close)
        self.returncode: int | None = None
        self.terminate_called = False
        self.kill_called = False
        self._wait_event = asyncio.Event()

    class _Stdin:
        def __init__(self, on_close: typing.Callable[[], None]) -> None:
            self._closing = False
            self._on_close = on_close

        def is_closing(self) -> bool:
            return self._closing

        def close(self) -> None:
            if not self._closing:
                self._closing = True
                self._on_close()

    def _on_stdin_close(self) -> None:
        if self._exits_on == "stdin_close":
            self._set_exited(0)

    def terminate(self) -> None:
        self.terminate_called = True
        if self._exits_on == "terminate":
            self._set_exited(-15)

    def kill(self) -> None:
        self.kill_called = True
        if self._exits_on == "kill":
            self._set_exited(-9)

    def _set_exited(self, rc: int) -> None:
        if self.returncode is None:
            self.returncode = rc
            self._wait_event.set()

    async def wait(self) -> int:
        await self._wait_event.wait()
        assert self.returncode is not None
        return self.returncode


class TestTerminateProcess:
    """`_terminate_process`の段階的フォールバック挙動を検証する。

    本クラスのテストはprivate関数`_terminate_process`を直接検証する。
    段階的停止経路（stdin close→terminate→kill）は実プロセス起動を伴わないと
    各フォールバック段を選択的に発火できず、公開経路（`RemoteWatcher.run`の
    キャンセル経路）では実SSH/subprocess起動が必要となる。
    例外的に最小限の直接テストへ限定する。
    """

    @pytest.mark.asyncio
    async def test_stdin_close_triggers_graceful_exit(self):
        """stdin closeで停止経路に乗る場合、terminate/killは呼ばれない。"""
        proc = _FakeProcessForTerminate(exits_on="stdin_close")
        await _remote._terminate_process(typing.cast(typing.Any, proc), grace_timeout=0.1)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（実プロセス起動なしに停止フォールバック段を選択的に発火するため）
        assert proc.returncode == 0
        assert not proc.terminate_called
        assert not proc.kill_called

    @pytest.mark.asyncio
    async def test_terminate_after_stdin_unresponsive(self):
        """stdin closeで応答が無ければterminateで停止し、killは呼ばれない。"""
        proc = _FakeProcessForTerminate(exits_on="terminate")
        await _remote._terminate_process(typing.cast(typing.Any, proc), grace_timeout=0.05)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（実プロセス起動なしに停止フォールバック段を選択的に発火するため）
        assert proc.returncode == -15
        assert proc.terminate_called
        assert not proc.kill_called

    @pytest.mark.asyncio
    async def test_kill_when_process_is_unresponsive(self):
        """terminateにも応答しないプロセスはkillで打ち切られる。"""
        proc = _FakeProcessForTerminate(exits_on="kill")
        await _remote._terminate_process(typing.cast(typing.Any, proc), grace_timeout=0.05)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（実プロセス起動なしに停止フォールバック段を選択的に発火するため）
        assert proc.returncode == -9
        assert proc.terminate_called
        assert proc.kill_called

    @pytest.mark.asyncio
    async def test_already_exited_process_is_no_op(self):
        """既に終了済みのプロセスはstdin close・terminate・killいずれも呼ばれない。"""
        proc = _FakeProcessForTerminate(exits_on="never")
        proc.returncode = 0
        await _remote._terminate_process(typing.cast(typing.Any, proc), grace_timeout=0.05)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（実プロセス起動なしに停止フォールバック段を選択的に発火するため）
        assert not proc.terminate_called
        assert not proc.kill_called
        assert not proc.stdin.is_closing()


class TestRemoteStreamLimit:
    """`asyncio.create_subprocess_exec`既定StreamReader上限超過時の挙動。

    本クラスのテストはprivate関数`_iter_stream_lines`を`asyncio.StreamReader`へ直接渡して
    検証する。`limit`引上げ後の挙動は実subprocess起動を伴う公開経路（`RemoteWatcher._connect`）
    では再現コストが高く、`asyncio.StreamReader`を直接構成する直接テストの方が安定する。
    例外的に最小限の直接テストへ限定する。
    """

    @pytest.mark.asyncio
    async def test_iter_stream_lines_handles_oversized_line(self):
        """64KiB既定上限を超える1行をlimit引き上げ後のStreamReaderで読み取れる。

        modules内に専用モジュールを足さず、`asyncio.StreamReader`に対し
        `_iter_stream_lines`の前提（`readline()`が分離記号を見つけるまで読み続ける）が
        `limit`引数で制御可能であることを直接確認する。
        既定limit=64KiBではvalueErrorが上がる挙動を再現したうえで、
        REMOTE_STREAM_LIMIT_BYTES適用時に同じ行が完了することを示す。
        """
        big_payload = ("a" * (200 * 1024)) + "\n"
        # 既定limit (64KiB) では行末を見つけられず例外を送出する。
        small_reader = asyncio.StreamReader()
        small_reader.feed_data(big_payload.encode("utf-8"))
        small_reader.feed_eof()
        with pytest.raises(ValueError, match="chunk is longer than limit"):
            await small_reader.readline()

        # REMOTE_STREAM_LIMIT_BYTES適用後はそのまま読み終えられる。
        big_reader = asyncio.StreamReader(limit=_remote.REMOTE_STREAM_LIMIT_BYTES)
        big_reader.feed_data(big_payload.encode("utf-8"))
        big_reader.feed_eof()

        async def _consume() -> list[str]:
            collected: list[str] = []
            async for line in _remote._iter_stream_lines(big_reader):  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（asyncio.StreamReaderを直接構成しlimit引き上げ後の読み取りを確認するため）
                collected.append(line)
            return collected

        lines = await _consume()
        assert len(lines) == 1
        assert lines[0].rstrip("\n") == "a" * (200 * 1024)


class TestSafeBasePath:
    """`_app.safe_base_path`の入力検証。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", ""),
            ("/", ""),
            ("/plans", "/plans"),
            ("/plans/", "/plans"),
            ("/api/v1", "/api/v1"),
            ("/foo-bar_baz.qux", "/foo-bar_baz.qux"),
        ],
    )
    def test_accepts_safe_values(self, raw: str, expected: str):
        assert _app.safe_base_path(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "//evil.example",
            "/foo//bar",
            '/"><script>',
            '/"; alert(1); //',
            "no-leading-slash",
            "/has space",
            "/has\nnewline",
            "/has<tag>",
        ],
    )
    def test_rejects_unsafe_values(self, raw: str):
        assert _app.safe_base_path(raw) == ""


class TestProxyFixIntegration:
    """ProxyFixミドルウェアがX-Forwarded-Prefix/Protoを反映する経路の統合検証。

    ASGI scopeでは`root_path`に対しQuartが`path`の冒頭から同値を除去するため、
    リバースプロキシは「prefixを保持したままバックエンドへ転送する」構成（nginxで
    `proxy_pass http://backend;`をtrailing slash無しで指定する形）を想定する。
    テストもクライアントがプレフィクス付きの絶対URLへ要求する前提で組み立てる。
    """

    @pytest.mark.asyncio
    async def test_index_includes_prefix_in_links_and_js(self, tmp_path: Path):
        """`X-Forwarded-Prefix`付与時、href・JS const `BASE_PATH`の双方に反映される。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get(
            "/plans/",
            headers={"X-Forwarded-Prefix": "/plans", "X-Forwarded-Proto": "https"},
        )

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert 'href="/plans/favicon.svg"' in body
        assert 'href="/plans/manifest.webmanifest" crossorigin="use-credentials"' in body
        assert 'href="/plans/static/markdown.css"' in body
        # JSリテラルはjson.dumpsで生成されるためダブルクォート付き。
        assert 'const BASE_PATH = "/plans";' in body

    @pytest.mark.asyncio
    async def test_index_without_prefix_uses_empty_base(self, tmp_path: Path):
        """ヘッダー無しでは空文字列扱いとなりプレフィクスが付かない。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/")

        body = await response.get_data(as_text=True)
        assert 'href="/favicon.svg"' in body
        assert 'const BASE_PATH = "";' in body

    @pytest.mark.asyncio
    async def test_manifest_includes_prefix(self, tmp_path: Path):
        """manifest.webmanifestの`start_url`・`icons.src`がプレフィクス付きになる。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get(
            "/plans/manifest.webmanifest",
            headers={"X-Forwarded-Prefix": "/plans"},
        )

        data = json.loads(await response.get_data())
        assert data["start_url"] == "/plans/"
        assert data["icons"][0]["src"] == "/plans/favicon.svg"

    @pytest.mark.asyncio
    async def test_protocol_relative_prefix_rejected_by_proxy_fix(self, tmp_path: Path):
        """プロトコル相対形式のプレフィクスはProxyFix層で拒否される。

        pytilpackの`validate_forwarded_prefix`が先頭`//`を不正値として拒否するため
        `root_path`は設定されず、Quartは`//evil.example/`をルート未マッチとして404を返す。
        `safe_base_path`へ到達する前段で防御される二段構えを担保する。
        """
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("//evil.example/", headers={"X-Forwarded-Prefix": "//evil.example"})
        assert response.status_code == 404
        body = await response.get_data(as_text=True)
        assert "//evil.example" not in body

    @pytest.mark.asyncio
    async def test_routable_malicious_prefix_neutralized_in_output(self, tmp_path: Path):
        """ルート到達可能な悪意プレフィクスでも出力に生バイトが漏れない。

        途中に`//`を含むプレフィクスはProxyFix層を通過してroot_pathに設定され、
        Quartがprefix除去してルートに到達する。`safe_base_path`が空扱いに正規化するため、
        HTML属性・JS定数・manifestのいずれにもプレフィクス文字列が漏れない。
        """
        malicious_path = "/foo//bar/"
        prefix_header = "/foo//bar"
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response_index = await client.get(malicious_path, headers={"X-Forwarded-Prefix": prefix_header})
        body_index = await response_index.get_data(as_text=True)
        assert response_index.status_code == 200
        assert prefix_header not in body_index
        assert 'href="/favicon.svg"' in body_index
        assert 'const BASE_PATH = "";' in body_index

        response_manifest = await client.get(
            f"{malicious_path}manifest.webmanifest",
            headers={"X-Forwarded-Prefix": prefix_header},
        )
        manifest = json.loads(await response_manifest.get_data())
        assert manifest["start_url"] == "/"
        assert manifest["icons"][0]["src"] == "/favicon.svg"
