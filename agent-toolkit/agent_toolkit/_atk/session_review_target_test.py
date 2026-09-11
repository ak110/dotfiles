"""保存済みのセッション記録から振り返り対象を特定するコマンドを検証する。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess

import pytest

from agent_toolkit._atk import session_records as _session_records
from agent_toolkit._atk import session_review_target as target


def _repository(path: pathlib.Path, name: str) -> pathlib.Path:
    repository = path / name
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "remote", "add", "origin", f"https://github.com/ak110/{name}.git"],
        check=True,
    )
    return repository


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    target.build_parser(parser.add_subparsers(dest="command", required=True))
    return parser


def _arguments(repository: pathlib.Path, *identity: str) -> argparse.Namespace:
    return _parser().parse_args(["session-review-target", f"--target-repo={repository}", *identity])


def _prepare_homes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path]:
    claude_home = tmp_path / "claude"
    codex_home = tmp_path / "codex"
    monkeypatch.setattr(_session_records, "default_claude_home", lambda: claude_home)
    monkeypatch.setattr(_session_records, "default_codex_home", lambda: codex_home)
    return claude_home, codex_home


def _write_record(path: pathlib.Path, records: list[object], modified_at: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(value if isinstance(value, str) else json.dumps(value) for value in records)
    path.write_text(f"{content}\n", encoding="utf-8")
    os.utime(path, (modified_at, modified_at))


def _claude_record(cwd: pathlib.Path, *, entrypoint: str = "cli", timestamp: str | None = None) -> dict[str, object]:
    record: dict[str, object] = {"type": "user", "entrypoint": entrypoint, "cwd": str(cwd)}
    if timestamp is not None:
        record["timestamp"] = timestamp
    return record


def _codex_record(cwd: pathlib.Path, *, originator: str = "codex-tui") -> dict[str, object]:
    return {"type": "session_meta", "payload": {"originator": originator, "cwd": str(cwd)}}


def _claude_process_wi_record() -> dict[str, object]:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "process-wi",
                    "content": "Launching skill: agent-toolkit:process-wi",
                }
            ],
        },
    }


def _codex_process_wi_record() -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "/goal `agent-toolkit:process-wi`を完遂してください。",
                }
            ],
        },
    }


def _output(capsys: pytest.CaptureFixture[str]) -> dict[str, str] | None:
    output = capsys.readouterr()
    assert output.err == ""
    return json.loads(output.out) if output.out else None


def test_dispatch_returns_latest_main_session_across_engines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """本体以外、自身、別リポジトリ、対象外の深さと名前を除き、実行系をまたいで最新を返す。"""
    repository = _repository(tmp_path, "target")
    other_repository = _repository(tmp_path, "other")
    claude_home, codex_home = _prepare_homes(monkeypatch, tmp_path)
    project = claude_home / "projects" / "-target"
    day = codex_home / "sessions" / "2026" / "09" / "07"

    _write_record(project / "claude-main.jsonl", [_claude_record(repository), _claude_process_wi_record()], 10)
    _write_record(project / "current.jsonl", [_claude_record(repository, timestamp="1970-01-01T00:01:40Z")], 100)
    _write_record(project / "sdk.jsonl", [_claude_record(repository, entrypoint="sdk-cli")], 110)
    _write_record(project / "other.jsonl", [_claude_record(other_repository)], 90)
    _write_record(project / "malformed.jsonl", ["not-json"], 80)
    _write_record(project / "current" / "subagents" / "agent-child.jsonl", [_claude_record(repository)], 120)
    codex_main = day / "rollout-2026-09-07T00-00-00-00000000-0000-0000-0000-000000000001.jsonl"
    _write_record(codex_main, [_codex_record(repository), _codex_process_wi_record()], 70)
    for index, originator in enumerate(("agent-toolkit-codex-app-server", "codex_cli_rs", "codex_exec"), start=2):
        _write_record(
            day / f"rollout-2026-09-07T00-00-00-00000000-0000-0000-0000-00000000000{index}.jsonl",
            [_codex_record(repository, originator=originator)],
            100 + index,
        )
    _write_record(day / "record-00000000-0000-0000-0000-000000000009.jsonl", [_codex_record(repository)], 130)

    assert target.dispatch(_arguments(repository, "--transcript=/records/current.jsonl")) == 0

    assert _output(capsys) == {"engine": "codex", "session_id": "00000000-0000-0000-0000-000000000001"}


def test_dispatch_accepts_previous_day_and_skips_malformed_lines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """時間の下限を設けず、不正な行と候補を対象から外して古い本体を返す。"""
    repository = _repository(tmp_path, "target")
    claude_home, _codex_home = _prepare_homes(monkeypatch, tmp_path)
    project = claude_home / "projects" / "-target"
    _write_record(project / "malformed.jsonl", ["not-json"], 20)
    _write_record(project / "current.jsonl", [_claude_record(repository, timestamp="1970-01-01T00:00:20Z")], 20)
    _write_record(
        project / "previous.jsonl",
        ["not-json", _claude_record(repository), _claude_process_wi_record()],
        10,
    )

    assert target.dispatch(_arguments(repository, "--codex-thread-id=current")) == 0

    assert _output(capsys) == {"engine": "claude", "session_id": "previous"}


def test_dispatch_returns_error_when_current_session_record_does_not_exist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _repository(tmp_path, "target")
    _prepare_homes(monkeypatch, tmp_path)

    assert target.dispatch(_arguments(repository, "--codex-thread-id=current")) == 2

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "現在のセッションの開始時刻を解決できません: current\n"


def test_dispatch_resolves_each_cwd_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じcwdを持つ複数候補でもリポジトリ識別子は1回だけ解決する。"""
    repository = _repository(tmp_path, "target")
    other_repository = _repository(tmp_path, "other")
    claude_home, _codex_home = _prepare_homes(monkeypatch, tmp_path)
    project = claude_home / "projects" / "-target"
    _write_record(project / "current.jsonl", [_claude_record(repository, timestamp="1970-01-01T00:00:40Z")], 40)
    _write_record(project / "other-new.jsonl", [_claude_record(other_repository)], 30)
    _write_record(project / "other-old.jsonl", [_claude_record(other_repository)], 20)
    _write_record(project / "target.jsonl", [_claude_record(repository), _claude_process_wi_record()], 10)
    real_resolve = _session_records.resolve_repo_id
    resolved_cwds: list[pathlib.Path | None] = []

    def resolve(value: str | None, *, cwd: pathlib.Path | None = None) -> str:
        if value is None:
            resolved_cwds.append(cwd)
        return real_resolve(value, cwd=cwd)

    monkeypatch.setattr(_session_records, "resolve_repo_id", resolve)

    assert target.dispatch(_arguments(repository, "--codex-thread-id=current")) == 0

    assert _output(capsys) == {"engine": "claude", "session_id": "target"}
    assert resolved_cwds == [other_repository, repository]


