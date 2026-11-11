"""共通のCodex稼働判定と表示ラベルのテスト。"""

import os
import pathlib
import subprocess
import sys
import typing
from types import SimpleNamespace

import psutil
import pytest

from pytools._internal import codex_processes


@pytest.fixture(autouse=True)
def _posix_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """既存のuids入力のテストを全OSで同じ所有者条件にそろえる。"""
    monkeypatch.setattr(codex_processes, "os", SimpleNamespace(name="posix", getuid=lambda: 1000))


def _patch_enumeration(monkeypatch: pytest.MonkeyPatch, processes: list[typing.Any], own: object = None) -> None:
    """列挙と、列挙した直後のPIDからのProcess構築を、同じ偽のプロセス集合へ差し替える。

    製品は列挙したPIDから新しいProcessを構築して属性を読むため、偽の`info`をその構築結果から返す。
    `own`は引数なしの`psutil.Process()`（自プロセスの照会）が返す値とする。
    """
    current: list[typing.Any] = []

    def listed(*_args: object, **_kwargs: object) -> typing.Iterator[typing.Any]:
        for process in processes:
            current[:] = [process]
            yield process

    def build(pid: int | None = None, **_kwargs: object) -> object:
        if pid is None:
            return own
        process = current[0]
        assert process.pid == pid
        return SimpleNamespace(pid=pid, as_dict=lambda _attributes, ad_value=None: process.info)

    monkeypatch.setattr(codex_processes.psutil, "process_iter", listed)
    monkeypatch.setattr(codex_processes.psutil, "Process", build)


def test_daemon_detection_excludes_other_users_and_prompt_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ所有者のdaemonを検出し、他ユーザーと引数中の検索語を稼働判定へ混ぜない。"""
    uid = 1000
    monkeypatch.setattr(codex_processes.os, "getuid", lambda: uid, raising=False)
    processes = [
        SimpleNamespace(
            pid=1,
            info={"name": "codex", "exe": "/bin/codex", "cmdline": ["codex", "app-server"], "uids": SimpleNamespace(real=uid)},
        ),
        SimpleNamespace(
            pid=2,
            info={
                "name": "codex",
                "exe": "/bin/codex",
                "cmdline": ["codex", "app-server"],
                "uids": SimpleNamespace(real=uid + 1),
            },
        ),
        SimpleNamespace(
            pid=3,
            info={"name": "grep", "exe": "/bin/grep", "cmdline": ["grep", "@openai/codex"], "uids": SimpleNamespace(real=uid)},
        ),
    ]
    _patch_enumeration(monkeypatch, processes)
    labels = codex_processes.running_codex_processes()
    assert labels == ("codex app-server",)
    assert codex_processes.format_running_processes(labels) == "codex app-server (1件)"


def test_daemon_detection_excludes_zombie_of_same_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ所有者でも、ゾンビと終了済みのCodexは稼働中に数えず、生存中のCodexだけを保護する。"""
    uid = 1000
    monkeypatch.setattr(codex_processes.os, "getuid", lambda: uid, raising=False)
    owner = SimpleNamespace(real=uid)
    # ゾンビは実行名を返し、command lineを返さない。
    zombies = [
        SimpleNamespace(pid=pid, info={"name": "codex", "exe": "", "cmdline": [], "uids": owner, "status": status})
        for pid, status in ((1, codex_processes.psutil.STATUS_ZOMBIE), (2, codex_processes.psutil.STATUS_DEAD))
    ]
    running = SimpleNamespace(
        pid=3,
        info={
            "name": "codex",
            "exe": "/bin/codex",
            "cmdline": ["codex", "app-server"],
            "uids": owner,
            "status": codex_processes.psutil.STATUS_SLEEPING,
        },
    )
    processes = list(zombies)
    _patch_enumeration(monkeypatch, processes)
    assert not codex_processes.running_codex_processes()
    processes.append(running)
    assert codex_processes.running_codex_processes() == ("codex app-server",)


