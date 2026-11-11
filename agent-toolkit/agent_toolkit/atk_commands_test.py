"""atk commandsの契約を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.atk_support import _assert_review_table_recovery_error, _isolate_agent_and_managed_temp_environment

__all__ = ["_isolate_agent_and_managed_temp_environment"]


def test_process_loop_abort_commands_report_and_transition_state(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """中断要求の設定・解除・参照コマンドが状態と標準出力を一致させる。"""
    state_file = tmp_path / "state" / "agent-toolkit" / "process-wi-abort"

    for command, expected in (
        ("status", "process-loopへの中断要求: なし\n保持中の追加指示: 0件\n"),
        ("abort-cancel", "成功: process-loopへの中断要求は設定されていないため、解除の変更は無い\n"),
        ("abort", "成功: process-loopへ中断を要求した\n"),
        ("status", "process-loopへの中断要求: あり\n保持中の追加指示: 0件\n"),
        ("abort-cancel", "成功: process-loopへの中断要求を解除した\n"),
        ("status", "process-loopへの中断要求: なし\n保持中の追加指示: 0件\n"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", command], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == expected

    assert not state_file.exists()
    assert not (tmp_path / "private-notes").exists()


def test_process_loop_instruction_commands_complete_storage_cycle(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIで次回指示を保持し、状態を確認して破棄する。"""
    for argv, expected in (
        (["instruct", "次の起動で確認する"], "成功: 次のセッションへ渡す追加指示を保持した。"),
        (["status"], "保持中の追加指示: 1件"),
        (["instruct-cancel"], "成功: 保持中の追加指示を1件破棄した"),
        (["status"], "保持中の追加指示: 0件"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", *argv], home=tmp_path)
        assert exc_info.value.code == 0
        assert expected in capsys.readouterr().out


def test_review_table_subcommands_are_public() -> None:
    """レビュー指摘管理表の5操作がトップレベルCLIへ登録されている。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    for subcommand in ("init", "add", "respond", "show", "validate"):
        argv = ["review-table", subcommand, "review.tsv"]
        if subcommand == "add":
            argv.extend(
                [
                    "--round=1",
                    "--track=exec-review",
                    "--level=詳細",
                    "--location-file=location.txt",
                    "--issue-file=issue.txt",
                ]
            )
        elif subcommand == "respond":
            argv.extend(
                [
                    "--track=exec-review",
                    "--location-file=location.txt",
                    "--issue-file=issue.txt",
                    "--response-file=response.txt",
                ]
            )
        args = parser.parse_args(argv)
        assert args.command == "review-table"
        assert args.review_table_subcommand == subcommand


def test_public_review_table_validate_rejects_unanswered_rows(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIは構造検証の明示指定を許容し、指定しない場合は未応答行を拒否する。"""
    path = tmp_path / "review.tsv"
    path.write_text(
        "\t".join(
            json.dumps(value, ensure_ascii=False)
            for value in ("1", "implementation-review", "位置", "指摘", "詳細", "", "", "")
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as structural_exc_info:
        atk.main(["review-table", "validate", "--allow-unanswered", str(path)])
    assert structural_exc_info.value.code == 0
    assert "成功: 構造を検証した" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "validate", str(path)])

    assert exc_info.value.code == 1
    assert "未応答" in capsys.readouterr().err


def test_public_review_table_show_accepts_compat_track(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIのshowは保存済み旧trackを受理してraw TSVを表示する。"""
    path = tmp_path / "review.tsv"
    raw = (
        "\t".join(
            json.dumps(value, ensure_ascii=False)
            for value in ("1", "implementation-review", "位置", "指摘", "詳細", "", "", "")
        )
        + "\n"
    )
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "show", str(path), "--track=implementation-review"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == f"1\t{raw}"


def test_public_review_table_validate_rejects_whitespace_around_stored_track(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIは表示時に選択不能となる空白付きtrackを構造検証で拒否する。"""
    path = tmp_path / "review.tsv"
    path.write_text(
        "\t".join(
            json.dumps(value, ensure_ascii=False) for value in ("1", " independent ", "位置", "指摘", "詳細", "yes", "修正", "")
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "validate", "--allow-unanswered", str(path)])

    assert exc_info.value.code == 1
    assert "trackが正規値ではない" in capsys.readouterr().err


def test_public_review_table_add_requires_track_and_shows_choices(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIは追加時のtrack省略をusageと正規値集合付きで拒否する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["review-table", "add", "review.tsv", "--round=1", "--level=詳細", "位置", "指摘"])
    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "--track" in error
    assert "exec-review" in error
    assert "plan-review" not in error
    assert "implementation-review" not in error
    assert "plan-conformance" not in error
    assert "independent" not in error


def test_public_review_table_invalid_column_count_error_explains_recovery(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開CLIが旧列数を検出したとき、列位置とtrack値を修復案内へ含める。"""
    path = tmp_path / "review.tsv"
    path.write_text(
        "\t".join(
            json.dumps(value, ensure_ascii=False)
            for value in ("1", "plan-conformance", "旧重大度", "位置", "指摘", "詳細", "", "", "")
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "validate", "--allow-unanswered", str(path)])
    assert exc_info.value.code == 1
    _assert_review_table_recovery_error(capsys.readouterr().err)


@pytest.mark.parametrize("subcommand", ["respond", "add"])
def test_public_review_table_mutations_reject_old_column_count_with_recovery(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    subcommand: str,
) -> None:
    """公開CLIの応答と追加も旧列数を拒否し、移行に必要な情報を示す。"""
    path = tmp_path / "review.tsv"
    path.write_text(
        "\t".join(
            json.dumps(value, ensure_ascii=False)
            for value in ("1", "plan-conformance", "旧重大度", "位置", "指摘", "詳細", "", "", "")
        )
        + "\n",
        encoding="utf-8",
    )
    location_file = tmp_path / "location.txt"
    issue_file = tmp_path / "issue.md"
    response_file = tmp_path / "response.md"
    location_file.write_text("位置", encoding="utf-8")
    issue_file.write_text("指摘", encoding="utf-8")
    response_file.write_text("修正", encoding="utf-8")
    argv = ["review-table", subcommand, str(path)]
    if subcommand == "respond":
        argv.extend(
            [
                "--track=exec-review",
                f"--location-file={location_file}",
                f"--issue-file={issue_file}",
                f"--response-file={response_file}",
            ]
        )
    else:
        argv.extend(
            [
                "--round=2",
                "--track=exec-review",
                "--level=詳細",
                f"--location-file={location_file}",
                f"--issue-file={issue_file}",
            ]
        )

    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv)

    assert exc_info.value.code == 1
    _assert_review_table_recovery_error(capsys.readouterr().err)
