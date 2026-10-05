"""共通のCodex稼働判定と表示ラベルのテスト。"""

from types import SimpleNamespace

import pytest

from pytools._internal import codex_processes


def test_daemon_detection_excludes_other_users_and_prompt_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ所有者のdaemonを拾い、他ユーザーと引数中の検索語を稼働判定へ混ぜない。"""
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
    monkeypatch.setattr(codex_processes.psutil, "process_iter", lambda *_args, **_kwargs: processes)
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
    monkeypatch.setattr(codex_processes.psutil, "process_iter", lambda *_args, **_kwargs: processes)
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
    monkeypatch.setattr(codex_processes.psutil, "process_iter", lambda *_args, **_kwargs: processes)


def test_managed_daemon_and_update_loop_are_classified_by_launch_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """管理daemonとその補助実行体はdaemonへ、更新ループは利用プロセスの外へ分類する。"""
    _patch_processes(
        monkeypatch,
        [
            _process(10, [_DAEMON_EXE, "app-server", "daemon", "pid-update-loop"]),
            _process(11, [_DAEMON_EXE, "app-server", "--listen", "unix://", "--managed-daemon"], ppid=10),
            _process(12, [_DAEMON_EXE.replace("/codex", "/codex-code-mode-host")], ppid=11, name="codex-code-mode-host"),
        ],
    )
    assert [(process.pid, process.role) for process in codex_processes.codex_processes()] == [
        (10, "update-loop"),
        (11, "managed-daemon"),
        (12, "managed-daemon"),
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
