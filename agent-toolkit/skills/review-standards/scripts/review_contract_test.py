"""review_contract validatorの利用シナリオを検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

import pytest
import review_contract
import yaml

from agent_toolkit._atk import run_script
from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing import git_repository


def _contract() -> dict[str, Any]:
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
    assert review_contract.main(["--contract", str(contract_path)]) == 0


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
    assert review_contract.main(["--contract", str(contract_path)]) == 2
    assert "次の操作:" in capsys.readouterr().err


def test_generate_contracts_keeps_authorization_and_rewritten_commits(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開CLIから計画別の認可を生成し、rebase後は現行OIDだけを契約へ渡す。"""
    repository = git_repository.init_repository(tmp_path / "repo", initial_branch="lane", commit_message="基点")
    base = git_repository.git_output(repository, "rev-parse", "HEAD")
    plans, commits, items = [], [], []
    for index in (1, 2):
        (repository / f"file{index}.txt").write_text(f"本文{index}\n", encoding="utf-8")
        commit = git_repository.commit_all(repository, f"実装{index}")
        plan = tmp_path / f"計画{index}.md"
        wi = f"20261008-000000-00{index}.md"
        plan.write_text(
            f"# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 起動経路: `agent-toolkit:process-wi`\n"
            f"- 対象リポジトリ: `{repository}`\n- 関連WI:\n  - {wi}: 要求\n- 作業種別: 通常変更\n",
            encoding="utf-8",
        )
        commit_mapping.append_event(plan, {"commit": commit, "awi": [wi]})
        plans.append(plan)
        commits.append(commit)
        items.append(
            {"plan": str(plan), "source": f"認可資料{index}", "scope": f"file{index}.txt", "clauses": _contract()["clauses"]}
        )
    inputs, outputs = tmp_path / "input.json", tmp_path / "contracts"
    inputs.write_text(json.dumps({"version": 1, "plans": items}), encoding="utf-8")
    args = argparse.Namespace(
        script_name="review-contract", script_args=["--generate", str(inputs), "--output-dir", str(outputs)]
    )
    monkeypatch.chdir(repository)
    assert run_script.dispatch(args) == 0, capsys.readouterr().err
    returned = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    for index, result in enumerate(returned):
        text = pathlib.Path(result["contract"]).read_text(encoding="utf-8")
        contract = yaml.safe_load(text)
        assert contract["clauses"][0] == _contract()["clauses"][0]
        assert commits[index] not in text and commits[1 - index] in text
        assert f"認可資料{2 - index}" in text and f"file{2 - index}.txt" in text
        assert review_contract.main(["--contract", result["contract"]]) == 0
    git_repository.git_output(repository, "switch", "-c", "upstream", base)
    (repository / "upstream.txt").write_text("上流\n", encoding="utf-8")
    upstream = git_repository.commit_all(repository, "上流")
    git_repository.git_output(repository, "switch", "lane")
    git_repository.git_output(repository, "rebase", "--onto", upstream, base)
    rewritten = git_repository.git_output(repository, "rev-list", "--reverse", f"{upstream}..HEAD").splitlines()
    for plan, old, new in zip(plans, commits, rewritten, strict=True):
        commit_mapping.append_event(plan, {"rewrite": {old: new}})
    assert run_script.dispatch(args) == 0, capsys.readouterr().err
    returned = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    for index, result in enumerate(returned):
        text = pathlib.Path(result["contract"]).read_text(encoding="utf-8")
        assert all(old not in text for old in commits)
        assert rewritten[index] not in text and rewritten[1 - index] in text

    commit_mapping.mapping_path(plans[1]).unlink()
    assert run_script.dispatch(args) == 2
    assert "実装commitの記録がありません" in capsys.readouterr().err
