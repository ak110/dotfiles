"""リモートホスト側ヘルパーの起動bootstrapのテスト。"""

import asyncio
import base64
import contextlib
import hashlib
import http.server
import io
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import typing
import uuid
import zipfile
from collections.abc import Generator

import pytest

from agent_toolkit._atk.serve import plans, sessions
from agent_toolkit._atk.serve import remote as serve_remote
from agent_toolkit._common import process_tree
from agent_toolkit._testing import late_descendant

# 配送時に使うbootstrapと、それが読み込むヘルパー本体の対応。
_HELPERS = (
    (plans.REMOTE_BOOTSTRAP, "atk_serve_plans_remote_helper.py"),
    (sessions.REMOTE_BOOTSTRAP, "atk_serve_sessions_remote_helper.py"),
)
_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parents[3] / "scripts"


@pytest.mark.parametrize(("bootstrap", "helper_name"), _HELPERS)
def test_bootstrap_runs_helper_without_file_global(
    bootstrap: str,
    helper_name: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`python -c`と同じ名前空間でbootstrapを実行し、ヘルパーが一覧を返すことを確認する。"""
    home = tmp_path / "home"
    installed = home / "dotfiles" / "agent-toolkit" / "scripts" / helper_name
    installed.parent.mkdir(parents=True)
    shutil.copy(_SCRIPTS_DIR / helper_name, installed)
    _isolate_environment(home, tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["-c", "list"])
    stdin = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    stdout_buffer = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_buffer, encoding="ascii")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    # `python -c`はモジュール`__main__`のグローバルを渡す。`__file__`は束縛されていない。
    namespace: dict[str, typing.Any] = {"__name__": "__main__"}

    with pytest.raises(SystemExit) as excinfo:
        exec(bootstrap, namespace)  # pylint: disable=exec-used

    assert excinfo.value.code == 0
    stdout.flush()
    json.loads(stdout_buffer.getvalue().decode("utf-8"))


@pytest.mark.parametrize("helper_name", [helper_name for _, helper_name in _HELPERS])
def test_helper_imports_from_checkout_of_bound_file(
    helper_name: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`__file__`を束縛した名前空間では、ヘルパー自身の設置場所のcheckoutを`agent_toolkit`の探索先へ加える。"""
    home = tmp_path / "home"
    home.mkdir()
    installed = tmp_path / "opt" / "dotfiles" / "agent-toolkit" / "scripts" / helper_name
    installed.parent.mkdir(parents=True)
    shutil.copy(_SCRIPTS_DIR / helper_name, installed)
    _isolate_environment(home, tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "path", list(sys.path))
    # `main`を実行させずに関数だけを得るため、`__name__`は`__main__`以外とする。
    namespace: dict[str, typing.Any] = {"__name__": "helper", "__file__": str(installed)}

    exec(compile(installed.read_text(encoding="utf-8"), str(installed), "exec"), namespace)  # pylint: disable=exec-used

    assert sys.path[0] == str(tmp_path / "opt" / "dotfiles" / "agent-toolkit")


@pytest.mark.parametrize("bootstrap", [bootstrap for bootstrap, _ in _HELPERS])
def test_bootstrap_avoids_shell_metacharacters(bootstrap: str) -> None:
    # test-copy-contract: SSHが遠隔シェルへ連結するbootstrapの引用符・展開制御文字を禁止し、POSIX/cmd.exeで引数境界を保つ。
    """bootstrapはPOSIXシェルとcmd.exeの双方で1つのダブルクォート引数として渡せる文字だけで構成する。"""
    assert not set(bootstrap) & set('"$%<>|&^\\')


_INSTALLED_PYTHONS = json.dumps(
    [
        {"implementation": "cpython", "version": "3.15.9"},
        {"implementation": "cpython", "version": "3.15.10"},
        {"implementation": "cpython", "version": "3.15.11a1"},
        {"implementation": "cpython", "version": "3.16.0"},
        {"implementation": "pypy", "version": "3.15.12"},
    ]
).encode()


class _RemoteHost:
    """SSHの接続先を模す。導入済みのPythonの一覧に応じ、ヘルパーの依存を専用キャッシュから、または索引から用意して起動する。

    昇格したSSHのセッションのWindowsでは、マイナー版（`3.15`）の指定はjunctionをたどれずに失敗し、
    導入済みのパッチ版の指定だけが起動する（2026-11-19、sthenoで観測）。
    索引へ出ない起動（`--offline`）は、依存が専用キャッシュにある場合だけ成功する。
    """

    def __init__(self, *installed: str, listing: tuple[int, bytes, bytes] | None = None, cached: bool = False) -> None:
        self.installed = set(installed)
        self.listing = listing
        self.cached = cached
        self.reachable = True
        self.index_reachable = True
        self.helper_fails = False
        self.attempts = 0
        self.listings = 0
        self.index_fetches = 0
        self.helpers: list[str] = []

    def answer(self, command: list[str]) -> tuple[int, bytes, bytes]:
        self.attempts += 1
        if not self.reachable:
            return 255, b"", b"ssh: connect to host remote port 22: Connection refused"
        if command[command.index("uv") + 1] == "python":
            self.listings += 1
            entries = [{"implementation": "cpython", "version": version} for version in self.installed]
            return self.listing or (0, json.dumps(entries).encode(), b"")
        python = command[command.index("--python") + 1]
        if python not in self.installed:
            return 2, b"", f"error: No interpreter found for Python {python}".encode()
        if "--offline" not in command:
            if not self.index_reachable:
                return 2, b"", b"error: Failed to fetch: https://pypi.org/simple/platformdirs/"
            self.index_fetches += 1
            self.cached = True
        elif not self.cached:
            return 1, b"", b"error: watchdog was not found in the cache"
        if command[command.index("-c") + 1] == "pass":
            return 0, b"", b""
        self.helpers.append(python)
        if self.helper_fails:
            return 3, b"", b"helper not found"
        return 0, b"[]", b""


@pytest.fixture(name="add_remote_host")
def _add_remote_host(monkeypatch: pytest.MonkeyPatch) -> typing.Callable[..., tuple[str, _RemoteHost]]:
    """単発と常駐のSSHを模した接続先へ向け、接続先を名前とともに加える関数を返す。

    `atk serve`のプロセスは接続先ごとにPythonの版を覚えるため、試験ごとに新しい名前の接続先を使う。
    """
    hosts: dict[str, _RemoteHost] = {}

    async def fake_run_ssh(cmd: list[str], timeout: float) -> tuple[int, bytes, bytes]:
        del timeout
        return hosts[cmd[1]].answer(cmd)

    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*cmd: str, **kwargs: typing.Any) -> asyncio.subprocess.Process:
        hosts[cmd[1]].answer(list(cmd))
        return await real_exec(sys.executable, "-c", "pass", **kwargs)

    monkeypatch.setattr(serve_remote, "run_ssh", fake_run_ssh)
    monkeypatch.setattr(serve_remote.asyncio, "create_subprocess_exec", fake_exec)

    def add(*installed: str, listing: tuple[int, bytes, bytes] | None = None, cached: bool = False) -> tuple[str, _RemoteHost]:
        name = f"remote-{uuid.uuid4().hex}"
        hosts[name] = _RemoteHost(*installed, listing=listing, cached=cached)
        return name, hosts[name]

    return add