_DAEMON_EXE = "/home/user/.codex/packages/app-server-daemon/current/bin/codex"


def _process(pid: int, cmdline: list[str], *, ppid: int = 1, name: str = "codex", exe: str = _DAEMON_EXE) -> SimpleNamespace:
    return SimpleNamespace(
        pid=pid,
        info={
            "name": name,
            "exe": exe,
            "cmdline": cmdline,
            "uids": SimpleNamespace(real=1000),
            "status": "sleeping",
            "ppid": ppid,
        },
    )


def _patch_processes(monkeypatch: pytest.MonkeyPatch, processes: list[SimpleNamespace]) -> None:
    monkeypatch.setattr(codex_processes.os, "getuid", lambda: 1000, raising=False)
    _patch_enumeration(monkeypatch, processes)


def test_managed_daemon_and_update_loop_are_classified_by_launch_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """管理daemonとその補助実行体はdaemonへ、更新ループは利用プロセスの外へ分類する。"""
    _patch_processes(
        monkeypatch,
        [
            _process(10, [_DAEMON_EXE, "app-server", "daemon", "pid-update-loop"]),
            _process(11, [_DAEMON_EXE, "app-server", "--listen", "unix://", "--managed-daemon"], ppid=10),
            _process(
                12,
                [_DAEMON_EXE.replace("/codex", "/codex-code-mode-host")],
                ppid=11,
                name="codex-code-mode-host",
                exe=_DAEMON_EXE.replace("/codex", "/codex-code-mode-host"),
            ),
        ],
    )
    assert [(process.pid, process.role) for process in codex_processes.codex_processes()] == [
        (10, "update-loop"),
        (11, "managed-daemon"),
        (12, "managed-daemon"),
    ]
    assert [process.executable for process in codex_processes.codex_processes()] == [
        _DAEMON_EXE,
        _DAEMON_EXE,
        _DAEMON_EXE.replace("/codex", "/codex-code-mode-host"),
    ]
    assert codex_processes.running_codex_processes() == ("codex app-server --managed-daemon", "codex-code-mode-host")


def test_update_loop_alone_is_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """管理daemonの停止後に更新ループだけが残る場合は、plugin実体と診断ログDBを参照するプロセスは無い。"""
    _patch_processes(monkeypatch, [_process(10, [_DAEMON_EXE, "app-server", "daemon", "pid-update-loop"])])
    assert not codex_processes.running_codex_processes()
    assert [process.role for process in codex_processes.codex_processes()] == ["update-loop"]


@pytest.mark.parametrize(
    "cmdline",
    [
        pytest.param(["codex", "app-server", "--stdio"], id="agents-server-stdio"),
        pytest.param(["codex"], id="interactive"),
        pytest.param(["codex", "exec", "app-server daemon pid-update-loop"], id="prompt-mentions-update-loop"),
        pytest.param(["codex", "--managed-daemon"], id="flag-without-app-server"),
    ],
)
def test_use_sessions_are_not_mistaken_for_daemon_processes(monkeypatch: pytest.MonkeyPatch, cmdline: list[str]) -> None:
    """公開された起動形と一致しないCodexは、引数に同じ語を含んでも利用セッションとして保護する。"""
    _patch_processes(monkeypatch, [_process(20, cmdline, exe="/bin/codex")])
    assert [process.role for process in codex_processes.codex_processes()] == ["session"]
    assert codex_processes.running_codex_processes()


def test_child_of_session_is_not_daemon_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """管理daemon以外の子として動く補助実行体は、親の利用セッションとともに保護する。"""
    _patch_processes(
        monkeypatch,
        [
            _process(10, [_DAEMON_EXE, "app-server", "--listen", "unix://", "--managed-daemon"]),
            _process(20, ["codex", "app-server", "--stdio"], exe="/bin/codex"),
            _process(21, ["codex-code-mode-host"], ppid=20, name="codex-code-mode-host"),
        ],
    )
    assert [(process.pid, process.role) for process in codex_processes.codex_processes()] == [
        (10, "managed-daemon"),
        (20, "session"),
        (21, "session"),
    ]