def test_dispatch_skips_latest_session_without_process_wi_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じリポジトリの最新候補に起動標識が無ければ、標識を持つ古い候補を返す。"""
    repository = _repository(tmp_path, "target")
    claude_home, _codex_home = _prepare_homes(monkeypatch, tmp_path)
    project = claude_home / "projects" / "-target"
    _write_record(project / "current.jsonl", [_claude_record(repository, timestamp="1970-01-01T00:00:30Z")], 30)
    _write_record(project / "latest.jsonl", [_claude_record(repository)], 20)
    _write_record(
        project / "process-wi.jsonl",
        [_claude_record(repository), _claude_process_wi_record()],
        10,
    )

    assert target.dispatch(_arguments(repository, "--codex-thread-id=current")) == 0

    assert _output(capsys) == {"engine": "claude", "session_id": "process-wi"}


def test_dispatch_skips_session_updated_after_current_session_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """現在の開始以後に更新された稼働中候補を返さず、終了済み候補を選ぶ。"""
    repository = _repository(tmp_path, "target")
    claude_home, _codex_home = _prepare_homes(monkeypatch, tmp_path)
    project = claude_home / "projects" / "-target"
    _write_record(project / "current.jsonl", [_claude_record(repository, timestamp="1970-01-01T00:00:20Z")], 20)
    _write_record(
        project / "running.jsonl",
        [_claude_record(repository), _claude_process_wi_record()],
        30,
    )
    _write_record(
        project / "completed.jsonl",
        [_claude_record(repository), _claude_process_wi_record()],
        10,
    )

    assert target.dispatch(_arguments(repository, "--transcript=/records/current.jsonl")) == 0

    assert _output(capsys) == {"engine": "claude", "session_id": "completed"}


def test_dispatch_returns_error_when_current_session_start_is_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """現在の記録に解析可能なtimestampが無い場合は対象を返さず診断する。"""
    repository = _repository(tmp_path, "target")
    claude_home, _codex_home = _prepare_homes(monkeypatch, tmp_path)
    project = claude_home / "projects" / "-target"
    _write_record(project / "current.jsonl", [_claude_record(repository), {"timestamp": "invalid"}], 20)
    _write_record(
        project / "completed.jsonl",
        [_claude_record(repository), _claude_process_wi_record()],
        10,
    )

    assert target.dispatch(_arguments(repository, "--transcript=/records/current.jsonl")) == 2

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "現在のセッションの開始時刻を解決できません: current\n"


@pytest.mark.parametrize("value", ["", "nested/id", "nested\\id"])
def test_parser_rejects_invalid_codex_thread_id(value: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["session-review-target", f"--codex-thread-id={value}"])

    assert exc_info.value.code == 2


def test_parser_requires_exactly_one_current_session_identity() -> None:
    parser = _parser()
    with pytest.raises(SystemExit) as missing:
        parser.parse_args(["session-review-target"])
    with pytest.raises(SystemExit) as both:
        parser.parse_args(["session-review-target", "--transcript=/records/current.jsonl", "--codex-thread-id=current"])

    assert missing.value.code == 2
    assert both.value.code == 2
