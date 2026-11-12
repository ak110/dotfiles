"""review table parserの契約を検証する。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re

import markdown_it
import pytest

from agent_toolkit import atk
from agent_toolkit._atk import review_table as table
from agent_toolkit._common import next_action
from agent_toolkit._testing.normative_contract import read_contract
from agent_toolkit._testing.review_table_support import (
    _CLI_ACCEPTED_CASES,
    _TRACK,
    _assert_guides_series_head_name,
    _invoke_table_operation,
    _isolated_home,
    _parser,
)

__all__ = ["_isolated_home"]


@pytest.mark.parametrize("operation", ("validate", "show", "add", "respond"))
@pytest.mark.parametrize(
    "target_kind",
    (
        pytest.param("fifo", marks=pytest.mark.skipif(os.name != "posix", reason="FIFOはPOSIX固有")),
        "directory",
    ),
)
def test_non_regular_file_input_is_rejected_with_the_expected_input_form(
    tmp_path: pathlib.Path,
    operation: str,
    target_kind: str,
) -> None:
    """通常ファイルでない入力は、読み込みを試みずに期待する入力形と標準入力の非受理を示す。"""
    path = tmp_path / "sample.plan-review.tsv"
    if target_kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()

    with pytest.raises(next_action.ActionableError) as exc_info:
        _invoke_table_operation(operation, path)

    assert exc_info.value.reason == f"レビュー指摘管理表を読み込めない: {path}: 通常ファイルではない"
    assert ".exec-review.tsv" in exc_info.value.next_action
    assert "標準入力" in exc_info.value.next_action


@pytest.mark.parametrize("operation", ("validate", "show"))
def test_missing_path_is_rejected_with_the_expected_input_form(tmp_path: pathlib.Path, operation: str) -> None:
    """存在しないパスを読む操作は、対象が存在しないことと期待する入力形を示して拒否する。"""
    path = tmp_path / "sample.plan-review.tsv"

    with pytest.raises(next_action.ActionableError) as exc_info:
        _invoke_table_operation(operation, path)

    assert exc_info.value.reason == f"レビュー指摘管理表を読み込めない: {path}: 存在しない"
    assert ".exec-review.tsv" in exc_info.value.next_action
    assert "標準入力" in exc_info.value.next_action


def test_missing_review_table_guides_series_head_name(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """存在しない表を`atk review-table show`へ渡すと、次の操作が修正系列の開始時のHEAD由来の名前を案内する。"""
    missing = tmp_path / "missing.exec-review.tsv"

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "show", str(missing)], home=tmp_path)

    assert exc_info.value.code != 0
    error = capsys.readouterr().err
    _assert_guides_series_head_name(next(line for line in error.splitlines() if line.startswith("次の操作: ")))


def test_missing_path_stays_creatable_by_init_and_appendable_by_add(tmp_path: pathlib.Path) -> None:
    """存在しないパスに対する作成と追記が成功する場合を、読み込みの拒否と区別して維持する。"""
    created = tmp_path / "created.plan-review.tsv"
    assert table.init(created) == 0
    assert created.is_file()

    appended = tmp_path / "appended.plan-review.tsv"
    assert table.add(appended, "1", _TRACK, "位置", "指摘") == 0
    assert table.validate(appended, require_responses=False) == 0


@pytest.mark.parametrize("name", ("legacy.plan-review.tsv", f"dlg-{'a' * 40}.exec-review.tsv"))
def test_dispatch_rejects_removed_review_write_paths(tmp_path: pathlib.Path, name: str) -> None:
    """保存済み旧表と廃止した対話是正表は公開された作成コマンドで更新しない。"""
    args = argparse.Namespace(review_table_subcommand="init", path=str(tmp_path / name))

    with pytest.raises(ValueError, match="旧形式のレビュー指摘管理表は読み取り専用"):
        table.dispatch(args)


@pytest.mark.parametrize("track", ("plan-review", "implementation-review", "plan-conformance", "independent"))
def test_dispatch_rejects_writing_table_with_legacy_track(tmp_path: pathlib.Path, track: str) -> None:
    """現行拡張子の表でも旧trackを含む場合は書き換えない。"""
    path = tmp_path / "sample.exec-review.tsv"
    row = ["1", track, "場所", "指摘", "詳細", "", "", ""]
    path.write_text("\t".join(json.dumps(value, ensure_ascii=False) for value in row) + "\n", encoding="utf-8")
    original = path.read_bytes()
    args = argparse.Namespace(review_table_subcommand="respond", path=str(path))

    with pytest.raises(ValueError, match="旧review typeは読み取り専用"):
        table.dispatch(args)

    assert path.read_bytes() == original


def test_cli_rejects_writes_under_saved_plans_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """保存済み計画の領域の表へのinit・add・respondは表を変えずに失敗し、showは表示する。"""
    private_notes = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))
    saved_dir = private_notes / "plans" / "2026" / "09"
    saved_dir.mkdir(parents=True)
    saved = saved_dir / "28-example-1a2b.exec-review.tsv"
    assert table.add(saved, "1", _TRACK, "sample.py:1", "保存済みの指摘") == 0
    original = saved.read_bytes()
    missing = saved_dir / "28-other-3c4d.exec-review.tsv"
    location_file = tmp_path / "location.txt"
    location_file.write_text("sample.py:2", encoding="utf-8")
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("追加の指摘", encoding="utf-8")
    response_file = tmp_path / "response.md"
    response_file.write_text("対応した", encoding="utf-8")
    capsys.readouterr()

    for argv, checkout in (
        (["init", str(missing)], "2026/09/28-other-3c4d.md"),
        (
            ["add", str(saved), "--round=1", f"--track={_TRACK}", "--level=詳細"]
            + [f"--location-file={location_file}", f"--issue-file={issue_file}"],
            "2026/09/28-example-1a2b.md",
        ),
        (["respond", str(saved), "--row-id=1", f"--response-file={response_file}"], "2026/09/28-example-1a2b.md"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["review-table", *argv], home=tmp_path)
        assert exc_info.value.code == 1
        error = capsys.readouterr().err
        assert f"`atk plans checkout {checkout}`" in error
        assert f"`atk plans commit {checkout}`" in error

    assert saved.read_bytes() == original
    assert not missing.exists()
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "show", str(saved)], home=tmp_path)
    assert exc_info.value.code == 0
    assert "保存済みの指摘" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    (
        ["review-table", "add", "review.tsv", "--round=1", "--track=invalid", "--level=詳細", "位置", "指摘"],
        ["review-table", "respond", "review.tsv", "--track=invalid", "位置", "指摘", "--response-file=response.txt"],
        ["review-table", "show", "review.tsv", "--track=invalid"],
    ),
)
def test_parser_rejects_invalid_track(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(argv)
    assert exc_info.value.code == 2


@pytest.mark.parametrize(("argv", "expected"), _CLI_ACCEPTED_CASES)
def test_skill_documented_review_table_commands_are_accepted(
    argv: list[str],
    expected: dict[str, object],
) -> None:
    """スキルが指示するadd・respondの全形式を公開parserが受理する。"""
    args = _parser().parse_args(argv)

    assert vars(args) == expected


@pytest.mark.parametrize(
    "argv",
    (
        ["review-table", "add", "review.tsv", f"--track={_TRACK}", "--level=詳細", "位置", "指摘"],
        ["review-table", "add", "review.tsv", "--round=1", "--level=詳細", "位置", "指摘"],
        ["review-table", "add", "review.tsv", "--round=1", f"--track={_TRACK}", "位置", "指摘"],
        ["review-table", "add", "review.tsv", "--round=1", f"--track={_TRACK}", "--level=重大", "位置", "指摘"],
        [
            "review-table",
            "respond",
            "review.tsv",
            "--round=1",
            f"--track={_TRACK}",
            "--file=response.txt",
        ],
    ),
)
def test_skill_review_table_commands_reject_incomplete_or_unsupported_forms(argv: list[str]) -> None:
    """必須値の欠落、不正値および旧`--file`形式を終了コード2で拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(argv)
    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("relative_path", "marker", "subcommand"),
    (
        ("share/exec-review-criteria.md", "`atk review-table add", "add"),
        ("share/exec-review-responses.md", "応答の記録は、`atk review-table respond", "respond"),
    ),
)
def test_skill_review_table_options_match_accepted_cli_vectors(
    relative_path: str,
    marker: str,
    subcommand: str,
) -> None:
    """スキル本文のオプション集合を受理ケースの仕様集合へ固定する。"""
    skill_path = pathlib.Path(__file__).parents[2] / relative_path
    text = skill_path.read_text(encoding="utf-8")
    if subcommand == "respond":
        text = read_contract(
            skill_path, skill_path.parent.parent, ("exec-review-responses-records.md", "レビュー指摘管理表への応答")
        )
        text = text.split("\n## レビュー指摘管理表への応答\n", 1)[1].split("\n## ", 1)[0]
    assert marker in text
    # インラインコードの引数を読み取り、同じ節のshow例とフェンスの区切り記号を分ける。
    spans = [
        child.content
        for token in markdown_it.MarkdownIt().parse(text)
        for child in token.children or ()
        if child.type == "code_inline" and child.content.startswith(("--", f"atk review-table {subcommand} "))
    ]
    documented = {option for span in spans for option in re.findall(r"--[a-z-]+", span)}
    accepted = {
        token.split("=", maxsplit=1)[0]
        for argv, _expected in _CLI_ACCEPTED_CASES
        if argv[1] == subcommand
        for token in argv
        if token.startswith("--")
    }
    assert documented == accepted


