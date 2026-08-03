"""`_tbd_scan.py`のTBD走査を検証する。"""

import pathlib
from collections.abc import Iterator

import _tbd_scan
import pytest

_REPO = "github.com/ak110/dotfiles"


def _entry(*, entry_type: str = "tbd", target_repo: str = _REPO, answer: str = "") -> str:
    """テスト用エントリ本文を返す。"""
    return (
        "---\n"
        f"target_repo: {target_repo}\n"
        f"type: {entry_type}\n"
        "---\n\n"
        "## 質問\n\n本文\n\n"
        "## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
        f"{answer}"
    )


class TestIsTbdAnswered:
    """回答節の有効な本文だけを回答として扱う。"""

    @pytest.mark.parametrize(
        "text",
        [
            "## 質問\n\n本文\n",
            "## 質問\n\n本文\n\n## 回答\n\n<!-- marker -->\n",
            "## 質問\n\n本文\n\n## 回答\n\n\n## 次節\n\n回答ではない本文\n",
        ],
    )
    def test_returns_false_without_answer_body(self, text: str) -> None:
        assert _tbd_scan.is_tbd_answered(text) is False

    def test_returns_true_with_answer_body(self) -> None:
        assert _tbd_scan.is_tbd_answered(_entry(answer="回答本文\n")) is True