async def _run_one_shot(host: str) -> None:
    assert await serve_remote.run_remote_helper(sessions.REMOTE_BOOTSTRAP, host, "list", [], ssh_options=(), timeout=30) == "[]"


async def _connect_resident(host: str) -> None:
    proc, stderr_task = await serve_remote.start_resident_helper(
        sessions.REMOTE_BOOTSTRAP, host, ssh_options=(), stream_limit=1024, logger=logging.getLogger(__name__), label="試験"
    )
    assert await serve_remote.stop_resident_helper(proc, stderr_task)


_LAUNCHES = pytest.mark.parametrize("launch", [_run_one_shot, _connect_resident], ids=["one-shot", "resident"])
_AddRemoteHost = typing.Callable[..., tuple[str, _RemoteHost]]
_Launch = typing.Callable[[str], typing.Awaitable[None]]


@pytest.mark.asyncio
@_LAUNCHES
@pytest.mark.parametrize(
    ("listing", "expected"),
    [((0, _INSTALLED_PYTHONS, b""), "3.15.10"), ((2, b"", b"uv: command not found"), "3.15")],
    ids=["installed", "listing-failed"],
)
async def test_helper_launch_names_newest_installed_python_patch(
    add_remote_host: _AddRemoteHost, launch: _Launch, listing: tuple[int, bytes, bytes], expected: str
) -> None:
    """ヘルパーは、リモートに導入済みのCPython 3.15の正式版のうち最新のパッチ版を指定して起動する。

    uvは`3.15`の指定を、管理Pythonのマイナー版のjunction（`cpython-3.15-windows-x86_64-none`）を経由して解決する。
    junctionは非昇格のuvが作り、WindowsのSSHのセッションは昇格したtokenで動くため、RedirectionGuardがjunctionを
    たどらせず、`os error 448`で起動できなかった（2026-11-19、sthenoで観測）。パッチ版の指定は実体のディレクトリを使う。
    一覧を得られない場合は`3.15`を指定し、uv自身の探索と診断に任せる。
    """
    name, host = add_remote_host("3.15", "3.15.10", listing=listing)

    await launch(name)

    assert host.helpers == [expected]


