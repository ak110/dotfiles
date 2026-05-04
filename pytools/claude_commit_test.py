"""claude_commitモジュールのテスト。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from pytools.claude_commit import _DEFAULT_FORMAT, _build_prompt, _get_format_instructions


class TestBuildPrompt:
    """_build_prompt のテスト。"""

    def test_normal_commit(self) -> None:
        """通常コミット時のプロンプトを構築する。"""
        result = _build_prompt(
            git_root=Path("/tmp"),
            format_instructions="Conventional Commits形式",
            staged_stat="foo.py | 5 +++++",
            amend=False,
            head_message="",
            dry_run=False,
        )
        assert "ステージング済みの変更の概要" in result
        assert "foo.py | 5 +++++" in result
        assert "Conventional Commits形式" in result

    def test_normal_commit_with_unstaged_and_untracked(self) -> None:
        """未ステージ・未追跡の変更もプロンプトに含める。"""
        result = _build_prompt(
            git_root=Path("/tmp"),
            format_instructions="Conventional Commits形式",
            staged_stat="",
            unstaged_stat="foo.py | 3 +++",
            untracked_names=["new_file.py", "docs/new.md"],
            amend=False,
            head_message="",
            dry_run=False,
        )
        assert "未ステージの変更の概要" in result
        assert "foo.py | 3 +++" in result
        assert "未追跡ファイルの一覧" in result
        assert "new_file.py" in result
        assert "docs/new.md" in result
        assert "git add" in result

    def test_amend(self) -> None:
        """amendコミット時のプロンプトを構築する。"""
        result = _build_prompt(
            git_root=Path("/tmp"),
            format_instructions="Conventional Commits形式",
            staged_stat="",
            amend=True,
            head_message="feat: 既存メッセージ",
            dry_run=False,
        )
        assert "amend" in result
        assert "feat: 既存メッセージ" in result

    def test_amend_message_only(self) -> None:
        """amendで変更ゼロの場合はメッセージのみ書き直す旨を含む。"""
        result = _build_prompt(
            git_root=Path("/tmp"),
            format_instructions="Conventional Commits形式",
            staged_stat="",
            amend=True,
            head_message="feat: 既存メッセージ",
            dry_run=False,
        )
        assert "メッセージのみ" in result

    def test_dry_run(self) -> None:
        """dry_run時にコミットしない旨をプロンプトに含む。"""
        result = _build_prompt(
            git_root=Path("/tmp"),
            format_instructions="Conventional Commits形式",
            staged_stat="foo.py | 1 +",
            amend=False,
            head_message="",
            dry_run=True,
        )
        assert "コミットはしない" in result

    def test_amend_with_staged(self) -> None:
        """amendかつステージング済みの変更がある場合、全情報をプロンプトに含む。"""
        result = _build_prompt(
            git_root=Path("/tmp"),
            format_instructions="",
            staged_stat="baz.py | 2 ++",
            amend=True,
            head_message="old message",
            dry_run=False,
        )
        assert "baz.py | 2 ++" in result
        assert "old message" in result

    def test_lock_only_staged(self) -> None:
        """lock系ファイルのみのステージング時、概要をプロンプトに含む。"""
        result = _build_prompt(
            git_root=Path("/tmp"),
            format_instructions="Conventional Commits形式",
            staged_stat="uv.lock | 10 ++++------",
            amend=False,
            head_message="",
            dry_run=False,
        )
        assert "uv.lock | 10 ++++------" in result


class TestGetFormatInstructions:
    """_get_format_instructions のテスト。"""

    def test_gitmessage_takes_priority(self, tmp_path: Path) -> None:
        """リポジトリルートの.gitmessageが最優先で読まれる。"""
        gitmessage = tmp_path / ".gitmessage"
        gitmessage.write_text("カスタムフォーマット\n", encoding="utf-8")
        result = _get_format_instructions(git_root=tmp_path)
        assert result == "カスタムフォーマット\n"

    def test_default_format_when_no_config(self, tmp_path: Path) -> None:
        """.gitmessageも設定もない場合はデフォルト形式を返す。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = _get_format_instructions(git_root=tmp_path)
        assert result == _DEFAULT_FORMAT
