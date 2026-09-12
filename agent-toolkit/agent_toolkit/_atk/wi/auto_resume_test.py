"""agent-toolkit/agent_toolkit/_atk/wi/auto_resume.py のテスト。

候補表示・y応答・n応答・全拒否・候補なしの各終端を検証する。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from collections.abc import Sequence

import pytest

from agent_toolkit._atk import session_records
from agent_toolkit._atk.wi import auto_resume


def _repository(path: pathlib.Path, name: str) -> pathlib.Path:
    repository = path / name
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "remote", "add", "origin", f"https://github.com/ak110/{name}.git"],
        check=True,
    )
    return repository


def _write_record(path: pathlib.Path, records: Sequence[object], modified_at: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(value) for value in records)
    path.write_text(f"{content}\n", encoding="utf-8")
    os.utime(path, (modified_at, modified_at))


def _claude_process_wi_record(cwd: pathlib.Path) -> list[dict[str, object]]:
    return [
        {"type": "user", "entrypoint": "cli", "cwd": str(cwd)},
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "x", "content": "Launching skill: agent-toolkit:process-wi"}
                ],
            },
        },
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "最初の入力"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "最後の発言"}]}},
    ]


def _prepare_homes(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    claude_home = tmp_path / "claude"
    codex_home = tmp_path / "codex"
    monkeypatch.setattr(session_records, "default_claude_home", lambda: claude_home)
    monkeypatch.setattr(session_records, "default_codex_home", lambda: codex_home)
    return claude_home, codex_home


def _repo_id(repository: pathlib.Path) -> str:
    return session_records.resolve_repo_id(str(repository))


class TestSelectSession:
    def test_no_candidates_exits_nonzero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """候補が1件も存在しない場合は診断を表示して非0で終了する。"""
        repository = _repository(tmp_path, "target")
        _prepare_homes(monkeypatch, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            auto_resume.select_session(_repo_id(repository), repository)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert f"見つからない: {repository}" in err

    def test_displays_candidate_and_resumes_on_yes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """候補情報を表示し、y応答でsession_idを確定して返す。"""
        repository = _repository(tmp_path, "target")
        claude_home, _codex_home = _prepare_homes(monkeypatch, tmp_path)
        project = claude_home / "projects" / "-target"
        _write_record(project / "session-a.jsonl", _claude_process_wi_record(repository), 10)
        monkeypatch.setattr(auto_resume, "input", lambda _prompt: "y", raising=False)

        result = auto_resume.select_session(_repo_id(repository), repository)

        assert result == "session-a"
        out = capsys.readouterr().out
        assert "候補 1/1: engine=claude session_id=session-a" in out
        assert "exit-session到達: なし" in out
        assert "最初のユーザー入力: 最初の入力" in out
        assert "最後のエージェント発言: 最後の発言" in out

    def test_no_answer_moves_to_next_candidate(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """n応答で当該候補を破棄し、次に古い候補を提示する。"""
        repository = _repository(tmp_path, "target")
        claude_home, _codex_home = _prepare_homes(monkeypatch, tmp_path)
        project = claude_home / "projects" / "-target"
        _write_record(project / "newer.jsonl", _claude_process_wi_record(repository), 20)
        _write_record(project / "older.jsonl", _claude_process_wi_record(repository), 10)
        answers = iter(["n", "y"])
        monkeypatch.setattr(auto_resume, "input", lambda _prompt: next(answers), raising=False)

        result = auto_resume.select_session(_repo_id(repository), repository)

        assert result == "older"
        out = capsys.readouterr().out
        assert "候補 1/2: engine=claude session_id=newer" in out
        assert "候補 2/2: engine=claude session_id=older" in out

    def test_all_rejected_exits_nonzero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """全候補を拒否した場合は診断を表示して非0で終了する。"""
        repository = _repository(tmp_path, "target")
        claude_home, _codex_home = _prepare_homes(monkeypatch, tmp_path)
        project = claude_home / "projects" / "-target"
        _write_record(project / "session-a.jsonl", _claude_process_wi_record(repository), 10)
        monkeypatch.setattr(auto_resume, "input", lambda _prompt: "n", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            auto_resume.select_session(_repo_id(repository), repository)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "すべての候補が拒否された: 1件" in err

    def test_excludes_candidates_of_other_repository(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """対象と異なるリポジトリの候補は除外する。"""
        repository = _repository(tmp_path, "target")
        other_repository = _repository(tmp_path, "other")
        claude_home, _codex_home = _prepare_homes(monkeypatch, tmp_path)
        project = claude_home / "projects" / "-target"
        _write_record(project / "other.jsonl", _claude_process_wi_record(other_repository), 20)
        _write_record(project / "target.jsonl", _claude_process_wi_record(repository), 10)
        monkeypatch.setattr(auto_resume, "input", lambda _prompt: "y", raising=False)

        result = auto_resume.select_session(_repo_id(repository), repository)

        assert result == "target"