@pytest.mark.asyncio
async def test_launches_reuse_python_resolved_for_each_host(add_remote_host: _AddRemoteHost) -> None:
    """接続先ごとに一度選んだPythonの版で、単発と常駐のヘルパーを起動し、要求ごとに版を照会するSSHを増やさない。

    覚えた版が起動できる間は、新しいパッチ版が導入されても覚えた版を使う。
    """
    first_name, first = add_remote_host("3.15.2")
    second_name, second = add_remote_host("3.15.9", "3.15.10")

    for name in (first_name, first_name, second_name, first_name, second_name):
        await _run_one_shot(name)
    first.installed.add("3.15.3")
    await _connect_resident(first_name)
    await _run_one_shot(first_name)

    assert (first.listings, first.helpers) == (1, ["3.15.2"] * 5)
    assert (second.listings, second.helpers) == (1, ["3.15.10"] * 2)


@pytest.mark.asyncio
@_LAUNCHES
async def test_launch_after_warm_up_needs_no_package_index(add_remote_host: _AddRemoteHost, launch: _Launch) -> None:
    """依存を一度専用キャッシュへ取得した後は、パッケージの索引へ出ずに起動し、索引へ到達できなくても起動する。

    uvは`--with`の依存を起動のたびに解決し、索引の応答のキャッシュの期限を過ぎると索引へ問い合わせる。
    依存が専用キャッシュにある接続先は、`atk serve`を起動し直した後の初回も索引へ出ない。
    """
    cold_name, cold = add_remote_host("3.15.2")
    warm_name, warm = add_remote_host("3.15.2", cached=True)

    await launch(cold_name)
    for host in (cold, warm):
        host.index_reachable = False
    for name in (cold_name, cold_name, warm_name, warm_name):
        await launch(name)

    assert (cold.index_fetches, cold.helpers) == (1, ["3.15.2"] * 3)
    assert (warm.index_fetches, warm.helpers) == (0, ["3.15.2"] * 2)