class TestScanActiveTbds:
    """active状態かつ対象リポジトリのTBDだけを走査する。"""

    def test_filters_and_orders_entries(self, tmp_path: pathlib.Path) -> None:
        for state in ("inbox", "processing", "adopted", "rejected"):
            (tmp_path / state).mkdir()
        (tmp_path / "inbox" / "b.md").write_text(_entry(answer="回答\n"), encoding="utf-8")
        (tmp_path / "inbox" / "a.md").write_text(_entry(), encoding="utf-8")
        (tmp_path / "inbox" / "feedback.md").write_text(_entry(entry_type="feedback"), encoding="utf-8")
        (tmp_path / "processing" / "c.md").write_text(_entry(), encoding="utf-8")
        (tmp_path / "processing" / "other.md").write_text(_entry(target_repo="example.com/x/y"), encoding="utf-8")
        (tmp_path / "adopted" / "done.md").write_text(_entry(), encoding="utf-8")
        (tmp_path / "rejected" / "rejected.md").write_text(_entry(), encoding="utf-8")

        assert _tbd_scan.scan_active_tbds(tmp_path, _REPO) == _tbd_scan.ActiveTbdScan(
            entries=[
                _tbd_scan.ActiveTbd("a.md", False),
                _tbd_scan.ActiveTbd("b.md", True),
                _tbd_scan.ActiveTbd("c.md", False),
            ],
            complete=True,
        )

    def test_marks_unreadable_or_invalid_utf8_scan_incomplete(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        unreadable = inbox / "a.md"
        unreadable.write_text(_entry(), encoding="utf-8")
        (inbox / "b.md").write_bytes(b"\xff")
        valid = inbox / "c.md"
        valid.write_text(_entry(), encoding="utf-8")
        original_read_text = pathlib.Path.read_text

        def _read_text(
            path: pathlib.Path,
            encoding: str | None = None,
            errors: str | None = None,
        ) -> str:
            if path == unreadable:
                raise OSError("unreadable")
            return original_read_text(path, encoding=encoding, errors=errors)

        monkeypatch.setattr(pathlib.Path, "read_text", _read_text)
        assert _tbd_scan.scan_active_tbds(tmp_path, _REPO) == _tbd_scan.ActiveTbdScan(
            entries=[_tbd_scan.ActiveTbd("c.md", False)], complete=False
        )

    def test_marks_directory_iteration_failure_incomplete(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        original_iterdir = pathlib.Path.iterdir

        def _iterdir(path: pathlib.Path) -> Iterator[pathlib.Path]:
            if path == inbox:
                raise OSError("unreadable directory")
            return original_iterdir(path)

        monkeypatch.setattr(pathlib.Path, "iterdir", _iterdir)
        assert _tbd_scan.scan_active_tbds(tmp_path, _REPO) == _tbd_scan.ActiveTbdScan([], False)

    @pytest.mark.parametrize(
        "frontmatter",
        [
            f"target_repo: \"{_REPO}\" # repository\ntype: 'tbd' # entry kind",
            f"target_repo: {_REPO}\ntype: >-\n  tbd",
        ],
    )
    def test_uses_standard_yaml_forms(self, tmp_path: pathlib.Path, frontmatter: str) -> None:
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        text = f"---\n{frontmatter}\n---\n\n## 回答\n\n回答\n"
        (inbox / "entry.md").write_text(text, encoding="utf-8")
        assert _tbd_scan.scan_active_tbds(tmp_path, _REPO) == _tbd_scan.ActiveTbdScan(
            [_tbd_scan.ActiveTbd("entry.md", True)], True
        )

    @pytest.mark.parametrize("text", ["type: tbd\n", "---\ntype: tbd\n", "---\n: invalid\n---\n"])
    def test_marks_invalid_frontmatter_incomplete(self, tmp_path: pathlib.Path, text: str) -> None:
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "invalid.md").write_text(text, encoding="utf-8")
        assert _tbd_scan.scan_active_tbds(tmp_path, _REPO) == _tbd_scan.ActiveTbdScan([], False)


class TestPrivateNotesRoot:
    """環境変数指定と既定の保存先を解決する。"""

    def test_prefers_environment_override(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        override = tmp_path / "override"
        override.mkdir()
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(override))
        assert _tbd_scan.private_notes_root() == override

    def test_empty_override_uses_home_default(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "private-notes"
        root.mkdir()
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", "")
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
        assert _tbd_scan.private_notes_root() == root

    def test_returns_none_when_root_is_missing(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENT_TOOLKIT_PRIVATE_NOTES", raising=False)
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(_tbd_scan.platformdirs, "user_data_dir", lambda *_args, **_kwargs: tmp_path / "data")
        assert _tbd_scan.private_notes_root() is None

    def test_uses_platformdirs_fallback(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fallback = tmp_path / "data" / "private-notes"
        fallback.mkdir(parents=True)
        monkeypatch.delenv("AGENT_TOOLKIT_PRIVATE_NOTES", raising=False)
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path / "home")
        monkeypatch.setattr(_tbd_scan.platformdirs, "user_data_dir", lambda *_args, **_kwargs: tmp_path / "data")
        assert _tbd_scan.private_notes_root() == fallback

    def test_handles_nested_mapping_and_folded_values(self, tmp_path: pathlib.Path) -> None:
        """入れ子マッピング配下と折り返し値を正しく処理する。

        `queue_schedule:`配下のインデントされた`type: normal`と、
        `choices:`の折り返し継続行を含むfrontmatterを検証する。
        """
        (tmp_path / "inbox").mkdir()
        # 計画のテスト用frontmatterと同じ形式
        entry_text = (
            "---\n"
            "target_repo: github.com/ak110/dotfiles\n"
            "type: tbd\n"
            "queue_schedule:\n"
            "  type: normal\n"
            "  target_files:\n"
            "  - agent-toolkit/rules/01-agent.md\n"
            "choices: (a) 前半の選択肢,(b)\n"
            "  折り返した後半の選択肢\n"
            "---\n\n"
            "## 質問\n\n本文\n\n"
            "## 回答\n\n"
            "<!-- ユーザーはこの行以降に回答を追記する -->\n"
        )
        (tmp_path / "inbox" / "test.md").write_text(entry_text, encoding="utf-8")

        result = _tbd_scan.scan_active_tbds(tmp_path, "github.com/ak110/dotfiles")
        assert len(result.entries) == 1
        assert result.entries[0].filename == "test.md"
        assert result.entries[0].answered is False
        assert result.complete is True
