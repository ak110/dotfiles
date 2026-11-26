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
import time
import typing
import uuid
import zipfile
from collections.abc import Generator

import pytest

from agent_toolkit._atk.serve import plans, sessions
from agent_toolkit._atk.serve import remote as serve_remote
from agent_toolkit._common import process_tree
from agent_toolkit._testing import late_descendant

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
    home = tmp_path / "home"
    home.mkdir()
    installed = tmp_path / "opt" / "dotfiles" / "agent-toolkit" / "scripts" / helper_name
    installed.parent.mkdir(parents=True)
    shutil.copy(_SCRIPTS_DIR / helper_name, installed)
    _isolate_environment(home, tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "path", list(sys.path))

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
    uvが起動するPythonの代わりに、起動形の`python -B`以降を試験のPythonで接続先のホームから実行する。
    ホームのセッション画面のヘルパーは一覧を返し、記録の`read`では記録が消えた場合と同じく`FileNotFoundError`を示して終わる。
    示す誤りは1行とし、警告本文が末尾だけを残して切り詰めても、ヘルパーが開始の印として書く行が本文に残るかを観測できる形にする。
    `trace`は、接続先のシェルが`sh -x`と同じく実行するコマンドを標準エラー出力へ書く場合を模す。
    `newline`は、接続先が標準エラー出力に書く改行で、WindowsのPythonはCRLFで書く。
    """

    def __init__(
        self, home: pathlib.Path, *installed: str, listing: tuple[int, bytes, bytes] | None = None, cached: bool = False
    ) -> None:
        self.home = home
        self.installed = set(installed)
        self.listing = listing
        self.cached = cached
        self.reachable = True
        self.index_reachable = True
        self.trace = False
        self.newline = b"\n"
        self.attempts = 0
        self.listings = 0
        self.index_fetches = 0
        self.helpers: list[str] = []
        helper = home / "dotfiles" / "agent-toolkit" / "scripts" / "atk_serve_sessions_remote_helper.py"
        helper.parent.mkdir(parents=True)
        helper.write_text(
            "import sys\n"
            "def main():\n"
            "    if sys.argv[1] == 'read':\n"
            "        sys.exit('FileNotFoundError: ' + sys.argv[2])\n"
            "    sys.stdout.write('[]')\n"
            "    return 0\n"
            "if __name__ == '__main__':\n"
            "    sys.exit(main())\n",
            encoding="utf-8",
        )

    def answer(self, command: list[str]) -> tuple[int, bytes, bytes]:
        self.attempts += 1
        if not self.reachable:
            return 255, b"", b"ssh: connect to host remote port 22: Connection refused"

        returncode, stdout, stderr = self._run(command[2:])
        trace = f"+ {' '.join(command[2:])}\n".encode() if self.trace else b""
        return returncode, stdout, (trace + stderr).replace(b"\n", self.newline)

    def _run(self, command: list[str]) -> tuple[int, bytes, bytes]:
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
        option, code, *args = command[command.index("-B") + 1 :]
        if code != "pass":
            self.helpers.append(python)
        ran = process_tree.run_with_timeout(
            [sys.executable, "-B", option, code.strip('"'), *args],
            cwd=self.home,
            env={**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home)},
            capture_output=True,
            timeout=30,
        )
        return ran.returncode, ran.stdout, ran.stderr


@pytest.fixture(name="add_remote_host")
def _add_remote_host(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> typing.Callable[..., tuple[str, _RemoteHost]]:
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
        hosts[name] = _RemoteHost(tmp_path / name, *installed, listing=listing, cached=cached)
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
@pytest.mark.parametrize(
    ("index_reachable", "cached", "index_fetches"),
    [(False, True, 0), (True, False, 1)],
    ids=["index-unreachable", "cache-emptied"],
)
async def test_launch_reresolves_when_remembered_python_is_gone(
    add_remote_host: _AddRemoteHost, launch: _Launch, index_reachable: bool, cached: bool, index_fetches: int
) -> None:
    name, host = add_remote_host("3.15.2")
    await launch(name)
    host.installed = {"3.15.3"}
    host.index_reachable = index_reachable
    host.cached = cached
    fetched = host.index_fetches

    await launch(name)
    await launch(name)

    assert host.helpers == ["3.15.2", "3.15.3", "3.15.3"]
    assert host.listings == 2
    assert host.index_fetches - fetched == index_fetches


@pytest.mark.asyncio
async def test_launch_failure_after_reresolution_reports_the_last_failure(add_remote_host: _AddRemoteHost) -> None:
    """選び直した版でも起動できなければ、版の照会を1回、起動を選び直した版での2回までに留め、最後の起動の失敗を示す。"""
    name, host = add_remote_host("3.15.2")
    await _run_one_shot(name)
    host.installed = set()
    attempts, listings = host.attempts, host.listings

    with pytest.raises(serve_remote.RemoteHelperError, match=r"No interpreter found for Python 3\.15$"):
        await _run_one_shot(name)

    assert host.listings - listings == 1
    assert host.attempts - attempts <= 4


@pytest.mark.asyncio
async def test_launch_does_not_repeat_a_failed_attempt(add_remote_host: _AddRemoteHost) -> None:
    """選び直した版が覚えた版と同じなら、失敗した索引へ出ない起動を繰り返さず、索引から依存を取得して起動する。

    専用キャッシュだけが消えた接続先で、同じ起動を繰り返すとSSHの往復だけが増える。
    """
    name, host = add_remote_host("3.15.2")
    await _run_one_shot(name)
    host.cached = False
    attempts = host.attempts

    await _run_one_shot(name)

    # 覚えた版で索引へ出ない起動の失敗、版の照会、索引を使う起動。
    assert (host.attempts - attempts, host.helpers) == (3, ["3.15.2", "3.15.2"])


@pytest.mark.asyncio
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("cached", [True, False], ids=["remembered-and-warm", "first-contact"])
async def test_helper_operation_error_is_reported_without_relaunch(
    add_remote_host: _AddRemoteHost, cached: bool, newline: bytes
) -> None:
    """起動した後のヘルパーの操作のエラーは、版を選び直したり起動し直したりせず、ヘルパーの標準エラー出力のまま示す。

    一覧の表示の後に消えた記録の`read`で、ヘルパーの`FileNotFoundError`を起動の失敗として扱って索引を使う形で起動し直し、
    索引へ届かない接続先では`Failed to fetch`だけが返って本当の原因が消えた（2026-11-19の検証で観測）。
    ヘルパーが開始の印として書く行は、改行がCRLFでも印と判定し、エンドユーザーへ示す誤りの文には含めない。
    """
    name, host = add_remote_host("3.15.2", cached=cached)
    host.newline = newline
    if cached:
        await _run_one_shot(name)
        host.index_reachable = False
    attempts, listings = host.attempts, host.listings

    with pytest.raises(serve_remote.RemoteHelperError) as error:
        await serve_remote.run_remote_helper(
            sessions.REMOTE_BOOTSTRAP, name, "read", ["gone.jsonl"], ssh_options=(), timeout=30
        )

    assert str(error.value).endswith("FileNotFoundError: gone.jsonl")
    assert "atk-remote-helper-started" not in str(error.value)
    assert host.helpers[-1:] == ["3.15.2"]
    # 温まった接続先は覚えた版での起動だけ、初めての接続先は照会と、索引へ出ない起動の失敗と、索引を使う起動。
    assert (host.attempts - attempts, host.listings - listings) == ((1, 0) if cached else (3, 1))


@pytest.mark.asyncio
async def test_one_shot_does_not_accept_another_requests_result(monkeypatch: pytest.MonkeyPatch) -> None:
    request = "a" * 32
    monkeypatch.setattr(serve_remote.secrets, "token_hex", lambda _size: request)
    calls: list[list[str]] = []

    async def run_ssh(command: list[str], timeout: float) -> tuple[int, bytes, bytes]:
        del timeout
        if "list" in command and "run" not in command:
            return 0, _INSTALLED_PYTHONS, b""
        calls.append(command)
        return 1, b"[]", (f"atk-remote-helper-started {request}\natk-remote-helper-finished {'b' * 32} 0\n").encode()

    monkeypatch.setattr(serve_remote, "run_ssh", run_ssh)
    with pytest.raises(serve_remote.RemoteHelperError, match="result unknown"):
        await _run_one_shot("fixture-wrong-request")
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("remembered", [False, True], ids=["first-contact", "remembered-python-gone"])
async def test_command_trace_of_remote_shell_is_not_taken_for_helper_start(
    add_remote_host: _AddRemoteHost, remembered: bool
) -> None:
    """接続先のシェルがコマンドのトレースを標準エラー出力へ書いても、uvが起動できなかった試行から次の試行へ進む。

    トレースにはコマンドの文字列の中の開始の印も現れる。印を標準エラー出力の部分文字列で探すと、ヘルパーが動いたと誤って判定し、
    専用キャッシュが空の初めての起動も、消えた版の選び直しも失敗し続けた（2026-11-19の検証で、`/bin/sh -x`で観測）。
    """
    name, host = add_remote_host("3.15.2")
    if remembered:
        await _run_one_shot(name)
        host.installed = {"3.15.3"}
    host.trace = True

    await _run_one_shot(name)

    assert host.helpers[-1:] == ["3.15.3" if remembered else "3.15.2"]


_UNRESPONSIVE = "import time; time.sleep(120)"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolve_timeout", "remote_code", "ssh_runs"),
    [
        pytest.param(10.0, _UNRESPONSIVE, 1, id="deadline-in-listing"),
        pytest.param(0.3, _UNRESPONSIVE, 2, id="deadline-in-launch"),
        pytest.param(
            0.5,
            f"import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); {_UNRESPONSIVE}",
            1,
            id="deadline-while-ending-listing",
            marks=pytest.mark.skipif(os.name != "posix", reason="WindowsのTerminateProcessは子が拒めない"),
        ),
    ],
)
async def test_one_shot_to_unresponsive_host_ends_within_its_timeout(
    monkeypatch: pytest.MonkeyPatch, resolve_timeout: float, remote_code: str, ssh_runs: int
) -> None:
    """応答しない接続先への単発の要求は、版の照会と起動を合わせて要求の上限で、その上限を示す`subprocess.TimeoutExpired`になる。

    版を覚えていない接続先で、版の照会が別の上限を持ち、`read`の上限30秒に対して90秒待った（2026-11-19の検証で観測）。
    照会が自身の上限で打ち切られた場合は残り時間で起動を試し、要求の期限に達した後はSSHを増やさない。
    照会の子がSIGTERMで終わらず、子の終了を待つ間に要求の期限を過ぎた場合も、新しいSSHを始めない。
    期限の後に`3.15`で起動を始めた（同日の検証で観測）。
    """
    monkeypatch.setattr(serve_remote, "_RESOLVE_TIMEOUT_SEC", resolve_timeout)
    real_exec = asyncio.create_subprocess_exec
    runs: list[tuple[str, ...]] = []

    async def unresponsive(*cmd: str, **kwargs: typing.Any) -> asyncio.subprocess.Process:
        runs.append(cmd)
        return await real_exec(sys.executable, "-c", remote_code, **kwargs)

    monkeypatch.setattr(serve_remote.asyncio, "create_subprocess_exec", unresponsive)
    request = serve_remote.run_remote_helper(
        sessions.REMOTE_BOOTSTRAP, f"unresponsive-{uuid.uuid4().hex}", "read", ["x"], ssh_options=(), timeout=1
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as error:
        await asyncio.wait_for(request, timeout=10)

    assert time.monotonic() - started < 3
    assert error.value.timeout == 1
    assert len(runs) == ssh_runs


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

    (checkout / "scripts" / "atk_serve_sessions_remote_helper.py").write_text(
        "import os, pathlib, sys, time\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
        "import checkout_module\n"
        "def main():\n"
        "    signals = pathlib.Path(os.environ['HELPER_SIGNALS'])\n"
        "    (signals / 'started').touch()\n"
        "    while not (signals / 'release').exists():\n"
        "        time.sleep(0.05)\n"
        "    print('[]', flush=True)\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main())\n",
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

    assert {path for path in written - inside if not dedicated.startswith(f"{path}/")} == set()
    assert inside


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="応答の遅いPythonの代わりに、PATHの後方へシェルスクリプトを置く")
@pytest.mark.parametrize(
    ("responds_after", "minor_launches"),
    [(None, True), (2.0, False)],
    ids=["stalled-listing", "slow-listing-on-elevated-ssh"],
)
async def test_one_shot_launches_when_python_listing_stalls(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, responds_after: float | None, minor_launches: bool
) -> None:
    """実際のuvで、接続先のPATHの後方に応答の遅い`python3.15`があっても、版の照会を打ち切って起動し、一覧を返す。

    `uv python list`はPATHの全てのPythonを調べ、`uv run --python 3.15`は最初に見つけたPythonで起動する。
    照会が要求の期限まで待つと、起動できる接続先で一度も起動を試さずに一覧の取得が失敗した（2026-11-19の検証で観測）。
    昇格したSSHのsthenoのように`3.15`の指定だけが`os error 448`で起動できない接続先では、
    要求の期限まで照会し直してパッチ版で起動する。
    照会を打ち切ったまま`3.15`の起動の失敗を返すと、パッチ版なら起動できる接続先で一覧の取得が失敗した（同日の検証で観測）。
    専用キャッシュが空の初めての接続先で、索引から依存を取得して起動する。
    照会の上限と要求の上限は、`atk serve`の値（照会10秒、一覧300秒）を縮めて使う。
    """
    home = tmp_path / "home"
    helper = home / "dotfiles" / "agent-toolkit" / "scripts" / "atk_serve_sessions_remote_helper.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("def main():\n    print('[]')\n    return 0\n", encoding="utf-8")
    wheels = tmp_path / "wheels"
    _write_dependency_wheels(wheels)
    environment = _remote_host_environment(home, tmp_path / "uv-cache", tmp_path)
    _route_ssh_to_local_shell(monkeypatch, home, environment)
    stalled = tmp_path / "stalled"
    stalled.mkdir()
    release = tmp_path / "stalled.release"
    python = pathlib.Path(sys.base_prefix) / "bin" / f"python{sys.version_info.major}.{sys.version_info.minor}"

    (stalled / "python3.15").write_text(
        f"#!/bin/sh\nwhile [ ! -e '{release}' ]; do\n  [ -e '{stalled}' ] || exit 1\n  sleep 0.05\ndone\n"
        f"exec '{python}' \"$@\"\n",
        encoding="utf-8",
    )
    (stalled / "python3.15").chmod(0o755)
    environment["PATH"] += os.pathsep + str(stalled)
    if not minor_launches:
        routed = asyncio.create_subprocess_exec

        async def elevated(*cmd: str, **kwargs: typing.Any) -> asyncio.subprocess.Process:
            if "--python 3.15 " in " ".join(cmd):
                cmd = (*cmd[:2], "sh", "-c", "'echo \"error: Failed to query Python interpreter (os error 448)\" >&2; exit 2'")
            return await routed(*cmd, **kwargs)

        monkeypatch.setattr(serve_remote.asyncio, "create_subprocess_exec", elevated)
    monkeypatch.setattr(serve_remote, "_RESOLVE_TIMEOUT_SEC", 0.5)
    responds = None if responds_after is None else asyncio.get_running_loop().call_later(responds_after, release.touch)

    try:
        with _package_index(wheels) as index_url:
            environment["UV_DEFAULT_INDEX"] = index_url
            output = await serve_remote.run_remote_helper(
                sessions.REMOTE_BOOTSTRAP, f"stalled-{uuid.uuid4().hex}", "list", [], ssh_options=(), timeout=20
            )
    finally:
        if responds is not None:
            responds.cancel()
        release.touch()

    assert output.strip() == "[]"


def _route_ssh_to_local_shell(monkeypatch: pytest.MonkeyPatch, home: pathlib.Path, environment: dict[str, str]) -> None:
    """単発と常駐のSSHを、接続先のホームで起動形の要素を空白で連結したコマンドをシェルで実行する形へ置き換える。

    `ssh_options`を空にした呼び出しの`["ssh", host, ...]`の3要素目以降が、接続先のシェルへ渡る起動形である。
    POSIXのシェルは`exec`で起動形のuvに置き換わる。
    単発SSHが上限で子プロセスを終了すると、sshの場合と同じく標準出力と標準エラー出力の書き手が終わる。
    """
    replace_shell = "exec " if os.name == "posix" else ""

    async def fake_exec(*cmd: str, **kwargs: typing.Any) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_shell(replace_shell + " ".join(cmd[2:]), cwd=home, env=environment, **kwargs)

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
    directory.mkdir()
    for name, version in (("watchdog", "6.0.0"), ("platformdirs", "4.0.0"), ("psutil", "7.2.2")):
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
    return {f"home/{path.relative_to(home).as_posix()}" for path in home.rglob("*")} | {
        f"uv-cache/{path.relative_to(cache).as_posix()}" for path in cache.rglob("*")
    }


def _isolate_environment(home: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("CODEX_HOME", raising=False)

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


@pytest.mark.parametrize("prefix", ["", "x" * 600 + " "])
def test_stderr_excerpt_redacts_credentials_before_taking_tail(prefix: str) -> None:
    stderr = f"{prefix}fatal: https://user:synthetic-secret-0123@example.org/repo password=synthetic-secret-4567".encode()
    excerpt = serve_remote.stderr_excerpt(stderr)
    assert "synthetic-secret" not in excerpt
    assert "[REDACTED]" in excerpt
    assert excerpt.startswith("...") == bool(prefix)
    assert len(excerpt) <= serve_remote.STDERR_EXCERPT_MAX_CHARS + len("...")


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (FileNotFoundError(), 404),
        (PermissionError(), 403),
        (TimeoutError(), 503),
        (serve_remote.RemoteHelperError(255, b"user@host: Permission denied (publickey)."), 403),
        (serve_remote.RemoteHelperError(255, b"Connection timed out"), 503),
        (serve_remote.RemoteHelperError(255, b"Host key verification failed."), 403),
    ],
)
def test_read_failure_status_preserves_the_cause(error, status):
    assert serve_remote.read_failure_status(error) == status