def test_node_package_update_loop_is_recognized(monkeypatch: pytest.MonkeyPatch) -> None:
    """`node <entry point>`形式の起動では、entry pointの後ろから起動形を判別する。"""
    entry = "/opt/node_modules/@openai/codex/bin/codex.js"
    _patch_processes(
        monkeypatch,
        [_process(30, ["node", entry, "app-server", "daemon", "pid-update-loop"], name="node", exe="/usr/bin/node")],
    )
    assert [process.role for process in codex_processes.codex_processes()] == ["update-loop"]


def test_windows_process_owner_and_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windowsではusernameで自ユーザーを選び、使用版の実行パスを返す。"""
    monkeypatch.setattr(codex_processes, "os", SimpleNamespace(name="nt"))
    own = _process(20, ["codex.exe"], exe="C:/codex/releases/old/bin/codex.exe")
    own.info["username"] = "host\\user"
    other = _process(21, ["codex.exe"])
    other.info["username"] = "host\\other"
    _patch_enumeration(monkeypatch, [own, other], own=SimpleNamespace(username=lambda: "host\\user"))
    processes = codex_processes.codex_processes()
    assert len(processes) == 1
    assert processes[0].executable == "C:/codex/releases/old/bin/codex.exe"


def _inject_previous_generation(monkeypatch: pytest.MonkeyPatch, pid: int, *, name: str, exe: str) -> None:
    """列挙が再利用するProcessだけを以前の世代の実行名・実行ファイルにし、走査を対象のPIDへ限る。

    PIDの自然な再利用や実行ファイルの置き換えを待たずに、列挙キャッシュへ以前の値が残った状態を作る。
    """
    stale = psutil.Process(pid)
    monkeypatch.setattr(stale, "_name", name)
    monkeypatch.setattr(stale, "_exe", exe)
    monkeypatch.setitem(vars(psutil)["_pmap"], pid, stale)
    real_process_iter = psutil.process_iter

    def own_process(*args: typing.Any, **kwargs: typing.Any) -> list[psutil.Process]:
        return [item for item in real_process_iter(*args, **kwargs) if item.pid == pid]

    monkeypatch.setattr(psutil, "process_iter", own_process)


@pytest.mark.skipif(os.name == "nt", reason="実行ファイルへのシンボリックリンクと起動したPIDだけの走査がPOSIX前提のため")
@pytest.mark.parametrize("current_is_codex", [True, False], ids=["current-codex", "previous-generation-codex"])
def test_stale_enumeration_cache_does_not_change_classification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, current_is_codex: bool
) -> None:
    """列挙キャッシュに以前の世代の実行名と実行ファイルが残っていても、現在の属性で分類し、現在の実行パスを返す。

    現在の起動形がCodexの実プロセスは現在の実行ファイルとともに検出し、
    以前の世代だけがCodexだった実プロセスは検出しない。旧版の整理は返した実行パスで使用中の版を保持する。
    """
    monkeypatch.setattr(codex_processes, "os", SimpleNamespace(name="posix", getuid=os.getuid))
    launcher = tmp_path / "releases" / "new" / "bin" / "codex"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(sys.executable)
    executable = str(launcher) if current_is_codex else sys.executable
    with subprocess.Popen([executable, "-c", "import time; time.sleep(60)"]) as child:
        try:
            current_exe = psutil.Process(child.pid).exe()
            if current_is_codex:
                _inject_previous_generation(monkeypatch, child.pid, name="python", exe="/previous/other")
            else:
                _inject_previous_generation(monkeypatch, child.pid, name="codex", exe="/previous/releases/old/bin/codex")
            found = codex_processes.codex_processes()
        finally:
            child.kill()
    if current_is_codex:
        assert [(process.pid, process.executable) for process in found] == [(child.pid, current_exe)]
    else:
        assert not found
