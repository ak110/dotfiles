"""`task_documents`の作業ツリーへの書込宣言と、起動本文からの役割文書の特定を確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common import task_documents


def _document(tmp_path: pathlib.Path, block: str) -> pathlib.Path:
    path = tmp_path / "role.subagent.md"
    path.write_text(f"# 担当\n\n## 入力\n\n```text\n{block}\n```\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(("value", "expected"), [("あり", True), ("なし", False)])
def test_worktree_write_declaration_is_read(tmp_path: pathlib.Path, value: str, expected: bool) -> None:
    """宣言値を真偽として返し、`mode:`など他の行の解析と両立する。"""
    declaration = task_documents.read_declaration_unchecked(
        _document(tmp_path, f"必須入力名: 対象\nmode: explore\n作業ツリーへの書込: {value}")
    )

    assert isinstance(declaration, task_documents.TaskDocumentDeclaration)
    assert declaration.writes_worktree is expected
    assert declaration.launch_kind == "explore"


@pytest.mark.parametrize(
    ("block", "reason"),
    [
        ("必須入力名: 対象", "`作業ツリーへの書込: <あり|なし>`の行を追加する"),
        ("必須入力名: 対象\n作業ツリーへの書込: 不明", "`作業ツリーへの書込:`の値が不正です: 不明"),
        ("必須入力名: 対象\n作業ツリーへの書込: 不明", "受理する値は`あり`か`なし`"),
    ],
)
def test_missing_or_invalid_worktree_write_is_a_format_violation(tmp_path: pathlib.Path, block: str, reason: str) -> None:
    """宣言の欠落と未知の値は書式違反として理由と文書を返す。"""
    path = _document(tmp_path, block)

    declaration = task_documents.read_declaration_unchecked(path)

    assert isinstance(declaration, str)
    assert reason in declaration and str(path) in declaration


def test_launched_task_document_round_trips_the_task_header(tmp_path: pathlib.Path) -> None:
    """`task`の見出しで始まる起動本文からだけ役割文書のパスを得る。"""
    document = tmp_path / "share" / "exec.subagent.md"
    prompt = task_documents.task_prompt_header(document) + "\n本文"

    assert task_documents.launched_task_document(prompt) == document


@pytest.mark.parametrize(
    "first_line",
    [
        "次の文書を読んで比べよ: /plugin/share/exec.subagent.md",
        "/plugin/share/exec.subagent.md の手順を実行せよ。",
        "次の文書の手順を実行せよ（出所: share/exec.subagent.md）。",
        "次の文書の手順を実行せよ（出所: /plugin/share/README.md）。",
    ],
)
def test_launched_task_document_ignores_other_first_lines(first_line: str) -> None:
    """文書へ言及するだけの自由本文、相対パス、役割文書以外は役割の起動と扱わない。"""
    assert task_documents.launched_task_document(first_line + "\n本文") is None


@pytest.mark.parametrize("frontmatter", [False, True])
def test_path_lists_declaration_preserves_both_document_formats(tmp_path: pathlib.Path, frontmatter: bool) -> None:
    """frontmatterと保持された旧宣言から同じ複数パスの契約を取得する。"""
    entry = {"instruction": "修正せよ", "required": ["表"], "path_lists": ["表"]}
    path = _document(
        tmp_path,
        "必須入力名: 対象\n作業ツリーへの書込: あり\n継続入力: " + json.dumps(entry, ensure_ascii=False),
    )
    if frontmatter:
        path.write_text(
            "---\nmode: delegate\nmodel_type: high_tier\nusage: execute\nrequired_inputs: [対象]\n"
            "writes_worktree: true\ncontinuations:\n  - " + json.dumps(entry, ensure_ascii=False) + "\n---\n",
            encoding="utf-8",
        )
    declaration = task_documents.read_declaration_unchecked(path)
    assert isinstance(declaration, task_documents.TaskDocumentDeclaration)
    assert declaration.continuations[0].path_lists == ("表",)
    assert not declaration.continuations[0].paths


@pytest.mark.parametrize("path_lists", ["表", ["宣言外"]])
def test_legacy_path_lists_reject_invalid_declaration(tmp_path: pathlib.Path, path_lists: str | list[str]) -> None:
    """旧形式も配列の型と宣言済みの入力名を検査する。"""
    entry = {"instruction": "修正せよ", "required": ["表"], "path_lists": path_lists}
    path = _document(
        tmp_path,
        "必須入力名: 対象\n作業ツリーへの書込: あり\n継続入力: " + json.dumps(entry, ensure_ascii=False),
    )
    declaration = task_documents.read_declaration_unchecked(path)
    assert isinstance(declaration, str)
    assert "継続入力の書式が不正" in declaration
