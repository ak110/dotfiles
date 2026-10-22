"""Antigravity生成CLIの単独選択と全対象の同期を検証する。"""

from pathlib import Path

import pytest
import sync_antigravity_plugin as subject


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ([], ["agent-toolkit", "agent-basics"]),
        (["--plugin", "agent-basics"], ["agent-basics"]),
        (["--plugin", "agent-basics", "--plugin", "agent-basics"], ["agent-basics"]),
    ],
)
def test_cli_selects_independent_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str], expected: list[str]
) -> None:
    """同じ公開CLIで通常の両方配送とbasics単独配送を選べる。"""
    calls: list[tuple[Path, str]] = []

    def sync(root: Path, name: str, **_kwargs: object) -> bool:
        calls.append((root, name))
        return True

    monkeypatch.setattr(subject.common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(subject.antigravity_plugin, "sync", sync)
    assert subject.main(arguments) == 0
    assert calls == [(tmp_path, name) for name in expected]


def test_check_collects_both_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """checkは全選択対象を検査し、生成物を書き換えない。"""
    calls: list[str] = []

    def diagnostics(root: Path, name: str, **_kwargs: object) -> tuple[str, ...]:
        assert root == tmp_path
        calls.append(name)
        return ("basicsの差分",) if name == "agent-basics" else ()

    monkeypatch.setattr(subject.common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(subject.antigravity_plugin, "check_diagnostics", diagnostics)
    assert subject.main(["--check"]) == 1
    assert calls == ["agent-toolkit", "agent-basics"]
    assert "basicsの差分" in capsys.readouterr().err
