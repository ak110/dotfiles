"""旧Codex User scope MCP移行のテスト。"""

import json
import pathlib

import pytest

from pytools._internal import claude_common
from pytools._internal import remove_legacy_codex_mcp_from_claude as subject

from ._test_helpers import _FakeResult


def _write(path: pathlib.Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _legacy_codex_mcp_cases() -> list[object]:
    """単体インストーラー2本と共有する旧Codex MCP定義の判定ケースを返す。"""
    data = json.loads((pathlib.Path(__file__).parent / "legacy_codex_mcp_cases.json").read_text(encoding="utf-8"))
    return [pytest.param(case["definition"], case["legacy"], id=case["name"]) for case in data["cases"]]


@pytest.mark.parametrize(("value", "legacy"), _legacy_codex_mcp_cases())
def test_is_legacy_definition_matches_case_table(value: dict[str, object], legacy: bool) -> None:
    """旧installerが生成した定義だけを旧定義と判定し、ユーザーが変えた定義を保持対象にする。"""
    assert subject.is_legacy_definition(value) is legacy


def test_run_removes_only_exact_user_definition(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    path = tmp_path / ".claude.json"
    # 旧installerの`claude mcp add`が生成する形をそのまま入力にする。
    _write(path, {"mcpServers": {"codex": {"type": "stdio", "command": "codex", "args": ["mcp-server"], "env": {}}}})
    monkeypatch.setattr(subject, "_CLAUDE_CONFIG_PATH", path)
    monkeypatch.setattr(claude_common, "resolve_executable", lambda *_args, **_kwargs: pathlib.Path("claude"))
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> _FakeResult:
        calls.append(args)
        return _FakeResult(returncode=0)

    monkeypatch.setattr(claude_common, "run_claude", run)
    assert subject.run() is True
    assert calls == [["mcp", "remove", "--scope", "user", "codex"]]


def test_run_keeps_custom_definition(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    path = tmp_path / ".claude.json"
    _write(
        path,
        {"mcpServers": {"codex": {"type": "stdio", "command": "codex", "args": ["mcp-server"], "env": {"X": "1"}}}},
    )
    monkeypatch.setattr(subject, "_CLAUDE_CONFIG_PATH", path)
    monkeypatch.setattr(claude_common, "resolve_executable", lambda *_args, **_kwargs: pathlib.Path("claude"))
    monkeypatch.setattr(claude_common, "run_claude", lambda *_args, **_kwargs: pytest.fail("削除してはいけない"))
    assert subject.run() is False


def test_run_skips_without_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_common, "resolve_executable", lambda *_args, **_kwargs: None)
    assert subject.run() is False
