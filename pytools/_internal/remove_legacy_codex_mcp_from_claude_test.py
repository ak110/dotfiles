"""旧Codex User scope MCP移行のテスト。"""

import json
import pathlib

import pytest

from pytools._internal import claude_common
from pytools._internal import remove_legacy_codex_mcp_from_claude as subject

from ._test_helpers import _FakeResult


def _write(path: pathlib.Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_is_legacy_definition_accepts_old_variants() -> None:
    assert subject.is_legacy_definition({"command": "codex", "args": ["mcp-server"]})
    assert subject.is_legacy_definition({"type": "stdio", "command": "codex", "args": ["mcp-server"], "timeout": 7_200_000})
    # 旧installerが使う`claude mcp add`は`-e`未指定でも空dictの`env`を書き込む。
    assert subject.is_legacy_definition({"type": "stdio", "command": "codex", "args": ["mcp-server"], "env": {}})


@pytest.mark.parametrize(
    "value",
    [
        {"command": "other", "args": ["mcp-server"]},
        {"command": "codex", "args": ["mcp-server", "--extra"]},
        {"type": "sse", "command": "codex", "args": ["mcp-server"]},
        {"command": "codex", "args": ["mcp-server"], "timeout": 1},
        {"command": "codex", "args": ["mcp-server"], "env": {"X": "1"}},
        {"command": "codex", "args": ["mcp-server"], "customField": True},
    ],
)
def test_is_legacy_definition_preserves_custom_definition(value: dict[str, object]) -> None:
    assert not subject.is_legacy_definition(value)


def test_run_removes_only_exact_user_definition(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    path = tmp_path / ".claude.json"
    # 旧installerの`claude mcp add`が生成する形をそのまま入力にする。
    _write(path, {"mcpServers": {"codex": {"type": "stdio", "command": "codex", "args": ["mcp-server"], "env": {}}}})
    monkeypatch.setattr(subject, "_CLAUDE_CONFIG_PATH", path)
    monkeypatch.setattr(subject.shutil, "which", lambda _name: "/usr/bin/claude")
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
    monkeypatch.setattr(subject.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(claude_common, "run_claude", lambda *_args, **_kwargs: pytest.fail("削除してはいけない"))
    assert subject.run() is False


def test_run_skips_without_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject.shutil, "which", lambda _name: None)
    assert subject.run() is False
