"""`atk fb`共通の警告・通知処理を検証する。"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_fb_common as _common  # noqa: E402  # pylint: disable=wrong-import-position


def _write_tbd(
    private_notes: pathlib.Path,
    filename: str,
    *,
    target_repo: str = "github.com/example/repo",
    question: str = "確認事項",
    answer: str = "",
) -> None:
    """テスト用TBDをinboxへ書き込む。"""
    inbox = private_notes / "tbd" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / filename).write_text(
        f"---\ntarget_repo: {target_repo}\n---\n\n## 質問\n\n{question}\n\n## 回答\n\n{answer}",
        encoding="utf-8",
    )


class TestWarnSpaceSeparatedOption:
    """空白区切りオプションの検出条件を検証する。"""

    @pytest.mark.parametrize("subcommand", ["adopt", "reject", "tbd-adopt"])
    @pytest.mark.parametrize("option", ["--note", "--commit"])
    def test_warns_for_target_subcommands(
        self,
        subcommand: str,
        option: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象サブコマンドの空白区切り指定では推奨形式を警告する。"""
        _common.warn_space_separated_option(["fb", subcommand, "item.md", option, "value"])

        assert capsys.readouterr().err == f"警告: {option}は{option}=VALUE形式で渡すことを推奨します。\n"

    @pytest.mark.parametrize(
        "argv",
        [
            ["fb", "rm", "item.md", "--note", "value"],
            ["fb", "add", "/repo", "adopt", "--note", "value"],
            ["fb", "adopt", "item.md", "--note=value"],
            ["fb", "adopt", "item.md", "--note", "value=with-equals"],
            ["fb", "adopt", "item.md", "--note", "--target-repo=example/repo"],
        ],
    )
    def test_does_not_warn_for_excluded_forms(
        self,
        argv: list[str],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象外サブコマンド・等号形式・次オプションでは警告しない。"""
        _common.warn_space_separated_option(argv)

        assert not capsys.readouterr().err


class TestNotifyUnansweredTbdsIfAny:
    """未回答TBD通知の件数・フィルター・形式を検証する。"""

    def test_does_not_notify_without_unanswered_entries(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """TBDが0件または全件回答済みの場合は何も通知しない。"""
        _write_tbd(tmp_path, "answered.md", answer="回答済み")

        _common.notify_unanswered_tbds_if_any(tmp_path, None)

        assert not capsys.readouterr().err

    def test_notifies_one_unanswered_entry(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未回答TBDが1件の場合はヘッダと1行を通知する。"""
        _write_tbd(tmp_path, "one.md", question="最初の質問")

        _common.notify_unanswered_tbds_if_any(tmp_path, None)

        assert capsys.readouterr().err == "# tbd\none.md\tgithub.com/example/repo\t[unanswered] 最初の質問\n"

    def test_notifies_matching_unanswered_entries_in_filename_order(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数件では対象リポジトリの未回答項目だけをファイル名順で通知する。"""
        _write_tbd(tmp_path, "002.md", question="質問2")
        _write_tbd(tmp_path, "001.md", question="質問1")
        _write_tbd(tmp_path, "003.md", target_repo="github.com/example/other", question="対象外")

        _common.notify_unanswered_tbds_if_any(tmp_path, "github.com/example/repo")

        assert capsys.readouterr().err == (
            "# tbd\n001.md\tgithub.com/example/repo\t[unanswered] 質問1\n002.md\tgithub.com/example/repo\t[unanswered] 質問2\n"
        )
