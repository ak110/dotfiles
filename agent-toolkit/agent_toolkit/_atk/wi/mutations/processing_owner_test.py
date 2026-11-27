import pathlib
import socket
import subprocess
import sys

import psutil
import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import frontmatter, layout, processing_owner, sync
from agent_toolkit._common import process_identity, session_activity, session_state
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_mutations_support import _init_notes_with_origin
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes, _write_awi_file


def _run(home: pathlib.Path, *args: str) -> int:
    with pytest.raises(SystemExit) as result:
        argv = ["wi", *args]
        if args[0] != "take-over":
            argv.append("--target-repo=github.com/example/foo")
        atk.main(argv, home=home, now=_FIXED_DT)
    assert isinstance(result.value.code, int)
    return result.value.code


def _metadata(notes: pathlib.Path, name: str) -> dict[str, object]:
    entry = layout.entry_path(notes, name)
    parsed = frontmatter.parse_frontmatter(entry.read_text(encoding="utf-8"))
    assert parsed is not None
    return parsed[0]


def _actor(monkeypatch: pytest.MonkeyPatch, notes: pathlib.Path, session: str) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", session)
    monkeypatch.setenv("AI_AGENT", "1")


@pytest.mark.parametrize("action", ["adopt", "reject", "return-to-inbox", "hold"])
def test_only_processing_conversation_can_exit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes)
    name = "owned.md"
    _write_awi_file(notes, name)
    _actor(monkeypatch, notes, "owner")
    monkeypatch.setenv("CODEX_THREAD_ID", "lane-child")
    assert _run(tmp_path, "start-processing", name) == 0
    assert _metadata(notes, name)["processing_owner"] == {"host": socket.gethostname(), "session": "owner"}

    option = ("--state=processing",) if action == "hold" else ()
    _actor(monkeypatch, notes, "another")
    assert _run(tmp_path, action, name, *option) == 1
    assert "take-over" in capsys.readouterr().err
    assert _metadata(notes, name)["state"] == "processing"

    _actor(monkeypatch, notes, "owner")
    assert _run(tmp_path, action, name, *option) == 0
    assert "processing_owner" not in _metadata(notes, name)


@pytest.mark.parametrize("reason", [None, '利用者の確認: "終了済み"'])
def test_same_host_handoff_without_process_records(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, reason: str | None
) -> None:
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes)
    name = "takeover.md"
    _write_awi_file(notes, name)
    _actor(monkeypatch, notes, "earlier")
    assert _run(tmp_path, "start-processing", name) == 0
    _actor(monkeypatch, notes, "later")
    options = () if reason is None else (f"--reason={reason}",)
    assert _run(tmp_path, "take-over", name, *options) == 0
    assert _metadata(notes, name)["processing_owner"] == {"host": socket.gethostname(), "session": "later"}
    assert _run(tmp_path, "adopt", name) == 0
    result = layout.entry_path(notes, name).read_text(encoding="utf-8")
    assert "担当の引き継ぎ:" in result and result.count("## 処理結果") == 1
    assert "稼働中のホストプロセスを確認できなかった" in result
    assert "ホストプロセスの終了を確認" not in result
    assert ('利用者の確認: \\"終了済み\\"' in result) is (reason is not None)


@pytest.mark.parametrize("human", [False, True])
def test_old_processing_item_and_human_exit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, human: bool) -> None:
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes)
    name = "compatibility.md"
    _write_awi_file(notes, name)
    _actor(monkeypatch, notes, "another-owner")

    def clear_agent_environment() -> None:
        for variable in ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT"):
            monkeypatch.delenv(variable, raising=False)

    if not human:
        clear_agent_environment()
    assert _run(tmp_path, "start-processing", name) == 0
    assert ("processing_owner" in _metadata(notes, name)) is human
    _actor(monkeypatch, notes, "adopting-session")
    if human:
        clear_agent_environment()
    assert _run(tmp_path, "adopt", name) == 0
    assert _metadata(notes, name)["state"] == "adopted"