@pytest.mark.asyncio
@_LAUNCHES
async def test_launch_reresolves_when_remembered_python_is_gone(add_remote_host: _AddRemoteHost, launch: _Launch) -> None:
    """覚えた版が接続先から消えていれば、同じ要求の中で版を選び直して起動し、以後は新しい版を使う。"""
    name, host = add_remote_host("3.15.2")
    await launch(name)
    host.installed = {"3.15.3"}

    await launch(name)
    await launch(name)

    assert host.helpers == ["3.15.2", "3.15.3", "3.15.3"]
    assert host.listings == 2


@pytest.mark.asyncio
async def test_one_shot_failure_is_reported_after_at_most_one_reresolution(add_remote_host: _AddRemoteHost) -> None:
    """覚えた版での失敗は版を1回だけ選び直して起動し直し、それでも失敗すれば失敗元の標準エラー出力とともに示す。"""
    name, host = add_remote_host("3.15.2")
    await _run_one_shot(name)
    host.helper_fails = True

    with pytest.raises(serve_remote.RemoteHelperError, match="helper not found"):
        await _run_one_shot(name)
    host.installed = {"3.15.3"}
    with pytest.raises(serve_remote.RemoteHelperError, match="helper not found"):
        await _run_one_shot(name)

    assert host.helpers == ["3.15.2", "3.15.2", "3.15.2", "3.15.3"]
    assert host.listings == 3


@pytest.mark.asyncio
@_LAUNCHES
@pytest.mark.parametrize("seen", [False, True], ids=["first-contact", "remembered"])
async def test_unreachable_host_costs_one_ssh_attempt_per_launch(
    add_remote_host: _AddRemoteHost, launch: _Launch, seen: bool
) -> None:
    """接続先へ接続できない場合は、版の照会や起動し直しを重ねず、1回のSSHの失敗をそのまま示す。

    常駐の接続が途絶えている間、画面の要求は単発のSSHで同じ接続先へ向かうため、接続の待ちを要求ごとに重ねない。
    """
    name, host = add_remote_host("3.15.2")
    if seen:
        await launch(name)
    host.reachable = False
    attempts = host.attempts

    with pytest.raises(serve_remote.RemoteHelperError, match="Connection refused"):
        await launch(name)

    assert host.attempts - attempts == 1


async def _one_shot_output(host: str) -> str:
    return await serve_remote.run_remote_helper(sessions.REMOTE_BOOTSTRAP, host, "list", [], ssh_options=(), timeout=120)


async def _resident_first_line(host: str) -> str:
    proc, stderr_task = await serve_remote.start_resident_helper(
        sessions.REMOTE_BOOTSTRAP, host, ssh_options=(), stream_limit=1024, logger=logging.getLogger(__name__), label="試験"
    )
    try:
        assert proc.stdout is not None
        return (await asyncio.wait_for(proc.stdout.readline(), timeout=120)).decode()
    finally:
        assert await serve_remote.stop_resident_helper(proc, stderr_task)


