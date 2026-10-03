"""エンドユーザーが使うatk commitコマンドを通して候補切替を検証する。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import commit


def _repo(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
    (repository / "change.txt").write_text("変更\n", encoding="utf-8")
    return repository


def test_public_commit_creates_git_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _repo(tmp_path)
    monkeypatch.chdir(repository)
    monkeypatch.setattr(commit, "_executable", lambda _engine: "/fake/claude")
    original_run = subprocess.run
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[0] == "/fake/claude":
            calls.append(command)
            assert Path(kwargs["cwd"]) != repository
            original_run(["git", "-C", str(repository), "add", "change.txt"], check=True)
            original_run(["git", "-C", str(repository), "commit", "-q", "-m", "test: 変更を保存する"], check=True)
            child_output = "子の完了報告\n"
            if kwargs.get("stdout") != subprocess.PIPE:
                print(child_output, end="")
                child_output = ""
            return subprocess.CompletedProcess(command, 0, stdout=child_output)
        return original_run(command, check=kwargs.pop("check", False), **kwargs)

    monkeypatch.setattr(commit.subprocess, "run", run)
    with pytest.raises(SystemExit, match="0"):
        atk.main(["commit", "--model-type", "claude:sonnet/high"])

    assert len(calls) == 1
    output = capsys.readouterr().out.splitlines()
    assert output[0].startswith("成功: ")
    assert output[1] == "子の完了報告"
    assert "--model=sonnet" in calls[0]
    assert "--effort=high" in calls[0]
    assert calls[0][-1].startswith('<atk-auto source="atk-commit" kind="commit-request">')
    assert 'source="agent-toolkit/atk-commit"' not in calls[0][-1]
    assert (
        original_run(
            ["git", "-C", str(repository), "log", "-1", "--format=%s"], capture_output=True, text=True, check=True
        ).stdout.strip()
        == "test: 変更を保存する"
    )


def test_dry_run_passes_forwarded_prompt_without_committing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _repo(tmp_path)
    monkeypatch.chdir(repository)
    monkeypatch.setattr(commit, "_executable", lambda _engine: "/fake/codex")
    original_run = subprocess.run
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[0] == "/fake/codex":
            calls.append(command)
            proposal = "test: 提案された件名\n"
            if kwargs.get("stdout") != subprocess.PIPE:
                print(proposal, end="")
                proposal = ""
            return subprocess.CompletedProcess(command, 0, stdout=proposal)
        return original_run(command, check=kwargs.pop("check", False), **kwargs)

    monkeypatch.setattr(commit.subprocess, "run", run)
    with pytest.raises(SystemExit, match="0"):
        atk.main(["commit", "--dry-run", "--model-type", "codex:gpt-6-sol/high", "背景を説明して"])

    assert len(calls) == 1
    output = capsys.readouterr().out.splitlines()
    assert output[0].startswith("成功: ")
    assert output[1] == "test: 提案された件名"
    assert calls[0][:6] == ["/fake/codex", "exec", "--model", "gpt-6-sol", "-c", "model_reasoning_effort=high"]
    assert "--dangerously-bypass-approvals-and-sandbox" in calls[0]
    assert "forwarded-user-input" in calls[0][-1]
    assert "背景を説明して" in calls[0][-1]
    assert "コミットはしない" in calls[0][-1]
    assert original_run(["git", "-C", str(repository), "rev-parse", "HEAD"], capture_output=True, check=False).returncode != 0


def test_format_instructions_follow_repository_priority(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    template = tmp_path / "template.txt"
    template.write_text("設定の形式\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "config", "commit.template", str(template)], check=True)

    assert commit._format_instructions(repository) == "設定の形式\n"  # pylint: disable=protected-access
    (repository / ".gitmessage").write_text("作業ツリーの形式\n", encoding="utf-8")
    assert commit._format_instructions(repository) == "作業ツリーの形式\n"  # pylint: disable=protected-access
    (repository / ".gitmessage").unlink()
    subprocess.run(["git", "-C", str(repository), "config", "--unset", "commit.template"], check=True)
    assert commit._format_instructions(repository) == commit._DEFAULT_FORMAT  # pylint: disable=protected-access


def test_default_model_type_reaches_candidate_resolver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repo(tmp_path)
    monkeypatch.chdir(repository)
    received: list[str] = []

    def resolve(model_type: str) -> list[tuple[str, str, str]]:
        received.append(model_type)
        return []

    monkeypatch.setattr(commit.config, "resolve_model_candidates", resolve)
    with pytest.raises(SystemExit, match="1"):
        atk.main(["commit"])

    assert received == ["medium_tier"]


@pytest.mark.parametrize(
    ("first_engine", "change_state", "expected_calls"),
    [
        ("agy", False, ["codex"]),
        ("claude", False, ["claude", "codex"]),
        ("claude", True, ["claude"]),
    ],
)
def test_candidates_skip_only_before_git_state_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_engine: str,
    change_state: bool,
    expected_calls: list[str],
) -> None:
    repository = _repo(tmp_path)
    monkeypatch.chdir(repository)
    candidates = [(first_engine, "first", "medium"), ("codex", "second", "medium")]
    monkeypatch.setattr(commit.config, "resolve_model_candidates", lambda _value: candidates)
    monkeypatch.setattr(commit, "_executable", lambda engine: f"/fake/{engine}")
    original_run = subprocess.run
    calls: list[str] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[0].startswith("/fake/"):
            engine = command[0].removeprefix("/fake/")
            calls.append(engine)
            if engine == "claude":
                if change_state:
                    (repository / "other.txt").write_text("途中変更\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 9)
            return subprocess.CompletedProcess(command, 0)
        return original_run(command, check=kwargs.pop("check", False), **kwargs)

    monkeypatch.setattr(commit.subprocess, "run", run)
    assert commit.run(atk._build_parser().parse_args(["commit", "--model-type", "medium_tier"])) == (9 if change_state else 0)  # pylint: disable=protected-access
    assert calls == expected_calls


def test_missing_executable_uses_next_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repo(tmp_path)
    monkeypatch.chdir(repository)
    monkeypatch.setattr(
        commit.config,
        "resolve_model_candidates",
        lambda _value: [("claude", "first", "medium"), ("codex", "second", "medium")],
    )
    monkeypatch.setattr(commit, "_executable", lambda engine: None if engine == "claude" else "/fake/codex")
    original_run = subprocess.run
    calls: list[str] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[0] == "/fake/codex":
            calls.append("codex")
            return subprocess.CompletedProcess(command, 0)
        return original_run(command, check=kwargs.pop("check", False), **kwargs)

    monkeypatch.setattr(commit.subprocess, "run", run)
    assert commit.run(atk._build_parser().parse_args(["commit"])) == 0  # pylint: disable=protected-access
    assert calls == ["codex"]


def _next_action_line(stderr: str) -> str:
    """失敗行の直後に続く次の操作の行を返す。"""
    lines = stderr.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("失敗: "))
    assert lines[index + 1].startswith("次の操作: ")
    return "\n".join(lines[index + 1 :])


def test_no_changes_reports_that_commit_is_unnecessary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """変更が無い場合はcommitが不要であることと再実行の条件を案内する。"""
    repository = _repo(tmp_path)
    (repository / "change.txt").unlink()
    monkeypatch.chdir(repository)

    assert commit.run(atk._build_parser().parse_args(["commit"])) == 1  # pylint: disable=protected-access
    assert "commitは不要" in _next_action_line(capsys.readouterr().err)


def test_start_failure_names_git_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """commitを開始できない失敗は`git status`での状態確認を案内する。"""
    repository = _repo(tmp_path)
    monkeypatch.chdir(repository)

    def fail(_value: str) -> list[tuple[str, str, str]]:
        raise OSError("読み取り失敗")

    monkeypatch.setattr(commit.config, "resolve_model_candidates", fail)
    assert commit.run(atk._build_parser().parse_args(["commit"])) == 2  # pylint: disable=protected-access
    assert "`git status`" in _next_action_line(capsys.readouterr().err)


@pytest.mark.parametrize(
    ("change_state", "expected_operation"),
    [(True, "`git status`"), (False, "atk config set")],
)
def test_final_failure_names_recovery_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    change_state: bool,
    expected_operation: str,
) -> None:
    """状態を変えた失敗は状態の確認を、候補の枯渇は候補の追加を案内する。"""
    repository = _repo(tmp_path)
    monkeypatch.chdir(repository)
    monkeypatch.setattr(commit.config, "resolve_model_candidates", lambda _value: [("claude", "first", "medium")])
    monkeypatch.setattr(commit, "_executable", lambda engine: f"/fake/{engine}")
    original_run = subprocess.run

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[0].startswith("/fake/"):
            if change_state:
                (repository / "other.txt").write_text("途中変更\n", encoding="utf-8")
            diagnostic = "子の失敗診断\n"
            if kwargs.get("stdout") != subprocess.PIPE:
                print(diagnostic, end="")
                diagnostic = ""
            return subprocess.CompletedProcess(command, 9, stdout=diagnostic)
        return original_run(command, check=kwargs.pop("check", False), **kwargs)

    monkeypatch.setattr(commit.subprocess, "run", run)
    commit.run(atk._build_parser().parse_args(["commit"]))  # pylint: disable=protected-access
    captured = capsys.readouterr()
    assert not captured.out
    assert "子の失敗診断" in captured.err
    assert expected_operation in _next_action_line(captured.err)