def test_same_host_handoff_after_activity_cleanup(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes)
    name = "ending.md"
    _write_awi_file(notes, name)
    _actor(monkeypatch, notes, "ending-owner")
    with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE) as process:
        identity = process_identity.ProcessIdentity.of(process.pid)
        assert identity is not None
        session_activity.record_activity(
            {"session_id": "ending-owner", "hook_event_name": "SessionStart"},
            process=session_activity.HostProcess("codex", identity),
        )
        assert _run(tmp_path, "start-processing", name) == 0
        _actor(monkeypatch, notes, "following-owner")
        assert _run(tmp_path, "take-over", name) == 1
        assert "（稼働中）" in capsys.readouterr().err
        session_activity.record_activity({"session_id": "ending-owner", "hook_event_name": "SessionEnd"})
        assert session_activity.root_activity("ending-owner")["status"] == "不明"
        assert _run(tmp_path, "take-over", name, "--reason=終了済み") == 1
        error = capsys.readouterr().err
        assert "（稼働中か不明）" in error
        assert "前の担当の終了を待つか" in error
        assert _metadata(notes, name)["processing_owner"] == {"host": socket.gethostname(), "session": "ending-owner"}
        process.communicate(timeout=5)
    assert _run(tmp_path, "take-over", name, "--reason=終了済み") == 0
    assert _metadata(notes, name)["processing_owner"] == {"host": socket.gethostname(), "session": "following-owner"}
    result = layout.entry_path(notes, name).read_text(encoding="utf-8")
    assert '記録されたホストプロセスの終了を確認。指定された理由: "終了済み"' in result


