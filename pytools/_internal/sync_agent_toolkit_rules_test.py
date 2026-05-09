"""pytools._internal.sync_agent_toolkit_rules のテスト。"""

import logging
import os
from pathlib import Path

import pytest

from pytools._internal import claude_common, sync_agent_toolkit_rules


@pytest.fixture(name="env")
def env_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """`find_dotfiles_root`と`CLAUDE_HOME`を一時ディレクトリ配下へ振り向ける。"""
    dotfiles_root = tmp_path / "dotfiles"
    home = tmp_path / "home" / ".claude"
    monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: dotfiles_root)
    monkeypatch.setattr(claude_common, "CLAUDE_HOME", home)
    return dotfiles_root, home


def _src_dir(dotfiles_root: Path) -> Path:
    return dotfiles_root / "agent-toolkit" / "rules"


def _dst_dir(home: Path) -> Path:
    return home / "rules" / "agent-toolkit"


class TestRun:
    """`sync_agent_toolkit_rules.run()`の動作検証。"""

    @pytest.mark.parametrize(
        ("scenario_id", "files"),
        [
            ("single_file", ["agent.md"]),
            ("multiple_files", ["agent.md", "styles.md"]),
        ],
    )
    def test_creates_dst_when_missing(
        self,
        env: tuple[Path, Path],
        scenario_id: str,
        files: list[str],
    ) -> None:
        """(a) dst未存在時はsrc配下の全ファイルが新規作成される。"""
        del scenario_id
        dotfiles_root, home = env
        src = _src_dir(dotfiles_root)
        src.mkdir(parents=True)
        for name in files:
            (src / name).write_text(f"{name}-body", encoding="utf-8")

        assert sync_agent_toolkit_rules.run() is True

        dst = _dst_dir(home)
        for name in files:
            assert (dst / name).read_text(encoding="utf-8") == f"{name}-body"

    @pytest.mark.parametrize(
        ("scenario_id", "mtime_offset_ns", "expected_content"),
        [
            # mtimeが一致している場合はsync()がst_mtime_ns比較で再コピーを抑止する。
            ("matching_mtime", 0, "destination-untouched"),
            # mtimeが異なる場合はsync()がshutil.copy2でsrcの内容と更新時刻に揃える。
            ("differing_mtime", -(10**9), "source-newer"),
        ],
    )
    def test_mtime_based_sync(
        self,
        env: tuple[Path, Path],
        scenario_id: str,
        mtime_offset_ns: int,
        expected_content: str,
    ) -> None:
        """(b)(c) dstのmtimeがsrcと一致すれば再コピーしない。
        異なる場合はsrcで上書きする。"""
        del scenario_id
        dotfiles_root, home = env
        src = _src_dir(dotfiles_root)
        src.mkdir(parents=True)
        (src / "agent.md").write_text("source-newer", encoding="utf-8")
        dst = _dst_dir(home)
        dst.mkdir(parents=True)
        (dst / "agent.md").write_text("destination-untouched", encoding="utf-8")
        st = (src / "agent.md").stat()
        os.utime(
            dst / "agent.md",
            ns=(st.st_atime_ns + mtime_offset_ns, st.st_mtime_ns + mtime_offset_ns),
        )

        assert sync_agent_toolkit_rules.run() is True

        assert (dst / "agent.md").read_text(encoding="utf-8") == expected_content

    def test_deletes_surplus_files(self, env: tuple[Path, Path]) -> None:
        """(d) dst側の余剰ファイルがdelete=Trueで削除される。"""
        dotfiles_root, home = env
        src = _src_dir(dotfiles_root)
        src.mkdir(parents=True)
        (src / "agent.md").write_text("a", encoding="utf-8")
        dst = _dst_dir(home)
        dst.mkdir(parents=True)
        (dst / "agent.md").write_text("a", encoding="utf-8")
        (dst / "stale.md").write_text("x", encoding="utf-8")

        assert sync_agent_toolkit_rules.run() is True

        assert not (dst / "stale.md").exists()

    def test_warns_when_src_missing(
        self,
        env: tuple[Path, Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """(e) src未存在時はwarningログのみで例外なし。"""
        _, home = env

        with caplog.at_level(logging.WARNING):
            assert sync_agent_toolkit_rules.run() is True

        assert any("コピー元が存在しません" in record.message for record in caplog.records)
        assert not _dst_dir(home).exists()

    def test_returns_false_when_dotfiles_root_unresolved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`find_dotfiles_root()`が`None`を返すときは何もせずFalseを返す。"""
        monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: None)
        monkeypatch.setattr(claude_common, "CLAUDE_HOME", tmp_path / "home" / ".claude")

        assert sync_agent_toolkit_rules.run() is False