@pytest.mark.asyncio
@pytest.mark.parametrize("launch", [_one_shot_output, _resident_first_line], ids=["one-shot", "resident"])
async def test_helper_launch_writes_only_its_own_uv_cache_and_starts_offline_once_warm(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    launch: typing.Callable[[str], typing.Coroutine[typing.Any, typing.Any, str]],
) -> None:
    """実際のuvで起動したヘルパーは、専用キャッシュの外へ書かず、接続先が普段使うuvキャッシュを使用中にせず、温まった後は索引なしで起動する。

    `uv run`は子の終了まで、使ったuvキャッシュの共有ロックを持つ。
    接続先が普段使うuvキャッシュでヘルパーが動くと、接続先の`uv cache prune`がロック待ちで保留され続けた
    （2026-11-19、sthenoで観測）。
    Windowsのsshのセッションは昇格したtokenで動くため、接続先が普段使うuvキャッシュとcheckoutの`.pyc`へも書かない。
    uvは`--with`の依存を起動のたびに解決し、索引の応答のキャッシュの期限を過ぎると索引へ問い合わせるため、
    索引は期限0の応答を返し、温めた後に止めて、`atk serve`を起動し直した後と同じ初めての接続先として起動する。
    SSHが接続先のシェルへ渡すのと同じく、起動形の要素を空白で連結して接続先のホームでシェルにより実行する。
    """
    uv = shutil.which("uv")
    assert uv is not None
    home = tmp_path / "home"
    checkout = home / "dotfiles" / "agent-toolkit"
    (checkout / "scripts").mkdir(parents=True)
    signals = tmp_path / "signals"
    signals.mkdir()
    # ヘルパーの代わりに、ヘルパーと同じくcheckoutのモジュールをimportし、解放されるまで動き続けてから一覧を出力する。
    (checkout / "scripts" / "atk_serve_sessions_remote_helper.py").write_text(
        "import os, pathlib, sys, time\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
        "import checkout_module\n"
        "signals = pathlib.Path(os.environ['HELPER_SIGNALS'])\n"
        "(signals / 'started').touch()\n"
        "while not (signals / 'release').exists():\n"
        "    time.sleep(0.05)\n"
        "print('[]', flush=True)\n",
        encoding="utf-8",
    )
    (checkout / "checkout_module.py").write_text("", encoding="utf-8")
    cache = tmp_path / "uv-cache"
    wheels = tmp_path / "wheels"
    _write_dependency_wheels(wheels)
    environment = _remote_host_environment(home, cache, signals)
    # 接続先が普段使うuvキャッシュを作り、整理がロックを取る対象を実在させる。
    seeded = process_tree.run_with_timeout(
        [uv, "run", "--no-project", "--offline", "--python", "3.15", "python", "-c", "pass"],
        cwd=home,
        env=environment,
        capture_output=True,
        timeout=60,
    )
    assert seeded.returncode == 0, seeded.stderr
    assert (cache / ".lock").is_file()
    before = _host_state(home, cache)
    _route_ssh_to_local_shell(monkeypatch, home, environment)

    with _package_index(wheels) as index_url:
        environment["UV_DEFAULT_INDEX"] = index_url
        (signals / "release").touch()
        assert (await launch(f"warm-{uuid.uuid4().hex}")).strip() == "[]"
    (signals / "release").unlink()
    (signals / "started").unlink()
    helper = asyncio.create_task(launch(f"restarted-{uuid.uuid4().hex}"))
    while not (signals / "started").exists():
        if helper.done():
            pytest.fail(f"索引を止めた後のヘルパーが起動しなかった: {helper.result()!r}")
        await asyncio.sleep(0.05)
    pruned = process_tree.run_with_timeout(
        [uv, "cache", "prune"],
        cwd=home,
        env={**environment, "UV_LOCK_TIMEOUT": "5"},
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )
    (signals / "release").touch()
    output = await helper

    written = _host_state(home, cache) - before
    dedicated = "home/.cache/agent-toolkit/remote-helper-uv"
    inside = {path for path in written if path == dedicated or path.startswith(f"{dedicated}/")}
    assert output.strip() == "[]"
    assert pruned.returncode == 0, pruned.stderr
    # 専用キャッシュを作るときにできる親のディレクトリは、専用キャッシュの一部として扱う。
    assert {path for path in written - inside if not dedicated.startswith(f"{path}/")} == set()
    assert inside


