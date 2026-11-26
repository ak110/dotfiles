# pylint: disable=useless-import-alias  # pytestのfixtureを登録する。
import hashlib
import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.wi_add_support import _cmd_add_args, _patch_cmd_add_operations, _run_public_add
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_mutations_content_support import _edit_body_args
from agent_toolkit.atk_test import _FIXED_DT, _make_subprocess_fake, _setup_notes, _write_awi_file

_DRAFT = """# 全レビューで原要求を先に判定する

原文と結果を照合する。

## 反映内容と反映先

- WI・計画・実行・文書・ユーザビリティすべてを対象とする。

## 完成条件

- 原要求を満たさない成果物は詳細判定より先に不合格にする。

## ユーザー指摘の逐語引用

> 全レビューでユーザーの原要求を先に確認して。
"""


def _review_file(tmp_path: pathlib.Path, text: str, defect: str = "none") -> pathlib.Path:
    original = tmp_path / "original.md"
    quote = text.split("> ", 1)[-1]
    original.write_text(quote, encoding="utf-8")
    record: dict[str, object] = {
        "判定": "修正要" if defect == "reject" else "合格",
        "根拠": "WI起票前、計画、実行、文書、ユーザビリティを先頭の判定で覆う",
        "原稿SHA256": hashlib.sha256(text.encode()).hexdigest(),
        "原本": str(original),
        "原本SHA256": hashlib.sha256(quote.encode()).hexdigest(),
        "範囲対応": {
            "全レビュー": {
                "対象集合": "WI、計画、実行、文書、ユーザビリティ",
                "原稿行": 7,
                "根拠": "反映内容が全ての工程を対象にする",
            }
        }
        if defect != "missing_coverage" and "全レビュー" in quote
        else {},
    }
    if defect == "changed_group":
        other = tmp_path / "other.md"
        other.write_text("検収の後に変更して残りのレビューを外した原稿", encoding="utf-8")
        record["分割原稿"] = {str(other): hashlib.sha256("検収時の原稿".encode()).hexdigest()}
    path = tmp_path / "review.json"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    if defect == "changed_origin":
        original.write_text("別のユーザー要求", encoding="utf-8")
    return path


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize(
    "defect", ["missing", "stale", "reject", "missing_coverage", "changed_origin", "changed_group", "none", "no_scope"]
)
def test_add_requires_same_draft_review(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
    defect: str,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    draft = _DRAFT if defect != "no_scope" else _DRAFT.replace("全レビューで", "文書レビューで")
    args = _cmd_add_args(tmp_path, draft, source="test", dry_run=dry_run)
    if defect != "missing":
        review = _review_file(tmp_path, draft if defect != "stale" else draft.replace("原文", "発話", 1), defect)
        args.origin_review = [str(review)]

    valid = defect in {"none", "no_scope"}
    if valid:
        _run_public_add(args, notes, _FIXED_DT, tmp_path)
    else:
        with pytest.raises(SystemExit, match="1"):
            _run_public_add(args, notes, _FIXED_DT, tmp_path)
        assert "原要求の検収が不成立" in capsys.readouterr().err
    assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == int(valid and not dry_run)


@pytest.mark.parametrize("dry_run", [True, False])
def test_edit_of_legacy_quoted_draft_rejects_narrowed_title_without_review(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
) -> None:
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    saved = _write_awi_file(notes, "legacy.md", body=_DRAFT)
    before = saved.read_bytes()
    narrowed = _DRAFT.replace("全レビューで", "文書レビューで", 1)
    with pytest.raises(SystemExit, match="1"):
        atk.main([*_edit_body_args(tmp_path, saved.name, narrowed), *(["--dry-run"] if dry_run else [])], home=tmp_path)
    assert "原要求の検収が不成立" in capsys.readouterr().err
    assert saved.read_bytes() == before
