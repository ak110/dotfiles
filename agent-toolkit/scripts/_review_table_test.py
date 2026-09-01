"""レビュー指摘管理表の7列TSV操作を検証する。"""

import argparse
import json
import pathlib
import subprocess
from concurrent.futures import ThreadPoolExecutor

import _review_table as table
import pytest

_TRACK = "implementation-review"


def _git(repo: pathlib.Path, *args: str) -> str:
    """テスト用リポジトリでGitを実行し、標準出力を返す。"""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atk")
    subcommands = parser.add_subparsers(dest="command")
    table.build_parser(subcommands)
    return parser


def test_init_add_and_raw_show(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "review.tsv"
    assert table.init(path) == 0
    assert table.add(path, "1", _TRACK, "module.py:10", "修正が必要") == 0
    assert table.add(path, "1", _TRACK, "README.md", "説明が不足") == 0
    output = capsys.readouterr().out
    assert "追加成功" in output
    lines = path.read_text(encoding="utf-8").splitlines()
    assert all(len(line.split("\t")) == 7 for line in lines)
    assert all(isinstance(json.loads(cell), str) for line in lines for cell in line.split("\t"))
    assert table.show(path) == 0
    assert capsys.readouterr().out == path.read_text(encoding="utf-8")


def test_show_can_filter_by_track(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "統合後指摘")
    table.add(path, "1", "independent", "module.py:10", "盲検指摘")
    capsys.readouterr()

    assert table.show(path, track=_TRACK) == 0
    output = capsys.readouterr().out
    assert "統合後指摘" in output
    assert "盲検指摘" not in output


def test_table_lock_is_kept_as_the_sibling_management_artifact(tmp_path: pathlib.Path) -> None:
    """表の排他制御に使う対応ロック成果物を管理領域の検収対象として確認する。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "修正が必要")

    lock_path = path.with_name(path.name + ".lock")
    assert lock_path.is_file()


def test_table_lock_is_ignored_in_plan_repository(tmp_path: pathlib.Path) -> None:
    """計画配下へレビュー表ロックを作成する前にGit除外を保証する。"""
    _git(tmp_path, "init", "-q")
    path = tmp_path / "plans" / "2026" / "09" / "sample.plan-review.tsv"

    table.init(path)

    lock_path = path.with_name(path.name + ".lock")
    assert _git(tmp_path, "check-ignore", str(lock_path.relative_to(tmp_path))).strip() == str(lock_path.relative_to(tmp_path))


def test_empty_review_table_strictly_validates_after_exclusive_initialization(tmp_path: pathlib.Path) -> None:
    """指摘が無い初回ラウンドでも、初期化・strict検証・完了検収を成立させる。"""
    path = tmp_path / "review.tsv"
    assert table.init(path) == 0
    assert table.validate(path) == 0
    assert path.read_text(encoding="utf-8") == ""
    assert path.with_name(path.name + ".lock").is_file()


def test_same_composite_key_can_be_represented_again_in_a_later_round(tmp_path: pathlib.Path) -> None:
    """未解消指摘を同じ表の別ラウンドへ再提示しても、先頭4列の複合キー検証を壊さない。"""
    key = ("module.py:10", "同じ契約違反")
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, *key)
    table.add(path, "2", _TRACK, *key)
    assert table.validate(path, require_responses=False) == 0

    table.respond(path, "1", _TRACK, *key, "yes", "初回修正を追加", "")
    table.respond(path, "2", _TRACK, *key, "yes", "残る違反を再修正", "")
    assert table.validate(path) == 0


def test_same_issue_can_be_separated_by_track_and_responded_individually(tmp_path: pathlib.Path) -> None:
    """同じラウンド・指摘でもtrackが異なれば別行として応答を分離する。"""
    key = ("module.py:10", "同じ事象")
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", "plan-conformance", *key)
    table.add(path, "1", "independent", *key)

    with pytest.raises(ValueError, match="一意に解決できない: 2件") as exc_info:
        table.respond(path, "1", "", *key, "yes", "修正", "")
    message = str(exc_info.value)
    assert "track=plan-conformance" in message
    assert "track=independent" in message

    table.respond(path, "1", "plan-conformance", *key, "yes", "準拠系で修正", "")
    table.respond(path, "1", "independent", *key, "no", "", "独立した根拠を維持")
    assert table.validate(path) == 0
    rows = [[json.loads(cell) for cell in line.split("\t")] for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row for row in rows if row[1] == "plan-conformance"][0][5] == "準拠系で修正"
    assert [row for row in rows if row[1] == "independent"][0][6] == "独立した根拠を維持"


def test_initial_review_can_validate_structure_before_response_and_strict_after_response(
    tmp_path: pathlib.Path,
) -> None:
    """初回レビューと応答中は構造を、全件応答後は厳格な検証を使う。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "修正が必要")
    table.add(path, "1", _TRACK, "README.md", "説明が不足")

    assert table.validate(path, require_responses=False) == 0
    with pytest.raises(ValueError, match="対応要否が未回答"):
        table.validate(path)

    table.respond(path, "1", _TRACK, "module.py:10", "修正が必要", "yes", "条件を追加した", "")
    assert table.validate(path, require_responses=False) == 0
    table.respond(path, "1", _TRACK, "README.md", "説明が不足", "no", "", "既存契約を維持する")
    assert table.validate(path) == 0


