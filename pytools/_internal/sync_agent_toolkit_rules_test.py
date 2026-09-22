"""pytools._internal.sync_agent_toolkit_rules のテスト。"""

import logging
from pathlib import Path

import pytest

from pytools._internal import claude_common, sync_agent_toolkit_rules


@pytest.fixture(name="env")
def env_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """配布元と2つの配布先を一時ディレクトリ配下へ振り向ける。"""
    dotfiles_root = tmp_path / "dotfiles"
    claude_home = tmp_path / "home" / ".claude"
    codex_home = tmp_path / "home" / ".codex"
    monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: dotfiles_root)
    monkeypatch.setattr(claude_common, "CLAUDE_HOME", claude_home)
    monkeypatch.setattr(sync_agent_toolkit_rules, "CODEX_HOME", codex_home)
    return dotfiles_root, claude_home, codex_home


def _src_dir(dotfiles_root: Path) -> Path:
    return dotfiles_root / "agent-toolkit" / "rules"


def _dst_dirs(claude_home: Path, codex_home: Path) -> tuple[Path, Path]:
    return claude_home / "rules" / "agent-toolkit", codex_home / "agent-toolkit" / "rules"


def _expected(name: str, body: str) -> str:
    opening = (
        f'<{sync_agent_toolkit_rules.NORMATIVE_ELEMENT} source="{sync_agent_toolkit_rules.NORMATIVE_SOURCE}"'
        f' kind="{sync_agent_toolkit_rules.NORMATIVE_KIND}" path="agent-toolkit/rules/{name}">'
    )
    return f"{opening}\n{body}\n</{sync_agent_toolkit_rules.NORMATIVE_ELEMENT}>\n"


class TestRun:
    """`sync_agent_toolkit_rules.run()`の動作検証。"""

    @pytest.mark.parametrize(
        ("scenario_id", "files"),
        [
            ("single_file", ["01-agent.md"]),
            # 単一ファイルと複数ファイルの一般的な同期契約を検証する。
            ("multiple_files", ["01-agent.md", "extra.md"]),
        ],
    )
    def test_writes_bounded_body_to_both_destinations(
        self,
        env: tuple[Path, Path, Path],
        scenario_id: str,
        files: list[str],
    ) -> None:
        """配布先が未存在でも、境界標識を付けた本文を両方の配布先へ書く。"""
        del scenario_id
        dotfiles_root, claude_home, codex_home = env
        src = _src_dir(dotfiles_root)
        src.mkdir(parents=True)
        for name in files:
            (src / name).write_text(f"{name}-body\n", encoding="utf-8")

        assert sync_agent_toolkit_rules.run() is True

        for dst in _dst_dirs(claude_home, codex_home):
            for name in files:
                assert (dst / name).read_text(encoding="utf-8") == _expected(name, f"{name}-body")

    def test_second_run_leaves_destinations_unchanged(self, env: tuple[Path, Path, Path]) -> None:
        """同じ配布元に対する2回目の同期は配布先を変更しない。"""
        dotfiles_root, claude_home, codex_home = env
        src = _src_dir(dotfiles_root)
        src.mkdir(parents=True)
        (src / "01-agent.md").write_text("body\n", encoding="utf-8")

        assert sync_agent_toolkit_rules.run() is True
        assert sync_agent_toolkit_rules.run() is False

        for dst in _dst_dirs(claude_home, codex_home):
            assert (dst / "01-agent.md").read_text(encoding="utf-8") == _expected("01-agent.md", "body")

    def test_replaces_legacy_link_destination(self, env: tuple[Path, Path, Path]) -> None:
        """リンクで配っていた配布先を、本文を書き換えるディレクトリへ置き換える。"""
        dotfiles_root, _claude_home, codex_home = env
        src = _src_dir(dotfiles_root)
        src.mkdir(parents=True)
        (src / "01-agent.md").write_text("body\n", encoding="utf-8")
        codex_rules = codex_home / "agent-toolkit" / "rules"
        codex_rules.parent.mkdir(parents=True)
        codex_rules.symlink_to(src, target_is_directory=True)

        assert sync_agent_toolkit_rules.run() is True

        assert not codex_rules.is_symlink()
        assert (codex_rules / "01-agent.md").read_text(encoding="utf-8") == _expected("01-agent.md", "body")
        assert (src / "01-agent.md").read_text(encoding="utf-8") == "body\n"

    def test_deletes_surplus_files(self, env: tuple[Path, Path, Path]) -> None:
        """配布元に無いファイルを配布先から削除する。"""
        dotfiles_root, claude_home, codex_home = env
        src = _src_dir(dotfiles_root)
        src.mkdir(parents=True)
        (src / "01-agent.md").write_text("a\n", encoding="utf-8")
        for dst in _dst_dirs(claude_home, codex_home):
            dst.mkdir(parents=True)
            (dst / "stale.md").write_text("x", encoding="utf-8")

        assert sync_agent_toolkit_rules.run() is True

        for dst in _dst_dirs(claude_home, codex_home):
            assert not (dst / "stale.md").exists()

    def test_warns_when_src_missing(
        self,
        env: tuple[Path, Path, Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """配布元が未存在の場合は警告だけを記録し、配布先を変更しない。"""
        _dotfiles_root, claude_home, codex_home = env

        with caplog.at_level(logging.WARNING):
            assert sync_agent_toolkit_rules.run() is True

        assert any("コピー元が存在しません" in record.message for record in caplog.records)
        for dst in _dst_dirs(claude_home, codex_home):
            assert not dst.exists()

    def test_returns_false_when_dotfiles_root_unresolved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`find_dotfiles_root()`が`None`を返すときは何もせずFalseを返す。"""
        monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: None)
        monkeypatch.setattr(claude_common, "CLAUDE_HOME", tmp_path / "home" / ".claude")

        assert sync_agent_toolkit_rules.run() is False