def _route_ssh_to_local_shell(monkeypatch: pytest.MonkeyPatch, home: pathlib.Path, environment: dict[str, str]) -> None:
    """単発と常駐のSSHを、接続先のホームで起動形の要素を空白で連結したコマンドをシェルで実行する形へ置き換える。

    `ssh_options`を空にした呼び出しの`["ssh", host, ...]`の3要素目以降が、接続先のシェルへ渡る起動形である。
    """

    async def shell(command: typing.Sequence[str], **kwargs: typing.Any) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_shell(" ".join(command[2:]), cwd=home, env=environment, **kwargs)

    async def fake_run_ssh(cmd: list[str], timeout: float) -> tuple[int, bytes, bytes]:
        proc = await shell(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        assert proc.returncode is not None
        return proc.returncode, stdout, stderr

    async def fake_exec(*cmd: str, **kwargs: typing.Any) -> asyncio.subprocess.Process:
        return await shell(cmd, **kwargs)

    monkeypatch.setattr(serve_remote, "run_ssh", fake_run_ssh)
    monkeypatch.setattr(serve_remote.asyncio, "create_subprocess_exec", fake_exec)


@contextlib.contextmanager
def _package_index(wheels: pathlib.Path) -> Generator[str]:
    """`wheels`の配布物を、応答のキャッシュの期限を0とする簡易索引（PEP 691）としてloopbackで配り、索引のURLを返す。

    配布物の公開日時は、uvの設定の`exclude-newer`で除外されないよう過去の日時とする。
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:  # noqa: N802  # uvは取得の前に範囲指定の可否をHEADで確かめる
            self._respond(head=True)

        def do_GET(self) -> None:  # noqa: N802
            self._respond(head=False)

        def _respond(self, *, head: bool) -> None:
            kind, _, name = self.path.strip("/").partition("/")
            if kind == "simple":
                files = [
                    {
                        "filename": wheel.name,
                        "url": f"/files/{wheel.name}",
                        "hashes": {"sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()},
                        "upload-time": "2024-01-01T00:00:00Z",
                    }
                    for wheel in wheels.glob(f"{name.strip('/')}-*.whl")
                ]
                body = json.dumps({"meta": {"api-version": "1.1"}, "name": name.strip("/"), "files": files}).encode()
                content_type = "application/vnd.pypi.simple.v1+json"
            elif kind == "files" and (wheels / name).is_file():
                body, content_type = (wheels / name).read_bytes(), "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=0, public")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def log_message(self, format: str, *args: typing.Any) -> None:  # noqa: A002  # pylint: disable=redefined-builtin
            del format, args

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/simple"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _remote_host_environment(home: pathlib.Path, cache: pathlib.Path, signals: pathlib.Path) -> dict[str, str]:
    """接続先ホームを模した環境を返す。`cache`を接続先が普段使うuvキャッシュとし、依存は試験の索引だけから取得させる。"""
    environment = dict(os.environ)
    # `.pyc`の書込、キャッシュ、索引とプロキシの指定は、接続先の通常の環境に無いため外す。
    for name in (
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONPYCACHEPREFIX",
        "UV_NO_CACHE",
        "UV_LOCK_TIMEOUT",
        "UV_OFFLINE",
        "UV_FIND_LINKS",
        "UV_INDEX",
        "UV_INDEX_URL",
        "UV_EXTRA_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        environment.pop(name, None)
    # ホームを差し替えるとuvが管理Pythonを探せないため、試験のPython本体をPATHから見つけさせる。
    python_directory = pathlib.Path(sys.base_prefix) / "bin" if os.name == "posix" else pathlib.Path(sys.base_prefix)
    environment.update(
        HOME=str(home),
        USERPROFILE=str(home),
        PATH=str(python_directory) + os.pathsep + environment.get("PATH", ""),
        UV_CACHE_DIR=str(cache),
        UV_HTTP_RETRIES="0",
        UV_PYTHON_DOWNLOADS="never",
        HELPER_SIGNALS=str(signals),
    )
    return environment


def _write_dependency_wheels(directory: pathlib.Path) -> None:
    """ヘルパーの起動形が要求する2依存を満たす、モジュールを含まないwheelを置く。"""
    directory.mkdir()
    for name, version in (("watchdog", "6.0.0"), ("platformdirs", "4.0.0")):
        info = f"{name}-{version}.dist-info"
        files = {
            f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
            f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: remote_test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        }
        record = "".join(f"{path},sha256={_record_digest(text)},{len(text.encode())}\n" for path, text in files.items())
        files[f"{info}/RECORD"] = record + f"{info}/RECORD,,\n"
        with zipfile.ZipFile(directory / f"{name}-{version}-py3-none-any.whl", "w") as archive:
            for path, text in files.items():
                archive.writestr(path, text)


def _record_digest(text: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode("ascii")


def _host_state(home: pathlib.Path, cache: pathlib.Path) -> set[str]:
    """接続先のホームとuvキャッシュに在る項目を返す。"""
    return {f"home/{path.relative_to(home).as_posix()}" for path in home.rglob("*")} | {
        f"uv-cache/{path.relative_to(cache).as_posix()}" for path in cache.rglob("*")
    }


def _isolate_environment(home: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ホームディレクトリ・キャッシュ・PATHを一時ディレクトリへ隔離する。"""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    # PATHを空にして`atk`を解決させない（対象ホストの実際の設定を読ませないため）。
    monkeypatch.setenv("PATH", "")


async def _wait_for_pid(pid_file: pathlib.Path) -> int:
    for _ in range(500):
        if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip():
            return int(pid_file.read_text(encoding="utf-8"))
        await asyncio.sleep(0.01)
    raise AssertionError("子プロセスが起動しなかった")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_run_ssh_terminates_child_when_cancelled(tmp_path: pathlib.Path) -> None:
    """単発SSHの呼び出し元をキャンセルすると、子プロセスを終了させてから`CancelledError`を送出する。

    子プロセスが残ると、停止処理がその終了（最長で単発SSHの時間上限）を待たされる。
    """
    pid_file = tmp_path / "pid"
    script = f"import os,pathlib; pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); " + late_descendant.held_source(
        tmp_path / "ssh.release"
    )
    task = asyncio.create_task(serve_remote.run_ssh([sys.executable, "-c", script], timeout=30))
    pid = await _wait_for_pid(pid_file)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)

    assert not _process_exists(pid)


