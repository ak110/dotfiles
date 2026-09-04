"""agent-toolkitキュー移行ステップを検証する。"""

import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from pytools._internal import migrate_atk_queue


def _process(name: str = "", exe: str = "", cmdline: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(info={"name": name, "exe": exe, "cmdline": cmdline or []})


@pytest.mark.parametrize(
    "process",
    (
        _process(name="claude.exe"),
        _process(exe="/usr/local/bin/codex"),
        _process(cmdline=["/usr/bin/node", "/opt/@anthropic-ai/claude-code/cli.js"]),
        _process(cmdline=["node.exe", "C:/npm/@openai/codex/bin/codex.js"]),
    ),
)
def test_run_defers_while_agent_is_running(monkeypatch: pytest.MonkeyPatch, process: SimpleNamespace) -> None:
    monkeypatch.setattr(migrate_atk_queue.psutil, "process_iter", lambda _fields: [process])
    monkeypatch.setattr(
        migrate_atk_queue.claude_common,
        "run_subprocess",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("移行を実行してはならない")),
    )
    assert migrate_atk_queue.run() is False


@pytest.mark.parametrize(
    "process",
    (
        pytest.param(_process(cmdline=["node", "server.js"]), id="unrelated-node"),
        pytest.param(_process(name="claude-statusline"), id="claude-statusline"),
    ),
)
def test_run_ignores_non_agent_processes_and_runs_both_migrations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    process: SimpleNamespace,
) -> None:
    """無関係な実行体と接頭辞だけが一致する実行体は移行を抑止しない。"""
    atk = tmp_path / "agent-toolkit/scripts/atk.py"
    atk.parent.mkdir(parents=True)
    atk.write_text("", encoding="utf-8")
    uv = tmp_path / "uv"
    monkeypatch.setattr(migrate_atk_queue.psutil, "process_iter", lambda _fields: [process])
    monkeypatch.setattr(migrate_atk_queue.claude_common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(migrate_atk_queue.claude_common, "resolve_uv_path", lambda: uv)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output = (
            "計画ファイルを移行しました: 0件（旧ファイル削除: 0件）"
            if "plans" in command
            else "0件を変換しました（うち0件を移動）。"
        )
        return subprocess.CompletedProcess(command, 0, output + "\n", "")

    monkeypatch.setattr(migrate_atk_queue.claude_common, "run_subprocess", fake_run)
    assert migrate_atk_queue.run() is False
    assert calls == [
        [str(uv), "run", "--no-project", "--script", str(atk), "plans", "migrate"],
        [str(uv), "run", "--no-project", "--script", str(atk), "wi", "migrate"],
    ]


def test_run_defers_when_process_list_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """プロセス一覧の取得失敗時は不在と断定せず移行を延期する。"""

    def raise_access_denied(_fields: tuple[str, ...]) -> list[SimpleNamespace]:
        raise psutil.AccessDenied(pid=123)

    monkeypatch.setattr(migrate_atk_queue.psutil, "process_iter", raise_access_denied)
    monkeypatch.setattr(
        migrate_atk_queue.claude_common,
        "run_subprocess",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("移行を実行してはならない")),
    )

    with caplog.at_level(logging.INFO, logger=migrate_atk_queue.logger.name):
        assert migrate_atk_queue.run() is False
    assert "プロセス一覧を取得できず判定不能 (AccessDenied)" in caplog.text


def test_run_defers_when_process_information_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """個別プロセスの取得失敗時は不在と断定せず移行を延期する。"""

    class UnreadableProcess:
        @property
        def info(self) -> dict[str, object]:
            raise psutil.AccessDenied(pid=123)

    monkeypatch.setattr(migrate_atk_queue.psutil, "process_iter", lambda _fields: [UnreadableProcess()])
    monkeypatch.setattr(
        migrate_atk_queue.claude_common,
        "run_subprocess",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("移行を実行してはならない")),
    )

    with caplog.at_level(logging.INFO, logger=migrate_atk_queue.logger.name):
        assert migrate_atk_queue.run() is False
    assert "プロセス情報を取得できず判定不能 (AccessDenied)" in caplog.text


def test_run_reports_changes_and_continues_after_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    atk = tmp_path / "agent-toolkit/scripts/atk.py"
    atk.parent.mkdir(parents=True)
    atk.write_text("", encoding="utf-8")
    monkeypatch.setattr(migrate_atk_queue.psutil, "process_iter", lambda _fields: [])
    monkeypatch.setattr(migrate_atk_queue.claude_common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(migrate_atk_queue.claude_common, "resolve_uv_path", lambda: tmp_path / "uv")
    results = iter(
        (
            subprocess.CompletedProcess(["uv"], 1, "", "失敗"),
            subprocess.CompletedProcess(["uv"], 0, "2件を変換しました（うち2件を移動）。\n", ""),
        )
    )
    monkeypatch.setattr(migrate_atk_queue.claude_common, "run_subprocess", lambda *_args, **_kwargs: next(results))
    assert migrate_atk_queue.run() is True


@pytest.mark.parametrize("missing", ("root", "uv", "atk"))
def test_run_skips_when_prerequisite_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing: str,
) -> None:
    monkeypatch.setattr(migrate_atk_queue.psutil, "process_iter", lambda _fields: [])
    root = None if missing == "root" else tmp_path
    monkeypatch.setattr(migrate_atk_queue.claude_common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(
        migrate_atk_queue.claude_common, "resolve_uv_path", lambda: None if missing == "uv" else tmp_path / "uv"
    )
    if missing != "atk":
        atk = tmp_path / "agent-toolkit/scripts/atk.py"
        atk.parent.mkdir(parents=True)
        atk.write_text("", encoding="utf-8")
    assert migrate_atk_queue.run() is False
