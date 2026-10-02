"""review_contract validatorの利用シナリオを検証する。"""

from __future__ import annotations

import pathlib

import pytest
import review_contract
import yaml


def _contract() -> dict[str, object]:
    return {
        "version": 1,
        "clauses": [
            {
                "clause": "CLI契約",
                "content": "abc1234の実装は要求を満たす",
                "source": "2026-09-21_request.md",
            }
        ],
    }


def test_main_accepts_clause_references_without_separate_declarations(tmp_path: pathlib.Path) -> None:
    """参照は条項を読む担当へ配送し、別配列への同期を要求しない。"""
    contract_path = tmp_path / "review-contract.yaml"
    contract_path.write_text(yaml.safe_dump(_contract(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert review_contract.main(["--contract", str(contract_path), "--target-repo", str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "contract",
    [
        {"version": 1, "clauses": []},
        {"version": 2, "clauses": [{"clause": "契約", "content": "内容", "source": "出典"}]},
        {"version": 1, "clauses": [{"clause": "契約", "content": "内容"}]},
        {"version": 1, "clauses": [{"clause": "契約", "content": " ", "source": "出典"}]},
        {**_contract(), "commit_references": []},
        {**_contract(), "awi_references": []},
    ],
)
def test_main_rejects_invalid_structure_with_next_action(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], contract: dict[str, object]
) -> None:
    """構造不正の配送を非0と修正操作で差し戻す。"""
    contract_path = tmp_path / "review-contract.yaml"
    contract_path.write_text(yaml.safe_dump(contract, allow_unicode=True), encoding="utf-8")
    assert review_contract.main(["--contract", str(contract_path), "--target-repo", str(tmp_path)]) == 2
    assert "次の操作:" in capsys.readouterr().err