@pytest.mark.parametrize(
    "arguments",
    (
        ["review.tsv", "--round=1", f"--track={_TRACK}", "位置", "指摘"],
        ["review.tsv", "--round=1", f"--track={_TRACK}", "--level=重大", "位置", "指摘"],
    ),
)
def test_add_parser_requires_canonical_level(arguments: list[str]) -> None:
    """addはlevelの省略と正規値以外の指定を拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["review-table", "add", *arguments])
    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("subcommand", "arguments", "accepted_options"),
    (
        ("validate", ["review.tsv", "--round", "1"], ("--allow-unanswered",)),
        ("init", ["review.tsv", "--track", "plan-review"], ()),
        (
            "add",
            ["review.tsv", "--round=1", f"--track={_TRACK}", "--level=詳細", "位置", "指摘", "--file", "本文.txt"],
            None,
        ),
        (
            "respond",
            ["review.tsv", "--file", "response.txt", "位置", "指摘"],
            (
                "--issue-file",
                "--location-file",
                "--no-response-reason-file",
                "--response-file",
                "--round",
                "--row-id",
                "--track",
            ),
        ),
        ("show", ["review.tsv", "--all"], ("--field", "--format", "--round", "--row-id", "--track")),
        (
            "add",
            ["review.tsv", "--round=1", f"--track={_TRACK}", "--level=詳細", "位置", "指摘", "余分"],
            None,
        ),
    ),
)
def test_parser_rejects_unsupported_options_with_guidance(
    subcommand: str,
    arguments: list[str],
    accepted_options: tuple[str, ...] | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["review-table", subcommand, *arguments])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert error.startswith(f"使い方: atk review-table {subcommand}")
    if accepted_options is None:
        assert "余った位置引数: 位置" in error
        assert f"atk review-table {subcommand}が受け取る位置引数はpath" in error
        assert "区切り記号" not in error
    else:
        accepted = "・".join(accepted_options) if accepted_options else "なし"
        assert f"atk review-table {subcommand}が受理するオプションは{accepted}" in error
