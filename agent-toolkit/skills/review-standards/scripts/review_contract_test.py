"""review_contract validatorの利用シナリオを検証する。"""

from __future__ import annotations

import pathlib
import subprocess

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
        "commit_references": ["abc1234"],
        "awi_references": ["2026-09-21_request.md"],
    }


def test_validate_resolves_commit_and_awi_references(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(review_contract.subprocess, "run", run)
    review_contract.validate(_contract(), tmp_path)

    assert calls[0][-3:] == ["cat-file", "-e", "abc1234^{commit}"]
    assert calls[1][0:4] == ["atk", "wi", "show", "2026-09-21_request.md"]


def test_validate_rejects_undeclared_clause_reference(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    contract = _contract()
    contract["clauses"] = [{"clause": "CLI契約", "content": "要求を満たす", "source": "計画"}]
    monkeypatch.setattr(review_contract.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("実体確認へ進んだ"))

    with pytest.raises(review_contract.ContractError, match="宣言した参照"):
        review_contract.validate(contract, tmp_path)


def test_main_reads_yaml_and_rejects_missing_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    contract_path = tmp_path / "review-contract.yaml"
    contract_path.write_text(yaml.safe_dump(_contract(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(
        review_contract.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", "missing"),
    )

    assert review_contract.main(["--contract", str(contract_path), "--target-repo", str(tmp_path)]) == 2


def test_validate_rejects_missing_awi(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1 if command[0] == "atk" else 0, "", "missing")

    monkeypatch.setattr(review_contract.subprocess, "run", run)

    with pytest.raises(review_contract.ContractError, match="AWI参照"):
        review_contract.validate(_contract(), tmp_path)


def test_validate_rejects_invalid_structure(tmp_path: pathlib.Path) -> None:
    contract = _contract()
    contract["clauses"] = [{"clause": "CLI契約", "content": "要求を満たす"}]

    with pytest.raises(review_contract.ContractError, match="キーが不正"):
        review_contract.validate(contract, tmp_path)