@pytest.mark.asyncio
async def test_run_ssh_terminates_child_on_timeout(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """時間上限に達した単発SSHは子プロセスを終了させ、`subprocess.TimeoutExpired`を送出する。

    時間上限の計測は子プロセスの生成後に始まる。子プロセスがPIDを書く前に上限へ達すると、
    PIDを得られず終了を確かめられないため、PIDの書込を見届けてから生成を完了させる。
    """
    pid_file = tmp_path / "pid"
    script = f"import os,pathlib; pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); " + late_descendant.held_source(
        tmp_path / "ssh.release"
    )
    create_subprocess_exec = asyncio.create_subprocess_exec
    pids: list[int] = []

    async def create_after_pid_written(*args: typing.Any, **kwargs: typing.Any) -> asyncio.subprocess.Process:
        proc = await create_subprocess_exec(*args, **kwargs)
        try:
            pids.append(await _wait_for_pid(pid_file))
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        return proc

    monkeypatch.setattr(serve_remote.asyncio, "create_subprocess_exec", create_after_pid_written)

    with pytest.raises(subprocess.TimeoutExpired):
        await serve_remote.run_ssh([sys.executable, "-c", script], timeout=0.5)

    assert pids
    assert not _process_exists(pids[0])


@pytest.mark.asyncio
async def test_stop_resident_helper_reports_unconfirmed_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """最終killの後も終了を確認できなければ偽を返し、呼び出し側が再接続を止められるようにする。"""

    class Stuck:
        returncode = None
        stdin = None
        pid = 4321

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    async def never(_proc: object, _timeout: float) -> bool:
        return False

    monkeypatch.setattr(serve_remote, "_wait_with_timeout", never)
    stuck = typing.cast("asyncio.subprocess.Process", Stuck())
    assert await serve_remote.stop_resident_helper(stuck, None, grace_timeout=0) is False
    assert await serve_remote.stop_resident_helper(None, None) is True
