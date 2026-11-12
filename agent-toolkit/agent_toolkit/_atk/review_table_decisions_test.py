"""公開respondが判断主体と移管先を検証し、旧記録を維持することを確かめる。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import review_table


@pytest.mark.parametrize("maker", ("メイン", "レーン担当", "統括", "ユーザー", "レビュー担当"))
def test_respond_records_decision_and_transfer(tmp_path: Path, maker: str) -> None:
    """各判断主体の移管を受理し、保存済みの旧本文を変えない。"""
    path = tmp_path / "current.exec-review.tsv"
    legacy = ["1", "exec-review", "file.py", "旧指摘", "詳細", "", "根拠の所在: メインの技術判断"]
    pending = ["1", "exec-review", "file.py", "新指摘", "仕様", "", ""]
    path.write_text(
        "".join("\t".join(json.dumps(cell, ensure_ascii=False) for cell in row) + "\n" for row in (legacy, pending)),
        encoding="utf-8",
    )
    body = f"根拠の所在: 次回の要求として登録した\n判断主体: {maker}\n移管先: 20261104-183257-001.md"
    reason = tmp_path / "reason.txt"
    reason.write_text(body, encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "respond", str(path), "--row-id=2", f"--no-response-reason-file={reason}"], home=tmp_path)
    assert result.value.code == 0
    rows = review_table.read_rows(path)
    assert rows == [legacy, [*pending[:6], body]]
    assert review_table.response_state(rows[1]) == "transferred"


@pytest.mark.parametrize(
    "metadata",
    (
        "",
        "判断主体:",
        "前置き 判断主体: メイン",
        "判断主体: その他",
        "判断主体: メインが判断",
        "判断主体: メイン\n判断主体: ユーザー",
        "判断主体: メイン\n移管先:",
        "判断主体: メイン\n移管先: ../20261104-183257-001.md",
        "判断主体: メイン\n移管先: 20261104-183257-001.md 補足",
        "判断主体: メイン\n移管先: 20261104-183257-001.md\n移管先: 20261104-183257-002.md",
    ),
)
def test_respond_rejects_invalid_decision_metadata(tmp_path: Path, metadata: str) -> None:
    """欠落・未知値・重複・パスを含む移管先は保存前に拒否する。"""
    path = tmp_path / "current.exec-review.tsv"
    review_table.add(path, "1", "exec-review", "file.py", "指摘")
    before = path.read_bytes()
    reason = tmp_path / "reason.txt"
    reason.write_text(f"根拠の所在: 計画の範囲\n{metadata}", encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "respond", str(path), "--row-id=1", f"--no-response-reason-file={reason}"], home=tmp_path)
    assert result.value.code != 0
    assert path.read_bytes() == before