def test_respond_updates_only_reviewee_columns(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "修正が必要")
    table.respond(path, "1", _TRACK, "module.py:10", "修正が必要", "yes", "条件を追加した", "")
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["1", _TRACK, "module.py:10", "修正が必要", "yes", "条件を追加した", ""]
    table.validate(path)


def test_respond_rejects_no_response_reason_when_response_needed_yes(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "修正が必要")
    with pytest.raises(ValueError, match="no-response-reason"):
        table.respond(path, "1", _TRACK, "module.py:10", "修正が必要", "yes", "条件を追加した", "無視")


def test_respond_no_requires_reason_and_clears_response(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "hook", "対象外")
    table.respond(path, "1", _TRACK, "hook", "対象外", "対応不要", "", "以前の契約を保持")
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["1", _TRACK, "hook", "対象外", "no", "", "以前の契約を保持"]
    table.validate(path)


def test_respond_rejects_response_when_response_needed_no(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "hook", "対象外")
    with pytest.raises(ValueError, match="response"):
        table.respond(path, "1", _TRACK, "hook", "対象外", "対応不要", "以前の契約を保持", "")


def test_duplicate_key_and_existing_init_are_rejected(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    with pytest.raises(ValueError, match="既に存在"):
        table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "同じ指摘")
    with pytest.raises(ValueError, match="重複"):
        table.add(path, "1", _TRACK, " module.py:10 ", "同じ指摘")


def test_start_gate_preserves_existing_table_and_initializes_only_missing_table(tmp_path: pathlib.Path) -> None:
    """開始ゲートは既存表を初期化せず構造検証し、未作成時だけ初期化する。"""
    existing = tmp_path / "existing-review.tsv"
    table.init(existing)
    table.add(existing, "1", _TRACK, "module.py:10", "既存の指摘")
    before = existing.read_text(encoding="utf-8")

    if existing.exists():
        assert table.validate(existing, require_responses=False) == 0
    else:
        assert table.init(existing) == 0
        assert table.validate(existing, require_responses=False) == 0
    assert existing.read_text(encoding="utf-8") == before

    missing = tmp_path / "missing-review.tsv"
    if missing.exists():
        assert table.validate(missing, require_responses=False) == 0
    else:
        assert table.init(missing) == 0
        assert table.validate(missing, require_responses=False) == 0


