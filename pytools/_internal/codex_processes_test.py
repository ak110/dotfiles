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