def test_same_host_handoff_refuses_when_recorded_process_status_is_unreadable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes)
    name = "active.md"
    _write_awi_file(notes, name)
    _actor(monkeypatch, notes, "active-owner")
    assert _run(tmp_path, "start-processing", name) == 0
    assert not session_state.state_path("active-owner").exists()
    with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE) as process:
        identity = process_identity.ProcessIdentity.of(process.pid)
        assert identity is not None
        session_activity.record_activity(
            {"session_id": "active-owner", "hook_event_name": "SessionStart"},
            process=session_activity.HostProcess("codex", identity),
        )
        read_status = psutil.Process.status

        def status(candidate: psutil.Process) -> str:
            if candidate.pid == process.pid:
                raise psutil.AccessDenied(pid=process.pid)
            return read_status(candidate)

        monkeypatch.setattr(psutil.Process, "status", status)
        _actor(monkeypatch, notes, "following-owner")
        assert _run(tmp_path, "take-over", name) == 1
        assert "（稼働中か不明）" in capsys.readouterr().err
        assert _metadata(notes, name)["processing_owner"] == {"host": socket.gethostname(), "session": "active-owner"}
        process.communicate(timeout=5)


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.parametrize(
    "case",
    ["takeover-before", "takeover-during", "unrelated", "same-item-text", "rebase-conflict", "unreachable", "skip-push"],
)
def test_owned_transition_checks_latest_owner_before_retry(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    first = tmp_path / "first"
    first.mkdir()
    notes = _setup_notes(first)
    name = "owned.md"
    _write_awi_file(
        notes,
        name,
        body="# テスト本文\n\n最初の本文\n\n## ユーザー指摘\n\n> 指摘の1行目\n> 2行目\n> 3行目\n> 4行目",
    )
    remote = tmp_path / "origin.git"
    _init_notes_with_origin(notes, remote)
    _actor(monkeypatch, notes, "earlier")
    assert _run(first, "start-processing", name) == 0
    before = git_repository.git_output(notes, "rev-parse", "HEAD")
    second = tmp_path / "second"
    second.mkdir()
    clone = second / "private-notes"
    git_repository.run_git(tmp_path, "clone", "--branch", "main", str(remote), str(clone))
    for key, value in (("user.name", "queue-test"), ("user.email", "queue-test@example.invalid")):
        git_repository.run_git(clone, "config", key, value)

    def update_remote() -> None:
        if case.startswith("takeover"):
            with monkeypatch.context() as temporary:
                _actor(temporary, clone, "later")
                temporary.setattr(processing_owner.socket, "gethostname", lambda: "second-host")
                assert _run(second, "take-over", name) == 1
                assert _run(second, "take-over", name, "--reason=停止を確認した") == 0
        else:
            if case == "unrelated":
                path = clone / "upstream.txt"
                path.write_text("別の項目\n", encoding="utf-8")
            else:
                path = layout.entry_path(clone, name)
                text = path.read_text(encoding="utf-8")
                if case == "same-item-text":
                    text = text.replace("## ユーザー指摘", "別の会話が追加した本文\n\n## ユーザー指摘", 1)
                    assert "別の会話が追加した本文" in text
                else:
                    text = text.replace("state: processing", "state: processing # upstream")
                path.write_text(text, encoding="utf-8")
            git_repository.run_git(clone, "add", "--", str(path))
            git_repository.run_git(clone, "commit", "-m", "upstream update")
            git_repository.run_git(clone, "push")

    if case == "unreachable":
        remote.rename(tmp_path / "unreachable.git")
    elif case == "skip-push":
        assert _run(first, "adopt", name, "--skip-push") == 1
        assert git_repository.git_output(notes, "rev-parse", "HEAD") == before
        assert _metadata(notes, name)["state"] == "processing"
        return
    elif case == "takeover-before":
        update_remote()
        assert git_repository.git_output(notes, "rev-parse", "HEAD") == before
    else:
        original = sync.run_git
        injected = False

        def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
            nonlocal injected
            if cwd == notes and args == ["push"] and not injected:
                injected = True
                update_remote()
            original(args, cwd, forward_error_output=forward_error_output)

        monkeypatch.setattr(sync, "run_git", run_git)

    result = _run(first, "adopt", name)
    if case in {"unrelated", "same-item-text"}:
        assert result == 0
        assert _metadata(notes, name)["state"] == "adopted"
        if case == "unrelated":
            assert (notes / "upstream.txt").is_file()
        else:
            assert "別の会話が追加した本文" in layout.entry_path(notes, name).read_text(encoding="utf-8")
        return
    assert result == 1
    assert _metadata(notes, name)["state"] == "processing"
    assert git_repository.git_output(notes, "status", "--porcelain") == ""
    with pytest.raises(subprocess.CalledProcessError):
        git_repository.run_git(notes, "rev-parse", "--verify", "REBASE_HEAD")
    if case != "takeover-before":
        assert git_repository.git_output(notes, "rev-parse", "HEAD") == before


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.parametrize("bulk", [False, True])
def test_agent_exit_checks_owner_added_since_local_read(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bulk: bool
) -> None:
    notes = _setup_notes(tmp_path)
    name = "stale.md"
    _write_awi_file(notes, name)
    remote = tmp_path / "origin.git"
    _init_notes_with_origin(notes, remote)
    clone = tmp_path / "other"
    git_repository.run_git(tmp_path, "clone", "--branch", "main", str(remote), str(clone))
    git_repository.run_git(clone, "config", "user.name", "queue-test")
    git_repository.run_git(clone, "config", "user.email", "queue-test@example.invalid")

    _actor(monkeypatch, clone, "upstream-owner")
    assert _run(tmp_path, "start-processing", name) == 0
    assert _metadata(clone, name)["state"] == "processing"
    assert _metadata(notes, name)["state"] == "inbox"
    capsys.readouterr()
    _actor(monkeypatch, notes, "other-agent")
    if bulk:
        assert _run(tmp_path, "adopt", "--all", "--yes", "--skip-pull") == 0
        assert "確認後に変更されたため" in capsys.readouterr().out
    else:
        assert _run(tmp_path, "adopt", name) == 1
        assert "upstream-owner" in capsys.readouterr().err

    assert _metadata(notes, name)["state"] == "processing"
    assert _metadata(notes, name)["processing_owner"] == _metadata(clone, name)["processing_owner"]
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == git_repository.git_output(clone, "rev-parse", "HEAD")


def test_reopening_preserves_sections_after_processing_result(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes)
    name = "commented.md"
    comment = "## ユーザーコメント\n\nユーザーの追記。\n\n## 関連資料\n\n引き継ぐ情報。"
    _write_awi_file(notes, name, body=f"# 要求\n\n## 処理結果\n\n- 担当の引き継ぎ: 済\n\n{comment}")
    _actor(monkeypatch, notes, "acting-owner")
    assert _run(tmp_path, "adopt", name) == 0
    assert _run(tmp_path, "hold", name, "--state=adopted") == 0
    body = layout.entry_path(notes, name).read_text(encoding="utf-8")
    assert comment in body
    assert "## 処理結果" not in body
    assert _metadata(notes, name)["state"] == "hold"


@pytest.mark.usefixtures("leaf_git_commands")
def test_legacy_processing_exit_checks_remote_without_sending_its_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = _setup_notes(tmp_path)
    name = "legacy.md"
    path = _write_awi_file(notes, name)
    path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", "state: processing"), encoding="utf-8")
    remote = tmp_path / "origin.git"
    _init_notes_with_origin(notes, remote)
    original = git_repository.git_output(remote, "rev-parse", "main")
    _actor(monkeypatch, notes, "current-agent")
    assert _run(tmp_path, "adopt", name, "--skip-push") == 0
    assert _metadata(notes, name)["state"] == "adopted"
    assert git_repository.git_output(remote, "rev-parse", "main") == original
    assert git_repository.git_output(notes, "rev-parse", "HEAD") != original