def test_add_requires_round(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    with pytest.raises(ValueError, match="先頭4列は空にできない"):
        table.add(path, "", _TRACK, "module.py:10", "指摘")


def test_validate_rejects_non_integer_round(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    cells = ("R1", _TRACK, "位置", "指摘", "yes", "内容", "")
    path.write_text(
        "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ラウンドが1以上の整数ではない"):
        table.validate(path)


def test_validate_rejects_invalid_track(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    cells = ("1", "invalid", "位置", "指摘", "yes", "内容", "")
    path.write_text(
        "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="trackの正規値集合"):
        table.validate(path)


def test_validate_rejects_unanswered_response(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    cells = ("1", _TRACK, "位置", "指摘", "", "", "")
    path.write_text(
        "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="対応要否が未回答"):
        table.validate(path)


def test_legacy_column_count_has_recovery_guidance_for_all_mutations(tmp_path: pathlib.Path) -> None:
    """旧形式の表を検出したとき、修復に必要な列構造とtrack値を全操作で示す。"""
    path = tmp_path / "review.tsv"
    cells = ("1", _TRACK, "重大", "位置", "指摘", "", "", "")
    path.write_text(
        "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n",
        encoding="utf-8",
    )
    for operation in (
        lambda: table.validate(path, require_responses=False),
        lambda: table.add(path, "1", _TRACK, "位置2", "追加"),
        lambda: table.respond(path, "1", _TRACK, "位置", "指摘", "yes", "修正", ""),
    ):
        before = path.read_text(encoding="utf-8")
        with pytest.raises(ValueError) as exc_info:
            operation()
        message = str(exc_info.value)
        assert path.read_text(encoding="utf-8") == before
        assert "期待列数は7" in message
        assert "trackの位置はroundの直後" in message
        assert "plan-review, implementation-review, plan-conformance, independent" in message
        assert "旧8列形式はseverity列を除いて7列形式で再作成する" in message


@pytest.mark.parametrize(
    "argv",
    (
        ["review-table", "add", "review.tsv", "--round=1", "--track=invalid", "位置", "指摘"],
        ["review-table", "respond", "review.tsv", "--track=invalid", "位置", "指摘", "--response-needed=yes"],
        ["review-table", "show", "review.tsv", "--track=invalid"],
    ),
)
def test_parser_rejects_invalid_track(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(argv)
    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("subcommand", "arguments", "accepted_options"),
    (
        ("show", ["review.tsv", "--round", "1"], ("--track",)),
        ("validate", ["review.tsv", "--round", "1"], ("--allow-unanswered",)),
        ("init", ["review.tsv", "--track", "plan-review"], ()),
        (
            "add",
            ["review.tsv", "--round=1", f"--track={_TRACK}", "位置", "指摘", "--file", "本文.txt"],
            ("--issue", "--issue-file", "--location", "--location-file", "--round", "--track"),
        ),
        ("show", ["review.tsv", "--all"], ("--track",)),
        (
            "add",
            ["review.tsv", "--round=1", f"--track={_TRACK}", "位置", "指摘", "余分"],
            ("--issue", "--issue-file", "--location", "--location-file", "--round", "--track"),
        ),
    ),
)
def test_parser_rejects_unsupported_options_with_guidance(
    subcommand: str,
    arguments: list[str],
    accepted_options: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["review-table", subcommand, *arguments])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert error.startswith(f"使い方: atk review-table {subcommand}")
    accepted = "・".join(accepted_options) if accepted_options else "なし"
    assert f"atk review-table {subcommand}が受理するオプションは{accepted}で、表のパスは位置引数で指定する" in error


@pytest.mark.parametrize(
    ("subcommand", "options"),
    (
        ("add", ("--location-file", "--issue-file")),
        ("respond", ("--location-file", "--issue-file", "--response-file", "--no-response-reason-file")),
    ),
)
def test_cell_file_options_are_shown_in_help(
    subcommand: str,
    options: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["review-table", subcommand, "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    for option in options:
        assert option in help_text


def test_cell_files_preserve_issue_and_supply_responses(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "review.tsv"
    location = "module.py:10 `handler`\n"
    issue = '"引用"と`backtick`を含む\n複数行の$本文\n'
    response = '  修正で"引用"と`backtick`を保持する\n'
    reason = "  $仕様の対象外\n"
    location_file = tmp_path / "location.txt"
    issue_file = tmp_path / "issue.txt"
    response_file = tmp_path / "response.txt"
    reason_file = tmp_path / "reason.txt"
    location_file.write_text(location, encoding="utf-8")
    issue_file.write_text(issue, encoding="utf-8")
    response_file.write_text(response, encoding="utf-8")
    reason_file.write_text(reason, encoding="utf-8")
    table.init(path)

    add_args = _parser().parse_args(
        [
            "review-table",
            "add",
            str(path),
            "--round=1",
            f"--track={_TRACK}",
            "--location-file",
            str(location_file),
            "--issue-file",
            str(issue_file),
        ]
    )
    assert table.dispatch(add_args) == 0
    assert table.show(path) == 0
    stored = [[json.loads(cell) for cell in line.split("\t")] for line in capsys.readouterr().out.splitlines()[-1:]][0]
    assert stored[:4] == ["1", _TRACK, location, issue]

    respond_args = _parser().parse_args(
        [
            "review-table",
            "respond",
            str(path),
            "--round=1",
            "--issue-file",
            str(issue_file),
            "--response-needed=yes",
            "--response-file",
            str(response_file),
        ]
    )
    assert table.dispatch(respond_args) == 0

    table.add(path, "2", _TRACK, "README.md", "対象外")
    no_response_args = _parser().parse_args(
        [
            "review-table",
            "respond",
            str(path),
            "--round=2",
            "--response-needed=no",
            "--no-response-reason-file",
            str(reason_file),
        ]
    )
    assert table.dispatch(no_response_args) == 0
    rows = [[json.loads(cell) for cell in line.split("\t")] for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0] == ["1", _TRACK, location, issue, "yes", response.strip(), ""]
    assert rows[1] == ["2", _TRACK, "README.md", "対象外", "no", "", reason.strip()]


def test_add_rejects_empty_issue_file_instead_of_using_positional_issue(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    issue_file = tmp_path / "issue.txt"
    issue_file.write_text("", encoding="utf-8")
    table.init(path)
    before = path.read_text(encoding="utf-8")
    args = _parser().parse_args(
        [
            "review-table",
            "add",
            str(path),
            "位置",
            "位置引数の指摘",
            "--round=1",
            f"--track={_TRACK}",
            "--issue-file",
            str(issue_file),
        ]
    )

    with pytest.raises(ValueError, match="--issueを指定する"):
        table.dispatch(args)
    assert path.read_text(encoding="utf-8") == before


def test_respond_uses_empty_issue_file_instead_of_positional_issue(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    issue_file = tmp_path / "issue.txt"
    issue_file.write_text("", encoding="utf-8")
    table.init(path)
    table.add(path, "1", _TRACK, "同じ位置", "指摘A")
    table.add(path, "1", _TRACK, "同じ位置", "指摘B")
    before = path.read_text(encoding="utf-8")
    args = _parser().parse_args(
        [
            "review-table",
            "respond",
            str(path),
            "同じ位置",
            "指摘A",
            "--issue-file",
            str(issue_file),
            "--response-needed=yes",
            "--response=対応した",
        ]
    )

    with pytest.raises(ValueError, match="一意に解決できない: 2件"):
        table.dispatch(args)
    assert path.read_text(encoding="utf-8") == before


def test_cell_text_and_file_options_are_mutually_exclusive(tmp_path: pathlib.Path) -> None:
    issue_file = tmp_path / "issue.txt"
    issue_file.write_text("指摘", encoding="utf-8")
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(
            [
                "review-table",
                "add",
                "review.tsv",
                "--round=1",
                f"--track={_TRACK}",
                "位置",
                "--issue=指摘",
                f"--issue-file={issue_file}",
            ]
        )
    assert exc_info.value.code == 2


def test_unreadable_cell_file_does_not_update_table(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    before = path.read_text(encoding="utf-8")
    args = _parser().parse_args(
        [
            "review-table",
            "add",
            str(path),
            "--round=1",
            f"--track={_TRACK}",
            "位置",
            "--issue-file",
            str(tmp_path / "missing.txt"),
        ]
    )

    with pytest.raises(ValueError, match="--issue-fileの読み込みに失敗した"):
        table.dispatch(args)
    assert path.read_text(encoding="utf-8") == before


def test_respond_resolves_by_partial_key(tmp_path: pathlib.Path) -> None:
    """`round`・`track`だけの指定でも一意に定まれば応答を更新できる。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "指摘A")
    table.add(path, "2", _TRACK, "README.md", "指摘B")
    assert table.respond(path, "1", _TRACK, "", "", "yes", "対応した", "") == 0
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["1", _TRACK, "module.py:10", "指摘A", "yes", "対応した", ""]


def test_respond_rejects_multiple_matches_and_keeps_table_unchanged(tmp_path: pathlib.Path) -> None:
    """部分キーが複数行へ一致する場合は拒否し、表を変更しない。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "指摘A")
    table.add(path, "2", _TRACK, "module.py:20", "指摘B")
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="一意に解決できない: 2件"):
        table.respond(path, "", _TRACK, "", "", "yes", "対応した", "")
    assert path.read_text(encoding="utf-8") == before


def test_respond_reports_decoded_candidates_when_no_partial_key_matches(tmp_path: pathlib.Path) -> None:
    """一致しない部分キーへ、復号済み候補行を示して再指定を可能にする。"""
    path = tmp_path / "review.tsv"
    issue = '本文に"引用"を含む指摘'
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", issue)
    encoded_issue = json.dumps(issue, ensure_ascii=False)

    with pytest.raises(ValueError) as exc_info:
        table.respond(path, "1", _TRACK, "module.py:10", encoded_issue, "yes", "対応した", "")

    message = str(exc_info.value)
    assert "一意に解決できない: 0件" in message
    assert "指定された部分キー:" in message
    assert f"issue={issue}" in message
    candidate_section = message.split("候補行（復号済み）:\n", maxsplit=1)[1]
    assert encoded_issue not in candidate_section

    assert table.respond(path, "1", _TRACK, "module.py:10", issue, "yes", "対応した", "") == 0
    assert table.validate(path) == 0


def test_concurrent_add_and_reordered_response_preserve_rows(tmp_path: pathlib.Path) -> None:
    """並列追加と並べ替え後の複合キー更新で行の欠落・重複・破損を生じさせない。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    entries = [("1", _TRACK, f"module.py:{index}", f"指摘{index}") for index in range(8)]

    def add_entry(entry: tuple[str, str, str, str]) -> int:
        return table.add(path, *entry)

    with ThreadPoolExecutor(max_workers=len(entries)) as executor:
        results = list(executor.map(add_entry, entries))

    assert results == [0] * len(entries)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(entries)
    path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")

    target = entries[3]
    assert table.respond(path, *target, "yes", "再現経路を追加", "") == 0
    for entry in entries:
        if entry != target:
            assert table.respond(path, *entry, "no", "", "既存契約を維持する") == 0
    assert table.validate(path) == 0
    rows = [[json.loads(cell) for cell in line.split("\t")] for line in path.read_text(encoding="utf-8").splitlines()]
    matching = [row for row in rows if row[:4] == list(target)]
    assert matching == [[*target, "yes", "再現経路を追加", ""]]
