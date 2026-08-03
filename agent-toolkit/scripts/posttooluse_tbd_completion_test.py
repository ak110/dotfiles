"""PostToolUseフックのTBD全件回答完了通知をサブプロセスで検証する。"""

import json
import os
import pathlib
import subprocess

import _fork_runner
import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook.py"
_REPO = "github.com/ak110/dotfiles"


def _entry(*, answer: str = "") -> str:
    """テスト用TBDエントリ本文を返す。"""
    return (
        "---\n"
        f"target_repo: {_REPO}\n"
        "type: tbd\n"
        "---\n\n"
        "## 質問\n\n本文\n\n"
        "## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
        f"{answer}"
    )


def _private_notes(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """一時保存先と未回答TBDを作成する。"""
    root = tmp_path / "private-notes"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    (root / "processing").mkdir()
    entry = inbox / "20260802-221851-001.md"
    entry.write_text(_entry(), encoding="utf-8")
    return root, entry


def _init_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """originを持つ一時Gitリポジトリを作成する。"""
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "remote", "add", "origin", "git@github.com:ak110/dotfiles.git"],
        check=True,
    )
    return repository


def _run(
    payload: dict,
    *,
    state_dir: pathlib.Path,
    private_notes: pathlib.Path,
    home: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """一時状態と保存先を指定してPostToolUseフックを実行する。"""
    env = os.environ.copy()
    for name in ("TMPDIR", "TEMP", "TMP"):
        env[name] = str(state_dir)
    env["AGENT_TOOLKIT_PRIVATE_NOTES"] = str(private_notes)
    if home is not None:
        env["HOME"] = str(home)
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("posttooluse",),
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
    )


def _payload(session_id: str, repository: pathlib.Path, *, tool_name: str = "Read") -> dict:
    """通常のPostToolUse payloadを返す。"""
    return {
        "session_id": session_id,
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": "README.md"},
        "cwd": str(repository),
    }


def _answer(entry: pathlib.Path) -> None:
    """TBDの回答欄へ回答本文を追記する。"""
    entry.write_text(entry.read_text(encoding="utf-8") + "回答\n", encoding="utf-8")


class TestTbdCompletionNotice:
    """未回答TBDが全件回答済みへ遷移した時点で通知する。"""

    def test_notifies_only_after_transition(self, tmp_path: pathlib.Path) -> None:
        private_notes, entry = _private_notes(tmp_path)
        repository = _init_repo(tmp_path)
        payload = _payload("transition", repository)

        first = _run(payload, state_dir=tmp_path, private_notes=private_notes)
        assert first.returncode == 0
        assert not first.stdout

        _answer(entry)
        second = _run(payload, state_dir=tmp_path, private_notes=private_notes)
        assert second.returncode == 0
        output = json.loads(second.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        assert "all TBD entries" in context
        assert _REPO in context
        assert entry.name in context

    @pytest.mark.parametrize("hook_event_name", ["PostToolUseFailure", "PermissionDenied"])
    def test_failure_events_do_not_notify(self, tmp_path: pathlib.Path, hook_event_name: str) -> None:
        private_notes, entry = _private_notes(tmp_path)
        repository = _init_repo(tmp_path)
        payload = _payload(f"failure-{hook_event_name}", repository)
        assert not _run(payload, state_dir=tmp_path, private_notes=private_notes).stdout
        _answer(entry)
        payload["hook_event_name"] = hook_event_name
        result = _run(payload, state_dir=tmp_path, private_notes=private_notes)
        assert result.returncode == 0
        assert not result.stdout

    def test_combines_plan_and_tbd_notices_into_one_json(self, tmp_path: pathlib.Path) -> None:
        private_notes, entry = _private_notes(tmp_path)
        repository = _init_repo(tmp_path)
        home = tmp_path / "home"
        plan = home / ".claude" / "plans" / "sample.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("# 計画\n", encoding="utf-8")

        skill_payload = _payload("combined", repository, tool_name="Skill")
        skill_payload["tool_input"] = {"skill": "agent-toolkit:plan-mode"}
        assert not _run(skill_payload, state_dir=tmp_path, private_notes=private_notes, home=home).stdout
        _answer(entry)

        write_payload = _payload("combined", repository, tool_name="Write")
        write_payload["tool_input"] = {"file_path": str(plan), "content": "# 計画\n"}
        result = _run(write_payload, state_dir=tmp_path, private_notes=private_notes, home=home)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        assert "plan file" in context
        assert "all TBD entries" in context
        assert len(result.stdout.strip().splitlines()) == 1

    def test_non_repository_cwd_is_ignored(self, tmp_path: pathlib.Path) -> None:
        private_notes, _entry_path = _private_notes(tmp_path)
        payload = _payload("not-repository", tmp_path)
        result = _run(payload, state_dir=tmp_path, private_notes=private_notes)
        assert result.returncode == 0
        assert not result.stdout
