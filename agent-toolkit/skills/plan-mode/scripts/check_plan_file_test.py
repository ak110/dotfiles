"""簡素化した計画構造検査の回帰テスト。"""

from __future__ import annotations

import pathlib
import subprocess

import check_plan_file
import pytest

_BASE = "a" * 40


def _plan(path: str = "existing.py", *, suffix: str = "（現行1行）", body: str = "変更する") -> str:
    return f"""# 主題

## 背景

### 計画メタ情報

- ベースコミット: `{_BASE}`

## 変更内容

### 対象ファイル一覧

- [ ] `{path}`{suffix}

### `{path}`

```text
{body}
```

## 実行方法

- 検証する
"""


def _run(tmp_path: pathlib.Path, text: str) -> tuple[list[str], list[str]]:
    (tmp_path / "existing.py").write_text("x\n", encoding="utf-8")
    plan = tmp_path / "plan.md"
    plan.write_text(text, encoding="utf-8")
    return check_plan_file.check(plan, tmp_path, None)


def test_valid_plan_passes(tmp_path: pathlib.Path) -> None:
    assert _run(tmp_path, _plan()) == ([], [])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (_plan().replace("# 主題", "主題", 1), "H1"),
        (_plan() + "\n# 追加\n", "H1"),
        (_plan().replace("### `existing.py`\n", "", 1), "H3"),
        (_plan().replace("- [ ] `existing.py`（現行1行）\n", "", 1), "対象ファイル一覧"),
        (_plan().replace("```text\n変更する\n```", "変更する"), "コードブロック"),
        (_plan("missing.py"), "実在確認"),
    ],
)
def test_structural_errors(tmp_path: pathlib.Path, text: str, message: str) -> None:
    errors, _warnings = _run(tmp_path, text)
    assert any(message in error for error in errors)


def test_new_path_does_not_require_existence(tmp_path: pathlib.Path) -> None:
    assert _run(tmp_path, _plan("new.py", suffix="（新設）")) == ([], [])


def test_deleted_path_requires_delete_instruction(tmp_path: pathlib.Path) -> None:
    errors, _warnings = _run(tmp_path, _plan(suffix="（現行1行、廃止・削除）", body="変更する"))
    assert any("削除指示" in error for error in errors)


def test_deleted_path_with_instruction_passes(tmp_path: pathlib.Path) -> None:
    assert _run(tmp_path, _plan(suffix="（現行1行、廃止・削除）", body="削除する")) == ([], [])


def test_deleted_path_with_h3_suffix_passes(tmp_path: pathlib.Path) -> None:
    text = _plan(suffix="（廃止・削除）", body="削除する").replace("### `existing.py`", "### `existing.py`（廃止・削除）")
    assert _run(tmp_path, text) == ([], [])


def test_unclosed_fence_is_error(tmp_path: pathlib.Path) -> None:
    errors, _warnings = _run(tmp_path, _plan().removesuffix("```\n\n## 実行方法\n\n- 検証する\n"))
    assert any("フェンス" in error for error in errors)


def test_table_cell_mismatch_is_error(tmp_path: pathlib.Path) -> None:
    text = _plan() + "\n| A | B |\n| --- | --- |\n| x |\n"
    errors, _warnings = _run(tmp_path, text)
    assert any("セル数" in error for error in errors)


def test_missing_skill_reference_is_error(tmp_path: pathlib.Path) -> None:
    text = _plan().replace("- 検証する", "- Skillツールで`agent-toolkit:not-found`を起動する")
    errors, _warnings = _run(tmp_path, text)
    assert any("スキル参照" in error for error in errors)


def test_base_commit_changed_file_mismatch_is_warning(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "existing.py").write_text("x\n", encoding="utf-8")
    plan = tmp_path / "plan.md"
    plan.write_text(_plan(), encoding="utf-8")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "other.py\n", ""),
    )
    errors, warnings = check_plan_file.check(plan, tmp_path, _BASE)
    assert not errors
    assert len(warnings) == 1


def test_main_returns_two_for_unreadable_plan(tmp_path: pathlib.Path) -> None:
    assert check_plan_file.main([str(tmp_path / "missing.md")]) == 2
