"""check_plan_file.pyの軽量機械チェックのテスト。"""

from __future__ import annotations

import collections.abc
import pathlib
import subprocess

import markdown_it
import pytest
from check_plan_file import main

_BUG_ROWS = tuple(
    """\
観測事象
期待する契約
直接的原因
混入要因
動機的要因
見逃し原因
根本原因
原因分析の根拠
類似見直しの観点
類似見直し結果
是正処置
横展開処置
再発防止処置
設計意図の記録
""".splitlines()
)
_FULL_COMMIT = "0123456789abcdef0123456789abcdef01234567"
# 既存ファイルの変更を含む計画で参照追従検査を充足させる`## 調査結果`節。
# 当該検査と無関係なテストのフィクスチャへ前置し、検査の発動条件を緩めずに意図を保つ。
_REFERENCE_ENUMERATION_SECTION = "## 調査結果\n\n- 参照追従対象: なし\n- 入力形態: 該当なし\n- 追従要否: 追従先なし\n\n"


def _write_plan(tmp_path: pathlib.Path, body: str, *, include_h1: bool = True) -> pathlib.Path:
    plan = tmp_path / "sample.md"
    content = body
    if include_h1 and not body.startswith("# "):
        content = f"# テスト計画\n\n{body}"
    plan.write_text(content, encoding="utf-8")
    return plan


def _init_git_repo(root: pathlib.Path) -> None:
    """識別子の走査対象をGitの管理下一覧から得るため、対象ディレクトリをリポジトリ化する。

    走査対象は`git ls-files --cached --others --exclude-standard`が返す一覧であり、
    リポジトリでないディレクトリは判定不能として扱われる。
    未追跡ファイルも`--others`で一覧へ入るため、コミットは作成しない。
    """
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)


def _bug_investigation_table(rows: collections.abc.Sequence[str]) -> str:
    body = ["### バグ調査結果", "", "| 項目 | 内容 |", "| --- | --- |"]
    body.extend(f"| {row} | 内容 |" for row in rows)
    return "\n".join(body)


def _pipe_table_lines(*, outer_pipes: bool) -> list[str]:
    """バグ調査結果表の行を外側パイプの有無に合わせて返す。"""
    lines = _bug_investigation_table(_BUG_ROWS).splitlines()[2:]
    if outer_pipes:
        return lines
    return [line.removeprefix("|").removesuffix("|").strip() for line in lines]


def _plan_metadata(work_type: str | None) -> str:
    body = [
        "### 計画メタ情報",
        "",
        f"- ベースコミット: `{_FULL_COMMIT}`",
        "- 実行系変更: なし",
    ]
    if work_type is not None:
        body.append(f"- 作業種別: {work_type}")
    return "\n".join(body)


def _execution_contract_table(*, columns: collections.abc.Sequence[str] | None = None) -> str:
    """実行契約表のフィクスチャを返す。"""
    headers = list(
        columns
        or (
            "変更単位",
            "入力",
            "起動契機",
            "実行主体",
            "SSOT",
            "出力形式と終了状態",
            "検証主体",
            "テスト対象",
        )
    )
    separator = ["---"] * len(headers)
    values = [f"値{index}" for index in range(1, len(headers) + 1)]
    return "\n".join(
        [
            "### 実行契約",
            "",
            f"| {' | '.join(headers)} |",
            f"| {' | '.join(separator)} |",
            f"| {' | '.join(values)} |",
        ]
    )


def _create_two_commit_repo(root: pathlib.Path, *, second_content: str = "after\n") -> str:
    """一時Gitリポジトリへ2コミットを作成し、基準コミットを返す。"""
    _init_git_repo(root)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    target = root / "actual.py"
    target.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "actual.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "--quiet", "-m", "基準コミット"], check=True)
    base_commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    target.write_text(second_content, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "actual.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "--quiet", "-m", "実際の件名"], check=True)
    return base_commit


def test_execution_change_declaration_is_required(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _write_plan(tmp_path, f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{_FULL_COMMIT}`\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "実行系変更の宣言が無い" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("body", "expected_count"),
    (
        ("## 背景\n\n背景本文\n", 0),
        (f"## 背景\n\n{_plan_metadata('通常変更')}\n\n{_plan_metadata('通常変更')}\n", 2),
        (f"## 背景\n\n背景本文\n\n## 別節\n\n{_plan_metadata('通常変更')}\n", 0),
    ),
)
def test_execution_change_declaration_requires_one_metadata_section_under_background(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    expected_count: int,
) -> None:
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert f"`## 背景`直下に`### 計画メタ情報`が1件必要: 実際={expected_count}件" in capsys.readouterr().err


def test_execution_change_declaration_rejects_invalid_value(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _write_plan(
        tmp_path,
        f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{_FULL_COMMIT}`\n- 実行系変更: 対象外\n",
    )
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "実行系変更の宣言が固定記法" in capsys.readouterr().err


def test_execution_contract_table_not_required_when_change_is_none(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _write_plan(
        tmp_path,
        f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{_FULL_COMMIT}`\n- 作業種別: 通常変更\n- 実行系変更: なし\n",
    )
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""


def test_execution_contract_table_required_when_change_exists(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _write_plan(tmp_path, f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{_FULL_COMMIT}`\n- 実行系変更: あり\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "`## 調査結果`直下に`### 実行契約`が1件必要" in capsys.readouterr().err


def test_execution_contract_table_rejects_missing_required_column(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = _execution_contract_table(
        columns=("変更単位", "入力", "起動契機", "実行主体", "SSOT", "出力形式と終了状態", "検証主体")
    )
    body = f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{_FULL_COMMIT}`\n- 実行系変更: あり\n\n## 調査結果\n\n{table}\n"
    plan = _write_plan(tmp_path, body)
    plan = plan.rename(tmp_path / "execution-contract.txt")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "実行契約表のヘッダーが必須8列と一致しない" in capsys.readouterr().err


def test_execution_contract_table_accepts_required_columns_and_data_row(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (
        f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{_FULL_COMMIT}`\n"
        "- 作業種別: 通常変更\n- 実行系変更: あり\n\n"
        f"## 調査結果\n\n{_execution_contract_table()}\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""


def test_base_commit_reports_target_file_set_mismatch(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    base_commit = _create_two_commit_repo(repo)
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `planned.py`（新設）\n\n### `planned.py`\n\n```text\ncontent\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", "--work-dir", str(repo), "--base-commit", base_commit, str(plan)])

    assert main() == 0
    assert "対象ファイル一覧と実変更ファイル集合が一致しない" in capsys.readouterr().err


def test_base_commit_reports_test_function_name_mismatch(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    base_commit = _create_two_commit_repo(repo, second_content="def test_actual():\n    pass\n")
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `actual.py`（新設）\n\n"
        "### `actual.py`\n\n```text\ndef test_planned\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", "--work-dir", str(repo), "--base-commit", base_commit, str(plan)])

    assert main() == 0
    assert "計画が列挙するテスト関数名と実差分の追加関数名が一致しない" in capsys.readouterr().err


@pytest.mark.parametrize(
    "planned_blocks",
    (
        "```text\nasync def test_actual\n```\n",
        "```text\ndef test_actual\n```\n\n### 補足\n\n```text\ndef test_actual\n```\n",
    ),
)
def test_base_commit_accepts_async_or_repeated_planned_test_function_name(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    planned_blocks: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    function = "async def test_actual():\n    pass\n" if "async" in planned_blocks else "def test_actual():\n    pass\n"
    base_commit = _create_two_commit_repo(repo, second_content=function)
    body = (
        _REFERENCE_ENUMERATION_SECTION + "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `actual.py`（新設）\n\n"
        f"### `actual.py`\n\n{planned_blocks}"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", "--work-dir", str(repo), "--base-commit", base_commit, str(plan)])

    assert main() == 0
    assert "計画が列挙するテスト関数名と実差分の追加関数名が一致しない" not in capsys.readouterr().err


@pytest.mark.parametrize("modified_content", (None, "after\n"))
def test_base_commit_accepts_complete_and_modified_rename_as_delete_and_add(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    modified_content: str | None,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    (repo / "old.py").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "old.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "基準コミット"], check=True)
    base_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "mv", "old.py", "new.py"], check=True)
    if modified_content is not None:
        (repo / "new.py").write_text(modified_content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "new.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "改名"], check=True)
    body = (
        _REFERENCE_ENUMERATION_SECTION + "## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `old.py`（廃止・削除）\n- [ ] `new.py`（新設）\n\n"
        "### `old.py`\n\n```text\n旧ファイルを削除する。\n```\n\n"
        "### `new.py`\n\n```text\n新ファイルを追加する。\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", "--work-dir", str(repo), "--base-commit", base_commit, str(plan)])

    assert main() == 0
    assert "対象ファイル一覧と実変更ファイル集合が一致しない" not in capsys.readouterr().err


def test_base_commit_accepts_async_test_function_name(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    base_commit = _create_two_commit_repo(repo, second_content="async def test_async_case():\n    pass\n")
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `actual.py`（新設）\n\n"
        "### `actual.py`\n\n```text\nasync def test_async_case\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", "--work-dir", str(repo), "--base-commit", base_commit, str(plan)])

    assert main() == 0
    assert "計画が列挙するテスト関数名と実差分の追加関数名が一致しない" not in capsys.readouterr().err


def test_base_commit_accepts_rename_as_delete_and_add(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    (repo / "old.py").write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "old.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "基準コミット"], check=True)
    base_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "mv", "old.py", "new.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "改名"], check=True)
    body = (
        _REFERENCE_ENUMERATION_SECTION + "## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `old.py`（廃止・削除）\n- [ ] `new.py`（新設）\n\n"
        "### `old.py`\n\n```text\n旧ファイルを削除する。\n```\n\n"
        "### `new.py`\n\n```text\ncontent\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", "--work-dir", str(repo), "--base-commit", base_commit, str(plan)])

    assert main() == 0
    assert "対象ファイル一覧と実変更ファイル集合が一致しない" not in capsys.readouterr().err


def test_base_commit_reports_commit_subject_mismatch(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    base_commit = _create_two_commit_repo(repo)
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `actual.py`（新設）\n\n"
        "### `actual.py`\n\n```text\nafter\n```\n\n- 件名案: `計画上の件名`\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", "--work-dir", str(repo), "--base-commit", base_commit, str(plan)])

    assert main() == 0
    assert "コミット件名案と実際のコミット件名が一致しない" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("body", "expected_exit", "expected_fragment"),
    [
        pytest.param(
            "    | A | B |\n    | --- | --- | --- |\n",
            0,
            None,
            id="indented_pseudo_table",
        ),
        pytest.param(
            "````markdown\n### `embedded.md`\n\n| A | B |\n| --- | --- | --- |\n````\n",
            0,
            None,
            id="fenced_structure",
        ),
        pytest.param(
            "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md` ###\n\n```text\ncontent\n```\n",
            0,
            None,
            id="closed_h3_heading",
        ),
        pytest.param(
            "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
            "### `foo.md`（新設, 見込み20行）\n\n```text\ncontent\n```\n",
            0,
            None,
            id="decorated_h3_path_heading",
        ),
        pytest.param(
            "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
            "### `foo.md`と`bar.md`\n\n```text\ncontent\n```\n",
            0,
            None,
            id="multiple_paths_in_h3_heading",
        ),
        pytest.param(
            "| A | B |\n| --- | --- |\n| only |\n",
            1,
            "5行目: 表の本文行のセル数がヘッダーと一致しない",
            id="table_body_missing_cell",
        ),
        pytest.param(
            "| A | B |\n| --- | --- |\n| 1 | 2 | 3 |\n",
            1,
            "5行目: 表の本文行のセル数がヘッダーと一致しない",
            id="table_body_extra_cell",
        ),
        pytest.param(
            "| A | B |\n| --- | --- | --- |\n",
            1,
            "4行目: 表の区切り行のセル数がヘッダーと一致しない",
            id="paragraphized_table_mismatch",
        ),
        pytest.param(
            "| A | B |\n| 1 | 2 | 3 |\n",
            0,
            None,
            id="ordinary_pipe_paragraph",
        ),
        pytest.param(
            "| A | B |\n--- | ---\n| 1 | 2 |\n",
            1,
            "4行目: 表の外側パイプの有無がヘッダーと一致しない",
            id="outer_pipe_mismatch",
        ),
    ],
)
def test_markdown_structure_boundary_corpus(
    body: str,
    expected_exit: int,
    expected_fragment: str | None,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """構造認識と原記法検査に共通するMarkdown境界条件を検証する。"""
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == expected_exit
    stderr = capsys.readouterr().err
    if expected_fragment is None:
        assert stderr == ""
    else:
        assert expected_fragment in stderr


def test_document_is_parsed_once(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """構造認識と原記法検査が1回のMarkdown解析結果を共有する。"""
    plan = _write_plan(tmp_path, "## 変更内容\n\n### 対象ファイル一覧\n")
    original_parse = markdown_it.MarkdownIt.parse
    calls = 0

    def counting_parse(self: markdown_it.MarkdownIt, source: str, env: dict[str, object] | None = None):
        nonlocal calls
        calls += 1
        return original_parse(self, source, env)

    monkeypatch.setattr(markdown_it.MarkdownIt, "parse", counting_parse)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""
    assert calls == 1


def test_single_h1_returns_zero_without_h1_error(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, "## 変更内容\n\n### 対象ファイル一覧\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_missing_h1_errors(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _write_plan(tmp_path, "## 変更内容\n\n### 対象ファイル一覧\n", include_h1=False)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "先頭行がATX形式`# <主題>`のH1見出しではない" in capsys.readouterr().err


@pytest.mark.parametrize("title", ["# #", "# ###   "])
def test_h1_with_only_closing_sequence_errors(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    title: str,
) -> None:
    plan = _write_plan(tmp_path, f"{title}\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "先頭行がATX形式`# <主題>`のH1見出しではない" in capsys.readouterr().err


def test_h1_only_after_first_line_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, "説明\n\n# 文書途中の見出し\n", include_h1=False)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    stderr = capsys.readouterr().err
    assert "先頭行がATX形式`# <主題>`のH1見出しではない" in stderr
    assert "フェンス外に追加のH1見出し候補がある" in stderr


def test_duplicate_atx_h1_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, "# テスト計画\n\n# 追加見出し\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "フェンス外に追加のH1見出し候補がある" in capsys.readouterr().err


def test_h1_in_fence_is_ignored(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, "````markdown\n# 埋め込み見出し\n````\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("indent", [" ", "  ", "   "])
def test_indented_atx_h1_only_errors(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    indent: str,
) -> None:
    plan = _write_plan(tmp_path, f"{indent}# インデント見出し\n", include_h1=False)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    stderr = capsys.readouterr().err
    assert "先頭行がATX形式`# <主題>`のH1見出しではない" in stderr


@pytest.mark.parametrize("indent", [" ", "  ", "   "])
def test_indented_atx_h1_after_canonical_h1_errors(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    indent: str,
) -> None:
    plan = _write_plan(tmp_path, f"{indent}# インデント見出し\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "フェンス外に追加のH1見出し候補がある" in capsys.readouterr().err


def test_setext_h1_only_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, "Setext見出し\n===\n", include_h1=False)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    stderr = capsys.readouterr().err
    assert "先頭行がATX形式`# <主題>`のH1見出しではない" in stderr


def test_setext_h1_after_canonical_h1_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, "Setext見出し\n===\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "フェンス外に追加のH1見出し候補がある" in capsys.readouterr().err


def test_equals_after_blank_line_is_not_setext_h1(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, "\n=\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_missing_h3_errors(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _write_plan(
        tmp_path,
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n",
    )
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "H3見出しが無い対象ファイル" in captured.err


def test_code_block_presence(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n```text\n[新設]\ncontent\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0


def test_change_structure_outside_change_section_is_ignored(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`## 変更内容`節外のチェックボックスとH3見出しを変更対象として検出しない。"""
    body = (
        "## 調査結果\n\n### 対象ファイル一覧\n\n- [ ] `does/not/exist.md`\n\n"
        "### `does/not/exist.md`\n\n変更内容ではない説明。\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_all_repeated_change_sections_are_checked(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """複数の`## 変更内容`節がある場合は全出現の対象ファイル一覧を検査する。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `first.md`（新設）\n\n"
        "### `first.md`\n\n```text\ncontent\n```\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `second.md`（新設）\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "H3見出しが無い対象ファイル: ['second.md']" in capsys.readouterr().err


def test_nonexistent_path_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `does/not/exist.md`\n\n"
        "### `does/not/exist.md`\n\n```text\nplaceholder\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "実在確認できないパス" in captured.err


def test_unrelated_new_marker_before_h3_does_not_skip_existence_check(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """対象H3直前の無関係な`（新設）`では実在確認を免除しない。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `does/not/exist.md`\n\n"
        "別項目の説明（新設）\n"
        "### `does/not/exist.md`\n\n```text\ncontent\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "実在確認できないパス: does/not/exist.md" in capsys.readouterr().err


def test_new_marker_on_checkbox_skips_existence_check(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """対象パスのチェックボックスにある`（新設）`は実在確認を免除する。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `does/not/exist.md`（新設）\n\n"
        "### `does/not/exist.md`\n\n```text\ncontent\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_nonexistent_path_with_prefixed_deletion_marker_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`（現行N行、廃止・削除）`のような前置き付き複合形のマーカーも実在確認を免除する。"""
    body = (
        _REFERENCE_ENUMERATION_SECTION + "## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `does/not/exist.md`（現行87行、廃止・削除）\n\n"
        "### `does/not/exist.md`\n\n```text\n本ファイルを削除する。\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "実在確認できないパス" not in captured.err


def test_fence_nesting_violation_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
        "### `foo.md`\n\n```text\n[新設]\n````markdown\nnested\n````\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "フェンス" in captured.err


def test_execution_method_session_review_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 実行方法\n\n- 実装する\n- `agent-toolkit:session-review`で振り返りを実施する\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "セッション運用工程" in captured.err


def test_execution_method_without_session_ops_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 実行方法\n\n- 実装する\n- 検証する\n- コミットする\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "セッション運用工程" not in captured.err


@pytest.mark.parametrize(
    ("line", "expect_warning"),
    [
        # 工程としての記載。対象語が句の主辞となる形、列挙の中に現れる形、
        # 実装対象と同じ名詞を伴いながら実施の述部を持つ形、述部を持たない継続行を含む。
        pytest.param("- 振り返りを実施する", True, id="execute_review"),
        pytest.param("- セッション終了を行う", True, id="execute_session_end"),
        pytest.param("- 振り返りへ進む", True, id="proceed_to_review"),
        pytest.param("- 振り返りスキルを起動する", True, id="execute_with_noun_skill"),
        pytest.param("- 振り返り処理を実施する", True, id="execute_with_implementation_noun"),
        pytest.param("- セッション終了処理を実施する", True, id="execute_session_end_with_noun"),
        pytest.param("- 振り返り工程を完遂する", True, id="execute_process_noun"),
        pytest.param("- session-reviewスキルを呼び出す", True, id="invoke_skill_with_noun"),
        pytest.param("- 振り返り工程へ進む", True, id="proceed_to_process_noun"),
        pytest.param("- セッション終了まで完遂する", True, id="complete_through_session_end"),
        pytest.param("- 振り返り・exit-sessionを実施する", True, id="execute_enumeration"),
        pytest.param("- （振り返り・セッション終了）を実施する", True, id="execute_parenthesized_enumeration"),
        pytest.param(
            "- 後続工程（push・CI通過確認・振り返り・exit-session）を完遂する",
            True,
            id="execute_enumeration_with_trailing_verb",
        ),
        pytest.param("- 対象工程は`git push`・push後CI通過確認・振り返り・", True, id="enumeration_continued_next_line"),
        pytest.param("14. `agent-toolkit:exit-session`でセッション終了", True, id="nominal_ending_step"),
        pytest.param("- 振り返り工程（`session-review-dotfiles`を含む）", True, id="process_noun_without_verb"),
        pytest.param(
            "- Skillツールで`agent-toolkit:exit-session`を呼び出す",
            True,
            id="invoke_skill",
        ),
        # 実装対象への言及。対象語が実装対象を指す名詞を伴い、実施の述部を持たない形。
        pytest.param("- 振り返りフックの誘導を変更する", False, id="implementation_target"),
        pytest.param("- セッション終了処理を実装する", False, id="implementation_noun_phrase"),
        pytest.param("- 振り返りフックの誘導の変更を行う", False, id="modifier_in_noun_phrase"),
        pytest.param("- セッション終了判定を実装する", False, id="implementation_target_predicate"),
        pytest.param("- exit-session description厳格化を反映", False, id="implementation_target_description"),
        pytest.param("- 振り返り規範を改訂する", False, id="implementation_target_norm"),
        pytest.param("- 振り返りスクリプトを修正する", False, id="process_script_noun_modified"),
        pytest.param("- セッション終了フローの見直しを行う", False, id="process_flow_noun_modified"),
        pytest.param("- session-reviewモジュールを削除する", False, id="process_module_noun_deleted"),
        pytest.param("- 振り返りログの記録先を変更する", False, id="process_log_noun_modified"),
        pytest.param("- 振り返りの誘導を変更する", False, id="process_adnominal_noun_modified"),
        pytest.param("- exit-sessionの実装を移設する", False, id="process_adnominal_impl_moved"),
        pytest.param("- 振り返り結果を反映する", True, id="process_result_reflected"),
        pytest.param(
            "- 振り返り工程（session-review-dotfiles）を含む",
            True,
            id="process_noun_with_unquoted_session_op",
        ),
    ],
)
def test_execution_method_requires_instruction_form(
    line: str,
    expect_warning: bool,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実装対象の名詞句を除外し、工程としてのセッション運用の記載を語順によらず検出する。"""
    body = f"## 実行方法\n\n{line}\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert ("セッション運用工程" in capsys.readouterr().err) is expect_warning


@pytest.mark.parametrize(
    "line",
    [
        "- `uvx pyfltr run agent-toolkit/skills/session-review/SKILL.md`で検証する",
        "- コミット件名は`feat(session-review): 観察源を追加する`とする",
        "- `agent-toolkit/skills/session-review/SKILL.md`を変更する",
    ],
)
def test_execution_method_inline_session_ops_identifiers_are_silent(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    line: str,
) -> None:
    body = f"## 実行方法\n\n{line}\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "line",
    [
        "- Skillツールで`agent-toolkit:exit-session`を呼び出す",
        "- `/agent-toolkit:exit-session`を呼び出す",
        "- セッション終了を実施する",
    ],
)
def test_execution_method_session_ops_invocations_warn(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    line: str,
) -> None:
    body = f"## 実行方法\n\n{line}\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" in capsys.readouterr().err


def test_execution_method_unclosed_inline_code_does_not_raise(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 実行方法\n\n- `agent-toolkit:session-reviewを実施する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" in capsys.readouterr().err


def test_execution_method_escaped_backticks_do_not_hide_session_ops(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 実行方法\n\n- \\`session-review\\`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" in capsys.readouterr().err


def test_execution_method_code_span_closes_after_literal_backslash(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """コードスパン内のバックスラッシュは閉じバッククォートをエスケープしない。"""
    body = "## 実行方法\n\n- `session-review\\`を実施する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" not in capsys.readouterr().err


def test_execution_method_multiline_code_span_is_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """複数行コードスパン内のセッション運用名は通常本文として扱わない。"""
    body = (
        "## 実行方法\n\n``\nSkillツールで`agent-toolkit:session-review`を呼び出す\n``\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" not in capsys.readouterr().err


def test_execution_method_nested_slash_command_in_multiline_code_span_is_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """複数行コードスパン内のスラッシュコマンド構文は起動指示として扱わない。"""
    body = "## 実行方法\n\n``\n`/agent-toolkit:exit-session`\n`` 通常本文\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" not in capsys.readouterr().err


def test_execution_method_code_span_does_not_cross_blank_line(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """別段落のバッククォート列を閉じ区切りとして扱わない。"""
    body = "## 実行方法\n\n`\nsession-reviewを実施する\n\n`\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" in capsys.readouterr().err


def test_execution_method_invocation_after_multiline_code_span_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """複数行コードスパンの後にある実際の呼び出し指示は検出する。"""
    body = (
        "## 実行方法\n\n``\nsession-review\n``\n"
        "- `/agent-toolkit:exit-session`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" in capsys.readouterr().err


@pytest.mark.parametrize(
    "execution_body",
    [
        "- `session-reviewを実施する\n- second`",
        "`session-reviewを実施する\n### heading `",
        "`session-reviewを実施する\n> quote `",
        "`session-reviewを実施する\n***\ntext `",
    ],
)
def test_execution_method_code_span_does_not_cross_inline_block_boundary(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    execution_body: str,
) -> None:
    """別のCommonMarkインラインブロックにある閉じ列と対応付けない。"""
    body = f"## 実行方法\n\n{execution_body}\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" in capsys.readouterr().err


@pytest.mark.parametrize(
    "execution_body",
    [
        "- item\n    `session-reviewを実施する`",
        "- ``\n  session-reviewを実施する\n  ``",
        "> ``\n> session-reviewを実施する\n> ``",
        "`session-reviewを実施する\nheading `\n---",
        "    session-reviewを実施する",
    ],
)
def test_execution_method_commonmark_inline_and_code_blocks_are_silent(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    execution_body: str,
) -> None:
    """正当なコードスパンとコードブロック内の検出語は通常本文として扱わない。"""
    body = f"## 実行方法\n\n{execution_body}\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" not in capsys.readouterr().err


def test_execution_method_longer_backtick_run_does_not_close_code_span(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """開き列より長い最大バッククォート列への部分一致ではコードスパンを閉じない。"""
    body = "## 実行方法\n\n- `session-reviewを実施する``を確認する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert "セッション運用工程" in capsys.readouterr().err


def test_unknown_skill_name_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 実行方法\n\n- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "実在しないスキル名の疑い" in captured.err
    assert "no-such-skill" in captured.err


def test_known_skill_name_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 実行方法\n\n- Skillツールで`agent-toolkit:coding-standards`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "実在しないスキル名の疑い" not in captured.err


def test_unknown_skill_name_with_prefix_mismatch_suggests_candidate(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """接頭辞違いで存在する候補があればerror文へ候補名を添える。"""
    body = "## 実行方法\n\n- Skillツールで`coding-standards`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "接頭辞違いの候補: `agent-toolkit:coding-standards`" in captured.err


def test_unknown_skill_name_without_prefix_mismatch_candidate(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """接頭辞違いの候補も実在しない場合はその旨をerror文へ添える。"""
    body = "## 実行方法\n\n- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "接頭辞違いの候補も無し" in captured.err


def test_agent_tool_subagent_name_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Agentツールで`構文のサブエージェント名は実在すればerrorとして検出しない。"""
    body = "## 実行方法\n\n- Agentツールで`agent-toolkit:plan-impl-executor`を起動する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "の疑い" not in captured.err


def test_agent_tool_unknown_subagent_name_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Agentツールで`構文で実在しない名前はサブエージェント名としてerrorにする。"""
    body = "## 実行方法\n\n- Agentツールで`agent-toolkit:no-such-agent`を起動する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "実在しないサブエージェント名の疑い" in captured.err
    assert "no-such-agent" in captured.err


def test_agent_tool_with_skill_name_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Agentツールで`構文にスキル名を書いた取り違えはerrorにする。"""
    body = "## 実行方法\n\n- Agentツールで`agent-toolkit:coding-standards`を起動する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "実在しないサブエージェント名の疑い" in captured.err
    assert "coding-standards" in captured.err


def test_local_subagent_name_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.claude/agents/*.md`のローカル定義はサブエージェント候補として受理する。"""
    local_agents = tmp_path / ".claude" / "agents"
    local_agents.mkdir(parents=True)
    (local_agents / "local-reviewer.md").write_text("# local-reviewer\n", encoding="utf-8")
    body = "## 実行方法\n\n- Agentツールで`local-reviewer`を起動する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "実在しないサブエージェント名の疑い" not in captured.err


def test_skill_tool_with_subagent_name_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Skillツールで`構文にサブエージェント名を書いた取り違えはerrorにする。"""
    body = "## 実行方法\n\n- Skillツールで`agent-toolkit:plan-impl-executor`を呼び出す\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "実在しないスキル名の疑い" in captured.err


def test_slash_prefix_with_subagent_name_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/`接頭辞にサブエージェント名を書いた取り違えはスキル名としてerrorにする。"""
    body = "## 実行方法\n\n- `/agent-toolkit:plan-impl-executor`を起動する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "実在しないスキル名の疑い" in captured.err
    assert "plan-impl-executor" in captured.err


def test_same_name_used_correctly_and_incorrectly_in_different_syntax(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """同名を正しい構文と誤った構文の双方で使った場合、誤った側だけを独立してerrorにする。

    名前だけをキーにする実装では一方の種別が失われ、この取り違えを検出できない。
    """
    body = (
        "## 実行方法\n\n"
        "- Skillツールで`agent-toolkit:plan-impl-executor`を呼び出す\n"
        "- Agentツールで`agent-toolkit:plan-impl-executor`を起動する\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    # `agent-toolkit:plan-impl-executor`はサブエージェント定義であり、スキル定義ではない。
    # `Agentツールで`側は正しい呼び出しでありerrorとして検出しない。`Skillツールで`側は
    # 取り違えであり、スキル名として実在しないことをerrorとして報告する。
    assert "実在しないスキル名の疑い: `agent-toolkit:plan-impl-executor`" in captured.err
    assert "実在しないサブエージェント名の疑い" not in captured.err


def test_bare_agent_toolkit_reference_accepts_subagent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """構文を伴わない参照はスキル・サブエージェントいずれかに実在すればerrorとして検出しない。"""
    body = "## 実行方法\n\n- 実装は`agent-toolkit:plan-impl-executor`の担当とする\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "の疑い" not in captured.err


def test_sample_plan_reports_no_diagnostics(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """配布している計画ファイルの記述例が診断を報告しない。

    記述例は先頭行を除く全体が````markdown`フェンス内にあり、構造抽出処理が
    フェンス外の行に限定されるため、埋め込み内の架空パス・呼び出し名を含め
    計画構造が1件も抽出されずerror・warningいずれも生じない。
    """
    sample = pathlib.Path(__file__).resolve().parents[1] / "references" / "sample.md"
    assert sample.is_file()
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(sample)])
    assert main() == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


@pytest.mark.parametrize(
    ("metadata", "expected_error", "expect_work_type_warning"),
    [
        (
            f"### 計画メタ情報\n\n- ベースコミット: `{_FULL_COMMIT}`\n- 作業種別: 通常変更\n- 実行系変更: なし\n",
            None,
            False,
        ),
        (
            "### 計画メタ情報\n\n- 対象リポジトリ: `/tmp/repo`\n- 作業種別: 通常変更\n",
            "ベースコミットの記載が無い",
            False,
        ),
        (
            "### 計画メタ情報\n\n- ベースコミット: `01234567`\n- 作業種別: 通常変更\n",
            "コミットハッシュの記載が無い",
            False,
        ),
        ("### 経緯\n\n変更理由\n", None, True),
        ("````markdown\n### 計画メタ情報\n\n- 対象リポジトリ: `/tmp/repo`\n````\n", None, True),
    ],
)
def test_base_commit_recording(
    metadata: str,
    expected_error: str | None,
    expect_work_type_warning: bool,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計画メタ情報の有無と記載値に応じてベースコミットの診断を切り替える。"""
    plan = _write_plan(tmp_path, f"## 背景\n\n{metadata}\n## 変更内容\n\n### 対象ファイル一覧\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == (1 if expected_error else 0)
    captured = capsys.readouterr()
    if expected_error is None and not expect_work_type_warning:
        assert captured.err == ""
    if expected_error is not None:
        assert expected_error in captured.err
    assert ("計画メタ情報に作業種別の記載が無い" in captured.err) is expect_work_type_warning


@pytest.mark.parametrize(
    "metadata",
    [
        "### 計画メタ情報\n",
        "### 計画メタ情報\n### 経緯\n\n変更理由\n",
    ],
)
def test_empty_base_commit_metadata_section_errors(
    metadata: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空の計画メタ情報節と直後が次節となる境界値は、ベースコミット未記載として終了コード1を返す。"""
    plan = _write_plan(tmp_path, f"## 背景\n\n{metadata}\n## 変更内容\n\n### 対象ファイル一覧\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 1
    assert "ベースコミットの記載が無い" in capsys.readouterr().err


def test_deletion_marker_without_deletion_word_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n### `foo.md`\n\n```text\n節を圧縮する\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "指定内容の食い違いの疑い" in captured.err


def test_deletion_marker_with_deletion_word_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        _REFERENCE_ENUMERATION_SECTION + "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        "### `foo.md`\n\n```text\n本ファイルを削除する。退避先: archive/foo.md\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "指定内容の食い違いの疑い" not in captured.err


def test_deletion_marker_with_deletion_word_only_in_prose_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """削除指示語が地の文のみに現れ`text`コードブロック内に無い場合は食い違いとしてerrorにする。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        "### `foo.md`\n\n本ファイルを削除する。\n\n```text\n節を圧縮する\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "指定内容の食い違いの疑い" in captured.err


def test_deletion_marker_with_deletion_word_only_in_python_block_errors(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """削除指示語が`python`ブロックのみに現れ`text`ブロックが無い場合も食い違いとしてerrorにする。

    `_has_code_block_after`は任意の情報文字列のコードブロック存在で通過するため、
    `text`以外のブロックのみのH3が両検査を回避しないことを確認する。
    """
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        "### `foo.md`\n\n```python\n# 本ファイルを削除する\nx = 1\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    captured = capsys.readouterr()
    assert "指定内容の食い違いの疑い" in captured.err


def test_deprecated_identifier_extraction_does_not_cross_lines(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """片側だけバッククォートを持つ行を後続行と対応づけて識別子化しない。"""
    _init_git_repo(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "leftover.py").write_text("x = 1\n", encoding="utf-8")
    body = (
        "## 変更内容\n\n#### 廃止・改名対象一覧\n\n- 対象は`scripts/leftover.py\n- 補足は次のとおり`\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    # 行を跨いで結合すると本文全体が1識別子となり、パスとして残存判定されない。
    # 行単位で抽出すると、片側だけバッククォートを持つ行は識別子の抽出対象にならない。
    assert "\n- 補足は次のとおり" not in capsys.readouterr().err


def test_deprecated_identifier_residual_file_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "leftover.py").write_text("x = 1\n", encoding="utf-8")
    body = "## 変更内容\n\n#### 廃止・改名対象一覧\n\n- `scripts/leftover.py`\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "廃止・改名対象一覧の識別子が残存している疑い" in captured.err
    assert "scripts/leftover.py" in captured.err


def test_deprecated_identifier_removed_silent(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    body = "## 変更内容\n\n#### 廃止・改名対象一覧\n\n- `_removed_helper`\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "廃止・改名対象一覧の識別子が残存している疑い" not in captured.err


def test_deprecated_identifier_type_annotated_constant_residual_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """型注釈付き定数定義（`NAME: Type = value`）の残存も検出する。"""
    _init_git_repo(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "leftover.py").write_text(
        "_LEFTOVER_CONSTANT: frozenset[str] = frozenset({'x'})\n", encoding="utf-8"
    )
    body = "## 変更内容\n\n#### 廃止・改名対象一覧\n\n- `_LEFTOVER_CONSTANT`\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "廃止・改名対象一覧の識別子が残存している疑い" in captured.err
    assert "_LEFTOVER_CONSTANT" in captured.err


def test_deprecated_identifier_excludes_temporary_copy_and_plan_file(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一時複製・計画ファイル自身は走査の対象外とする。

    `.git`配下はGitの一覧に現れないため、走査対象の母集団へ最初から入らない。
    """
    _init_git_repo(tmp_path)
    # 定義形で書く。単なる言及では識別子定義の検出条件に元から一致せず、除外の有無を判定できない。
    (tmp_path / ".plan-check-sample-1234.md").write_text("def _removed_helper():\n    pass\n", encoding="utf-8")
    body = (
        "## 変更内容\n\n#### 廃止・改名対象一覧\n\n- `_removed_helper`\n\n### 対象ファイル一覧\n\n"
        "計画ファイル自身が定義例を掲載する場合の自己参照除外を確認する。\n\n"
        "```python\ndef _removed_helper():\n    pass\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    captured = capsys.readouterr()
    assert "廃止・改名対象一覧の識別子が残存している疑い" not in captured.err


def _new_identifier_plan_body(block_body: str, *, investigation: str = "") -> str:
    """新設識別子検査用の計画本文を組み立てる。"""
    return (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `target.py`（新設）\n\n"
        f"### `target.py`\n\n```text\n{block_body}\n```\n{investigation}"
    )


@pytest.mark.parametrize(
    ("identifier", "block_body"),
    [
        pytest.param("new_scope_flag", "+`new_scope_flag`を導入する", id="snake_case"),
        pytest.param("NEW_SCOPE_LABELS", "+`NEW_SCOPE_LABELS`を導入する", id="upper_snake_case"),
        pytest.param("_new_scope_helper", "+`_new_scope_helper`を導入する", id="leading_underscore"),
    ],
)
def test_new_identifier_scope_detects_identifier_forms(
    identifier: str,
    block_body: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既存出現の無い識別子を形態ごとに抽出し、波及先列挙の不足を警告する。"""
    _init_git_repo(tmp_path)
    plan = _write_plan(tmp_path, _new_identifier_plan_body(block_body))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    captured = capsys.readouterr()
    assert "新設識別子の波及先列挙の不足の疑い" in captured.err
    assert identifier in captured.err


def test_new_identifier_scope_silent_for_existing_identifier(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """対象worktree内に既存出現がある識別子は新設と判定しない。"""
    _init_git_repo(tmp_path)
    (tmp_path / "existing.py").write_text("existing_flag = 1\n", encoding="utf-8")
    plan = _write_plan(tmp_path, _new_identifier_plan_body("+`existing_flag`の扱いを変える"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "新設識別子の波及先列挙の不足の疑い" not in capsys.readouterr().err


def test_new_identifier_scope_warns_for_partial_record(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """抽出した識別子の一部だけが`## 調査結果`にある場合は不足分を警告する。"""
    _init_git_repo(tmp_path)
    body = _new_identifier_plan_body(
        "+`alpha_flag`と`beta_flag`を導入する",
        investigation="\n## 調査結果\n\n- 新設識別子: `alpha_flag`（波及先は当該ファイルのみ）\n",
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    captured = capsys.readouterr()
    assert "不足: beta_flag" in captured.err
    assert "必須語" not in captured.err


def test_new_identifier_scope_warns_when_repository_resolution_fails(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """対象worktreeをGitリポジトリとして解決できない場合は判定不能を警告する。"""
    plan = _write_plan(tmp_path, _new_identifier_plan_body("+`new_scope_flag`を導入する"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan), "--work-dir", str(tmp_path / "absent")])

    assert main() == 0
    captured = capsys.readouterr()
    assert "新設識別子の既存出現を判定できない" in captured.err
    assert "新設識別子の波及先列挙の不足の疑い" not in captured.err


def test_new_identifier_scope_uses_work_dir_instead_of_cwd(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """既存出現の照会先は現在の作業ディレクトリではなく`--work-dir`とする。"""
    work_dir = tmp_path / "target"
    work_dir.mkdir()
    _init_git_repo(work_dir)
    (work_dir / "existing.py").write_text("existing_flag = 1\n", encoding="utf-8")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    _init_git_repo(unrelated)
    plan = _write_plan(tmp_path, _new_identifier_plan_body("+`existing_flag`の扱いを変える"))
    monkeypatch.chdir(unrelated)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan), "--work-dir", str(work_dir)])

    assert main() == 0
    # cwd側には出現が無いため、`--work-dir`が無視されると新設と誤判定される。
    assert "新設識別子の波及先列挙の不足の疑い" not in capsys.readouterr().err


def test_new_identifier_scope_ignores_files_outside_git_management(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gitが無視するディレクトリの出現は既存出現として数えない。"""
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "vendored.py").write_text("existing_flag = 1\n", encoding="utf-8")
    plan = _write_plan(tmp_path, _new_identifier_plan_body("+`existing_flag`を導入する"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    captured = capsys.readouterr()
    assert "新設識別子の波及先列挙の不足の疑い" in captured.err
    assert "existing_flag" in captured.err


def test_deprecated_identifier_uses_work_dir_instead_of_cwd(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """廃止・改名対象の残存判定も`--work-dir`を照会先とする。

    識別子定義の走査とファイルパスの実在確認は判定経路が別のため、両形態を対象へ含める。
    """
    work_dir = tmp_path / "target"
    work_dir.mkdir()
    _init_git_repo(work_dir)
    (work_dir / "leftover.py").write_text("def _removed_helper():\n    pass\n", encoding="utf-8")
    (work_dir / "scripts").mkdir()
    (work_dir / "scripts" / "obsolete.py").write_text("x = 1\n", encoding="utf-8")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    _init_git_repo(unrelated)
    body = "## 変更内容\n\n#### 廃止・改名対象一覧\n\n- `_removed_helper`\n- `scripts/obsolete.py`\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(unrelated)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan), "--work-dir", str(work_dir)])

    assert main() == 0
    captured = capsys.readouterr()
    # cwd側には定義もファイルも無いため、`--work-dir`が無視されると両形態とも残存を見逃す。
    assert "_removed_helper" in captured.err
    assert "scripts/obsolete.py" in captured.err


def test_deprecated_identifier_warns_when_repository_resolution_fails(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gitリポジトリとして解決できない場合、識別子定義の走査だけを判定不能として通知する。

    パス形態はファイルの実在で判定できるため、解決の可否によらず検査を継続する。
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "leftover.py").write_text("x = 1\n", encoding="utf-8")
    body = "## 変更内容\n\n#### 廃止・改名対象一覧\n\n- `_removed_helper`\n- `scripts/leftover.py`\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan), "--work-dir", str(tmp_path)])

    assert main() == 0
    captured = capsys.readouterr()
    assert "廃止・改名対象一覧の識別子の残存を判定できない" in captured.err
    assert "scripts/leftover.py" in captured.err


_META_NORM_PLAN_BODY = (
    "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `agent-toolkit/rules/foo.md`（新設）\n\n"
    "### `agent-toolkit/rules/foo.md`\n\n```text\n+- 起動時にnameを指定しない\n```\n"
)
_NO_TRIGGER_PLAN_BODY = (
    "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `agent-toolkit/rules/foo.md`（新設）\n\n"
    "### `agent-toolkit/rules/foo.md`\n\n```text\n+- 起動手順を追記する\n```\n"
)


@pytest.mark.parametrize(
    ("investigation_section", "expect_error"),
    [
        pytest.param(
            "## 調査結果\n\n- 対象パターン: 汎用禁止形バレット\n- 検出件数: 1件\n- 対応方針: 機械チェックと対で記載する\n\n"
            + _META_NORM_PLAN_BODY,
            False,
            id="required_items_present",
        ),
        pytest.param(
            "## 調査結果\n\n- 対象パターン: 汎用禁止形バレット\n- 対応方針: 機械チェックと対で記載する\n\n"
            + _META_NORM_PLAN_BODY,
            True,
            id="required_item_missing",
        ),
        pytest.param(
            "## 調査結果\n\n- 既存節へ手順を1件追記する\n\n" + _NO_TRIGGER_PLAN_BODY,
            False,
            id="not_triggered",
        ),
    ],
)
def test_retroactive_scan_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    investigation_section: str,
    expect_error: bool,
) -> None:
    """遡及スキャン必須3語の充足有無に応じてerrorの有無が切り替わる。"""
    plan = _write_plan(tmp_path, investigation_section)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == (1 if expect_error else 0)
    captured = capsys.readouterr()
    assert ("遡及スキャン記録の不足の疑い" in captured.err) is expect_error


def test_retroactive_scan_not_required_for_non_agent_doc_targets(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """対象ファイルがエージェント向け文書でない計画では遡及スキャン記録を要求しない。

    対象ファイルが`docs/development/development.md`のみの計画で、変更後文面に既存の`##`見出しを
    含む検体を用いる。対象ファイル判定の追加前は当該見出しで過検出していた。
    """
    target = tmp_path / "docs" / "development" / "development.md"
    target.parent.mkdir(parents=True)
    target.write_text("# 開発\n", encoding="utf-8")
    body = (
        _REFERENCE_ENUMERATION_SECTION + "## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `docs/development/development.md`（現行1行）\n\n"
        "### `docs/development/development.md`\n\n```text\n## 既存見出し\n\n説明を追記する。\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "遡及スキャン記録の不足の疑い" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("target", "expect_error"),
    [
        (".claude/rules/foo.md", True),
        (".claude/skills/x/SKILL.md", True),
        (".claude/skills/x/references/y.md", True),
        # `agent-toolkit/skills/`側と粒度を揃えるため、`SKILL.md`と`references/`配下以外は対象外とする。
        (".claude/skills/x/templates/z.md", False),
    ],
)
def test_retroactive_scan_covers_project_local_agent_docs(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    expect_error: bool,
) -> None:
    """利用者プロジェクトが直接持つ規範文書でも遡及スキャン記録の不足を検出する。

    配布元固有パスだけを対象にすると、プラグインの配布先プロジェクトで検査が素通りする。
    """
    body = (
        "## 調査結果\n\n- 対象パターン: 汎用禁止形バレット\n- 対応方針: 機械チェックと対で記載する\n\n"
        f"## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `{target}`（新設）\n\n"
        f"### `{target}`\n\n```text\n+- 起動時にnameを指定しない\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == (1 if expect_error else 0)
    assert ("遡及スキャン記録の不足の疑い" in capsys.readouterr().err) is expect_error


_EXISTING_TARGET_PLAN_BODY = (
    "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `existing.md`（現行10行）\n\n"
    "### `existing.md`\n\n```text\n+- 手順を1件追記する\n```\n"
)
_NEW_ONLY_PLAN_BODY = (
    "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n```text\n+- 手順を1件追記する\n```\n"
)


@pytest.mark.parametrize(
    ("investigation_section", "plan_body", "expect_error"),
    [
        pytest.param(
            "## 調査結果\n\n- 参照追従対象: `grep -rn`で3件\n- 入力形態: 該当なし\n"
            "- 追従要否: 全件を対象ファイル一覧へ含めた\n\n",
            _EXISTING_TARGET_PLAN_BODY,
            False,
            id="required_items_present",
        ),
        pytest.param(
            "## 調査結果\n\n- 参照追従対象: `grep -rn`で3件\n- 追従要否: 全件を対象ファイル一覧へ含めた\n\n",
            _EXISTING_TARGET_PLAN_BODY,
            True,
            id="required_item_missing",
        ),
        pytest.param(
            "## 調査結果\n\n- 新設のみのため参照追従の対象が無い\n\n",
            _NEW_ONLY_PLAN_BODY,
            False,
            id="not_triggered_for_new_only",
        ),
        pytest.param(
            "## 調査結果\n\n- 新設のみのため参照追従の対象が無い\n\n",
            "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n"
            "````text\n- [ ] `existing.md`（現行10行）\n````\n",
            False,
            id="fenced_checkbox_example_does_not_trigger",
        ),
        pytest.param(
            "## 調査結果\n\n- 新設と既存が混在する\n\n",
            "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n"
            "- [ ] `existing.md`（現行10行）\n\n"
            "### `foo.md`\n\n```text\n+- 手順を1件追記する\n```\n\n"
            "### `existing.md`\n\n```text\n+- 手順を1件追記する\n```\n",
            True,
            id="triggered_when_new_and_existing_are_mixed",
        ),
    ],
)
def test_reference_enumeration_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    investigation_section: str,
    plan_body: str,
    expect_error: bool,
) -> None:
    """参照追従必須3語の充足有無に応じてerrorの有無が切り替わる。"""
    (tmp_path / "existing.md").write_text("既存ファイル\n", encoding="utf-8")
    plan = _write_plan(tmp_path, investigation_section + plan_body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == (1 if expect_error else 0)
    captured = capsys.readouterr()
    assert ("参照追従の網羅列挙の不足の疑い" in captured.err) is expect_error


def test_reference_enumeration_words_in_fenced_example_do_not_satisfy_requirement(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """フェンス内の記述例に必須語があっても要件を満たさない。"""
    body = "## 調査結果\n\n````text\n参照追従対象\n入力形態\n追従要否\n````\n\n" + _EXISTING_TARGET_PLAN_BODY
    (tmp_path / "existing.md").write_text("既存ファイル\n", encoding="utf-8")
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "参照追従の網羅列挙の不足の疑い" in capsys.readouterr().err


def test_deleted_target_triggers_reference_enumeration_check(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`（廃止・削除）`のみの計画でも参照追従検査が発動する。"""
    body = (
        "## 調査結果\n\n- 削除対象の参照は無い\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行10行、廃止・削除）\n\n"
        "### `foo.md`\n\n```text\n本ファイルを削除する\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "参照追従の網羅列挙の不足の疑い" in capsys.readouterr().err


def test_bug_investigation_table_accepts_required_rows_in_order(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = f"# バグ修正計画\n\n## 背景\n\n{_plan_metadata('バグ対応')}\n\n{_bug_investigation_table(_BUG_ROWS)}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""


def test_closed_investigation_heading_is_counted(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _bug_investigation_table(_BUG_ROWS).replace("### バグ調査結果", "### バグ調査結果 ###")
    body = f"# バグ修正計画\n\n## 背景\n\n{_plan_metadata('バグ対応')}\n\n{table}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""


def test_bug_investigation_table_outside_background_warns(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        f"# バグ修正計画\n\n## 背景\n\n{_plan_metadata('バグ対応')}\n\n"
        f"不具合が発生している。\n\n## 対応方針\n\n{_bug_investigation_table(_BUG_ROWS)}\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "`### バグ調査結果`が`## 背景`直下に存在しない" in capsys.readouterr().err


def test_investigation_sections_in_background_and_other_h2_warn_as_duplicates(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _bug_investigation_table(_BUG_ROWS)
    body = f"# バグ修正計画\n\n## 背景\n\n{_plan_metadata('バグ対応')}\n\n{table}\n\n## 対応方針\n\n{table}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "`### バグ調査結果`が複数ある" in capsys.readouterr().err


@pytest.mark.parametrize(
    "table",
    [
        "| 項目 |\n| --- |\n" + "\n".join(f"| {row} |" for row in _BUG_ROWS),
        "| 分類 | 詳細 |\n| --- | --- |\n" + "\n".join(f"| {row} | 内容 |" for row in _BUG_ROWS),
        "| 項目 | 内容 | 補足 |\n| --- | --- | --- |\n" + "\n".join(f"| {row} | 内容 | 補足 |" for row in _BUG_ROWS),
        "| 項目 | 内容 |\n| -- | --- |\n" + "\n".join(f"| {row} | 内容 |" for row in _BUG_ROWS),
    ],
)
def test_bug_investigation_table_requires_two_column_contract(
    table: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (
        f"# バグ修正計画\n\n## 背景\n\n{_plan_metadata('バグ対応')}\n\n不具合が発生している。\n\n### バグ調査結果\n\n{table}\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "バグ調査結果表の列構造が現行契約と一致しない" in capsys.readouterr().err


@pytest.mark.parametrize(
    "description",
    [
        "APIエラーが発生した場合に再試行する機能を追加する。",
        "新しいサブコマンドでエラーが発生した場合は終了コード1を返す。",
        "不具合が発生しているので修正してください。",
        "発生中のエラーの原因を調査してください。",
        "不具合の修正を依頼する。",
        "エラーの修正をお願いします。",
    ],
)
def test_unclassified_text_warns_without_inferring_bug_plan(
    description: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = f"## 背景\n\n### 現状\n\n{description}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    stderr = capsys.readouterr().err
    assert "計画メタ情報に作業種別の記載が無い" in stderr
    assert "バグ計画に必須のバグ調査結果表が存在しない" not in stderr


@pytest.mark.parametrize(
    ("work_type", "description", "expect_bug_warning"),
    [
        ("バグ対応", "通常の変更内容を記載する。", True),
        ("通常変更", "不具合の修正を依頼する。", False),
        ("通常変更", "APIエラーが発生した場合に再試行する機能を追加する。", False),
    ],
)
def test_work_type_controls_bug_plan_independently_of_prose(
    work_type: str,
    description: str,
    expect_bug_warning: bool,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = f"## 背景\n\n{_plan_metadata(work_type)}\n\n### 現状\n\n{description}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    stderr = capsys.readouterr().err
    assert ("バグ計画に必須のバグ調査結果表が存在しない" in stderr) is expect_bug_warning


def test_work_type_like_text_outside_metadata_does_not_mark_bug_plan(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = f"## 背景\n\n{_plan_metadata('通常変更')}\n\n### 現状\n\n- 作業種別: バグ対応\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("work_type", [None, "保守作業"])
def test_missing_or_unknown_work_type_warns(
    work_type: str | None,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = f"## 背景\n\n{_plan_metadata(work_type)}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    stderr = capsys.readouterr().err
    if work_type is None:
        assert "計画メタ情報に作業種別の記載が無い" in stderr
    else:
        assert "作業種別が現行契約と一致しない" in stderr
        assert "固定記法`- 作業種別: <固定値>`と一致しない" not in stderr


@pytest.mark.parametrize(
    "work_type_line",
    [
        "- 作業種別：通常変更",
        "- 作業種別 : 通常変更",
        "- 作業種別:通常変更",
        "- 作業種別:  通常変更",
        "-  作業種別: 通常変更",
        "- 作業種別: 通常変更 ",
    ],
)
def test_work_type_requires_exact_fixed_syntax(
    work_type_line: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = f"{_plan_metadata(None)}\n{work_type_line}"
    plan = _write_plan(tmp_path, f"## 背景\n\n{metadata}\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    stderr = capsys.readouterr().err
    assert "固定記法`- 作業種別: <固定値>`と一致しない" in stderr
    assert "作業種別が現行契約と一致しない" not in stderr


def test_multiple_work_types_warn_without_inferring_bug_plan(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = f"{_plan_metadata('バグ対応')}\n- 作業種別: 通常変更"
    plan = _write_plan(tmp_path, f"## 背景\n\n{metadata}\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    stderr = capsys.readouterr().err
    assert "作業種別が複数ある" in stderr
    assert "バグ計画に必須のバグ調査結果表が存在しない" not in stderr


@pytest.mark.parametrize(
    ("first_work_type", "second_work_type"),
    [("バグ対応", "通常変更"), ("通常変更", "バグ対応")],
)
def test_duplicate_metadata_sections_warn_without_inferring_bug_plan(
    first_work_type: str,
    second_work_type: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同じ背景にある同名メタ情報節を出現順にかかわらずすべて検査する。"""
    metadata = f"{_plan_metadata(first_work_type)}\n\n{_plan_metadata(second_work_type)}"
    plan = _write_plan(tmp_path, f"## 背景\n\n{metadata}\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    stderr = capsys.readouterr().err
    assert "作業種別が複数ある" in stderr
    assert "バグ計画に必須のバグ調査結果表が存在しない" not in stderr


@pytest.mark.parametrize("indent", ["    ", "\t"])
def test_indented_pseudo_metadata_heading_does_not_add_work_type(
    indent: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """コードブロック相当の疑似見出し配下を計画メタ情報として検査しない。"""
    body = f"## 背景\n\n{_plan_metadata('通常変更')}\n\n### 現状\n\n{indent}### 計画メタ情報\n\n- 作業種別: バグ対応\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("closing", ["", " ###"])
@pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
def test_metadata_heading_with_commonmark_indent_and_closing_sequence_is_accepted(
    indent: str,
    closing: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _plan_metadata("通常変更").replace("### 計画メタ情報", f"{indent}### 計画メタ情報{closing}")
    plan = _write_plan(tmp_path, f"## 背景\n\n{metadata}\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("marker", ["*", "+"])
def test_noncanonical_work_type_marker_warns_without_inferring_bug_plan(
    marker: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = f"{_plan_metadata(None)}\n{marker} 作業種別: バグ対応"
    plan = _write_plan(tmp_path, f"## 背景\n\n{metadata}\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    stderr = capsys.readouterr().err
    assert "計画メタ情報に作業種別の記載が無い" in stderr
    assert "バグ計画に必須のバグ調査結果表が存在しない" not in stderr


@pytest.mark.parametrize("outer_pipes", [True, False])
@pytest.mark.parametrize("indent", ["    ", "\t"])
def test_indented_code_block_table_is_not_accepted_as_investigation_table(
    indent: str,
    outer_pipes: bool,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = "\n".join(f"{indent}{line}" for line in _pipe_table_lines(outer_pipes=outer_pipes))
    body = f"## 背景\n\n{_plan_metadata('バグ対応')}\n\n### バグ調査結果\n\n{table}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "バグ調査結果表の列構造が現行契約と一致しない" in capsys.readouterr().err


@pytest.mark.parametrize("outer_pipes", [True, False])
@pytest.mark.parametrize("indent", ["", " ", "   "])
def test_markdown_table_with_up_to_three_spaces_is_accepted(
    indent: str,
    outer_pipes: bool,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = "\n".join(f"{indent}{line}" for line in _pipe_table_lines(outer_pipes=outer_pipes))
    body = f"## 背景\n\n{_plan_metadata('バグ対応')}\n\n### バグ調査結果\n\n{table}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("outer_pipes", [True, False])
def test_table_search_skips_pipe_text_before_valid_table(
    outer_pipes: bool,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = "\n".join(_pipe_table_lines(outer_pipes=outer_pipes))
    body = f"## 背景\n\n{_plan_metadata('バグ対応')}\n\n### バグ調査結果\n\n補足A | 補足B\n\n{table}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("outer_pipes", [True, False])
def test_table_search_reports_mismatched_header_and_separator_before_valid_table(
    outer_pipes: bool,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mismatched = "| 補足A | 補足B | 補足C |\n| --- | --- |" if outer_pipes else "補足A | 補足B | 補足C\n--- | ---"
    table = "\n".join(_pipe_table_lines(outer_pipes=outer_pipes))
    body = f"## 背景\n\n{_plan_metadata('バグ対応')}\n\n### バグ調査結果\n\n{mismatched}\n\n{table}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 1
    assert "表の区切り行のセル数がヘッダーと一致しない" in capsys.readouterr().err


@pytest.mark.parametrize("valid_first", [True, False])
def test_duplicate_investigation_sections_warn_in_both_orders(
    valid_first: bool,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = _bug_investigation_table(_BUG_ROWS)
    invalid = _bug_investigation_table(_BUG_ROWS[:-1])
    sections = [valid, invalid] if valid_first else [invalid, valid]
    body = f"## 背景\n\n{_plan_metadata('バグ対応')}\n\n{'\n\n'.join(sections)}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "`### バグ調査結果`が複数ある" in capsys.readouterr().err


@pytest.mark.parametrize("location", ["title", "background", "provided_material", "request"])
def test_bug_work_type_without_investigation_table_warns_for_all_content_locations(
    location: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title = "# バグ修正計画" if location == "title" else "# 機能変更計画"
    background_parts = {
        "background": "### 現状\n\n不具合が発生している。",
        "provided_material": "### 提示素材\n\nユーザーの依頼は不具合を修正すること。",
        "request": "### 依頼内容\n\n障害への対応を依頼された。",
    }
    background = background_parts.get(location, "### 現状\n\n通常の機能変更を行う。")
    plan = _write_plan(tmp_path, f"{title}\n\n## 背景\n\n{_plan_metadata('バグ対応')}\n\n{background}\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    stderr = capsys.readouterr().err
    assert stderr.startswith("[warn] ")
    assert "バグ計画に必須のバグ調査結果表が存在しない" in stderr


def test_bug_plan_marker_after_fenced_h2_in_provided_material_is_detected(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        f"## 背景\n\n{_plan_metadata('バグ対応')}\n\n### 提示素材\n\n"
        "````text\n## 実行方法\n\n例示\n````\n\nフェンス後に不具合が発生している。\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "バグ計画に必須のバグ調査結果表が存在しない" in capsys.readouterr().err


def test_bug_request_in_fenced_provided_material_warns_without_investigation_table(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = f"## 背景\n\n{_plan_metadata('バグ対応')}\n\n### 提示素材\n\n```text\n発生しているエラーを修正する。\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "バグ計画に必須のバグ調査結果表が存在しない" in capsys.readouterr().err


def test_bug_plan_metadata_marker_warns_without_investigation_table(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = f"## 背景\n\n{_plan_metadata('バグ対応')}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "バグ計画に必須のバグ調査結果表が存在しない" in capsys.readouterr().err


@pytest.mark.parametrize(
    "background",
    [
        "### 現状\n\n新しいサブコマンドへ失敗時処理を追加する。",
        "### 現状\n\n新しいサブコマンドへエラー処理を追加する。",
        "### 現状\n\nエラー出力の仕様を定義する。",
        "### 提示素材\n\n```text\n新しいサブコマンドへエラー処理を追加する。\n```",
    ],
)
def test_non_bug_failure_handling_context_has_no_bug_warning(
    background: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _write_plan(
        tmp_path,
        f"# 機能追加計画\n\n## 背景\n\n{_plan_metadata('通常変更')}\n\n{background}\n",
    )
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""


def test_non_bug_plan_without_investigation_table_has_no_bug_warning(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_plan(tmp_path, f"## 背景\n\n{_plan_metadata('通常変更')}\n\n### 現状\n\n機能を追加する。\n")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "rows",
    [
        _BUG_ROWS[:-1],
        [*_BUG_ROWS[:8], _BUG_ROWS[9], _BUG_ROWS[8], *_BUG_ROWS[10:]],
        ["根本原因", "直接原因の候補", "修正方針", "再発防止策", "類似見直し結果", "知見の記録"],
    ],
)
def test_bug_investigation_table_warns_for_missing_order_or_legacy_rows(
    rows: list[str],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = f"# 不具合修正計画\n\n## 背景\n\n{_plan_metadata('バグ対応')}\n\n{_bug_investigation_table(rows)}\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    stderr = capsys.readouterr().err
    assert stderr.startswith("[warn] ")
    assert "バグ調査結果表の必須行または順序が現行契約と一致しない" in stderr
    assert "不足=" in stderr
    assert "実際=" in stderr
    assert "期待=" in stderr


def test_bug_investigation_table_in_fence_is_ignored(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fenced_table = _bug_investigation_table(_BUG_ROWS)
    body = f"# バグ修正計画\n\n## 背景\n\n{_plan_metadata('バグ対応')}\n\n````markdown\n{fenced_table}\n````\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 0
    assert "バグ計画に必須のバグ調査結果表が存在しない" in capsys.readouterr().err


def test_no_violation_returns_zero_without_stderr(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n```text\n[新設]\ncontent\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_warning_only_returns_zero_with_warn_prefix(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 実行方法\n\n- `agent-toolkit:session-review`で振り返りを実施する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    stderr = capsys.readouterr().err
    assert stderr.startswith("[warn] ")
    assert "セッション運用工程" in stderr


@pytest.mark.parametrize(
    ("target_path", "description", "expect_warning"),
    [
        pytest.param(
            "agent-toolkit/.claude-plugin/plugin.json",
            "更新後の版は1.2.3とする。\n",
            True,
            id="manifest_with_version_number",
        ),
        pytest.param(
            ".claude-plugin/marketplace.json",
            "更新種別はPATCH・MINOR・MAJORから選ぶ。\n",
            False,
            id="manifest_with_update_types_only",
        ),
        pytest.param(
            "foo.md",
            "更新後の版は1.2.3とする。\n",
            False,
            id="non_manifest_with_version_number",
        ),
        pytest.param(
            ".claude-plugin/marketplace.json",
            "```text\n更新後の版は1.2.3とする。\n```\n",
            False,
            id="manifest_with_fenced_version_number",
        ),
    ],
)
def test_version_number_warning(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    target_path: str,
    description: str,
    expect_warning: bool,
) -> None:
    """版更新正本の有無と数値の記載位置に応じてwarningを切り替える。"""
    body = (
        f"## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `{target_path}`（新設）\n\n"
        f"{description}\n### `{target_path}`\n\n```text\ncontent\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    stderr = capsys.readouterr().err
    assert ("バージョン数値の記載の疑い" in stderr) is expect_warning
    if expect_warning:
        assert stderr.startswith("[warn] ")


def test_error_returns_one_without_warn_prefix(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    stderr = capsys.readouterr().err
    assert "H3見出しが無い対象ファイル" in stderr
    assert not stderr.startswith("[warn] ")


def test_no_argument_returns_two_with_usage(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["check_plan_file.py"])
    assert main() == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("usage: check_plan_file.py")
    assert "plan_file" in captured.err


def test_nonexistent_plan_file_returns_two(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = tmp_path / "missing.md"
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 2
    assert "計画ファイルを読み込めない" in capsys.readouterr().err


def test_invalid_utf8_plan_file_returns_two(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = tmp_path / "invalid.md"
    plan.write_bytes(b"\xff")
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 2
    assert "計画ファイルを読み込めない" in capsys.readouterr().err


def test_duplicate_paths_return_one(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `foo.md`（新設）\n- [ ] `foo.md`（新設）\n\n"
        "### `foo.md`\n\n```text\nfirst\n```\n\n"
        "### `foo.md`\n\n```text\nsecond\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    stderr = capsys.readouterr().err
    assert "対象ファイル一覧に重複したパス" in stderr
    assert "重複したH3見出し" in stderr


def test_nonexistent_absolute_h3_path_returns_one(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing" / "foo.md"
    body = f"## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `{missing}`\n\n### `{missing}`\n\n```text\ncontent\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "実在確認できないパス" in capsys.readouterr().err


def test_h3_without_code_block_returns_one(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n変更後の説明のみ。\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "コードブロックが無いH3" in capsys.readouterr().err


def test_h3_without_checkbox_returns_one(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "foo.md"
    existing.write_text("content\n", encoding="utf-8")
    body = f"## 変更内容\n\n### 対象ファイル一覧\n\n### `{existing}`\n\n```text\ncontent\n```\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "対象ファイル一覧に無いH3見出し" in capsys.readouterr().err


def test_tilde_code_block_returns_zero(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n~~~text\ncontent\n~~~\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("indent", [" ", "  ", "   "])
def test_indented_fenced_code_block_returns_zero(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    indent: str,
) -> None:
    """1〜3文字インデントしたフェンスをH3配下のコードブロックとして認識する。"""
    body = (
        _REFERENCE_ENUMERATION_SECTION + "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        f"### `foo.md`\n\n{indent}```text\n本ファイルを削除する。\n{indent}```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("following_heading", ["## 実行方法", "### 補足", "#### 詳細"])
def test_h3_body_ends_at_any_supported_heading(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    following_heading: str,
) -> None:
    """対象H3より後のH2〜H4配下にあるフェンスを対象H3のコードブロックとみなさない。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
        f"### `foo.md`\n\n変更後の説明のみ。\n\n{following_heading}\n\n```text\n無関係なコードブロック\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "コードブロックが無いH3: foo.md" in capsys.readouterr().err


def test_h4_code_block_does_not_belong_to_parent_h3(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """H4小節のコードブロックを親H3の変更内容として流用しない。"""
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n### `foo.md`\n\n#### 詳細\n\n```text\n変更後\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])

    assert main() == 1
    assert "コードブロックが無いH3: foo.md" in capsys.readouterr().err


def test_repeated_execution_method_sections_are_all_checked(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """同名H2が再出現しても、先行する節の呼び出し名検査を保持する。"""
    body = (
        "## 実行方法\n\n- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n\n"
        "## 実行方法\n\n- 実装する\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "実在しないスキル名の疑い" in capsys.readouterr().err


def test_embedded_and_actual_same_h3_uses_actual_section(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
        "````markdown\n### `foo.md`\n\n埋め込み側にはコードブロックが無い。\n````\n\n"
        "### `foo.md`\n\n~~~text\ncontent\n~~~\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_fenced_example_in_execution_method_is_ignored(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 実行方法\n\n- 実装する\n\n````markdown\n"
        "- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n"
        "- `agent-toolkit:session-review`で振り返りを実施する\n"
        "````\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    stderr = capsys.readouterr().err
    assert "実在しないスキル名の疑い" not in stderr
    assert "セッション運用工程" not in stderr


def test_retroactive_scan_words_in_fenced_example_do_not_satisfy_requirement(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "## 調査結果\n\n````text\n対象パターン\n検出件数\n対応方針\n````\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `agent-toolkit/rules/foo.md`（新設）\n\n"
        "### `agent-toolkit/rules/foo.md`\n\n```text\n+- 起動時にnameを指定しない\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    assert "遡及スキャン記録の不足の疑い" in capsys.readouterr().err


def test_entire_plan_structure_in_markdown_fence_returns_zero(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "# 記述例\n\n````markdown\n## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `does/not/exist.md`\n\n### `does/not/exist.md`\n\n"
        "```text\ncontent\n```\n\n## 実行方法\n\n"
        "- Skillツールで`agent-toolkit:no-such-skill`を呼び出す\n````\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_embedded_h2_with_session_operation_is_not_a_section(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "````markdown\n## 実行方法\n\n"
        "- `agent-toolkit:session-review`で振り返りを実施する\n````\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    stderr = capsys.readouterr().err
    assert "セッション運用工程" not in stderr


def test_only_unfenced_violation_is_reported(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    outer = tmp_path / "outer-missing.md"
    body = (
        "````markdown\n## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `embedded-missing.md`\n\n### `embedded-missing.md`\n\n```text\ncontent\n```\n````\n\n"
        f"## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `{outer}`\n\n"
        f"### `{outer}`\n\n```text\ncontent\n```\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 1
    stderr = capsys.readouterr().err
    assert str(outer) in stderr
    assert "embedded-missing.md" not in stderr


def test_deprecated_identifier_list_in_fence_is_ignored(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "leftover.py").write_text("def _leftover_helper():\n    pass\n", encoding="utf-8")
    body = "````markdown\n#### 廃止・改名対象一覧\n\n- `_leftover_helper`\n````\n\n## 変更内容\n\n### 対象ファイル一覧\n"
    plan = _write_plan(tmp_path, body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_deleted_checkbox_in_fence_is_ignored(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "````markdown\n### 対象ファイル一覧\n\n- [ ] `foo.md`（廃止・削除）\n\n"
        "### `foo.md`\n\n```text\n節を圧縮する\n```\n````\n\n"
        "## 変更内容\n\n### 対象ファイル一覧\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""


def test_entire_plan_structure_in_tilde_fence_returns_zero(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "~~~~markdown\n## 変更内容\n\n### 対象ファイル一覧\n\n"
        "- [ ] `does/not/exist.md`（廃止・削除）\n\n"
        "#### 廃止・改名対象一覧\n\n- `_leftover_helper`\n\n"
        "### `does/not/exist.md`\n\n```text\n節を圧縮する\n```\n"
        "~~~~\n"
    )
    plan = _write_plan(tmp_path, body)
    monkeypatch.setattr("sys.argv", ["check_plan_file.py", str(plan)])
    assert main() == 0
    assert capsys.readouterr().err == ""
