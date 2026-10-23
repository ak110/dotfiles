"""公開lane-receipt-checkから、返却本文と既存統合記録・Git実体の不一致を検出する。"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing import git_repository

_ADOPT = "20261023-000000-001.md"
_REJECT = "20261023-000000-002.md"
_DEFER = "20261023-000000-003.md"


@pytest.fixture(name="receipt_case")
def _receipt_case(tmp_path: pathlib.Path) -> dict[str, Any]:
    """lane integrateが保持するinput/completedと、同じstemのcommit対応原本を用意する。"""
    repo = git_repository.init_repository(
        tmp_path / "repo", initial_branch="main", files={"code.py": "pass\n"}, commit_message="基準"
    )
    before = git_repository.git_output(repo, "rev-parse", "HEAD")
    (repo / "AGENTS.md").write_text("# 規範\n", encoding="utf-8")
    after = git_repository.commit_all(repo, "規範を追加")
    plans = [tmp_path / "first.md", tmp_path / "second.md"]
    for plan in plans:
        plan.write_text("# 計画\n", encoding="utf-8")
    commit_mapping.mapping_path(plans[0]).write_text(json.dumps({"commit": after, "awi": [_DEFER]}) + "\n", encoding="utf-8")
    manifest = {
        "plans": [{"name": plan.name, "saved": str(plan), "commands": []} for plan in plans],
        "wis": [
            {"name": _ADOPT, "commands": [["atk", "wi", "adopt", _ADOPT, "--target-repo", str(repo)]]},
            {"name": _REJECT, "commands": [["atk", "wi", "reject", _REJECT, "--target-repo", str(repo)]]},
            {"name": _DEFER, "commands": []},
        ],
    }
    record = tmp_path / "lane-01.json"
    record.write_text(
        json.dumps(
            {
                "base_branch": "main",
                "integration": {
                    "phase": "finalized",
                    "input": {"target": str(repo), "branch": "main", "manifest": manifest, "no_merge": False},
                    "target_head": before,
                    "integrated_head": after,
                    "completed": [f"wis:{_ADOPT}:0", f"wis:{_REJECT}:0", "selection-mark"],
                },
            }
        ),
        encoding="utf-8",
    )
    rows = {
        "統合後のHEAD": after[:12],
        "保存した計画ファイル": json.dumps([plan.name for plan in plans]),
        "adoptしたWI": _ADOPT,
        "rejectしたWI": _REJECT,
        "adoptを延期したAWIとcommit": json.dumps([{"awi": _DEFER, "commit": after[:12]}]),
        "変更したエージェント向け文書": json.dumps(["AGENTS.md"]),
    }
    return {
        "repo": repo,
        "record": record,
        "reply": tmp_path / "reply.txt",
        "rows": rows,
        "before": before,
        "after": after,
        "plans": plans,
    }


def _invoke(case: dict[str, Any], capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str, str]:
    case["reply"].write_text(
        "統合完了\n" + "".join(f"{name}: {value}\n" for name, value in case["rows"].items()), encoding="utf-8"
    )
    with pytest.raises(SystemExit) as exited:
        atk.main(
            ["run-script", "lane-receipt-check", "--", "--record", str(case["record"]), "--reply", str(case["reply"]), *extra]
        )
    captured = capsys.readouterr()
    assert isinstance(exited.value.code, int)
    return exited.value.code, captured.out, captured.err


@pytest.mark.parametrize("empty_commit", [False, True])
def test_public_receipt_uses_original_record_and_commit_mapping(
    receipt_case: dict[str, Any], capsys: pytest.CaptureFixture[str], *, empty_commit: bool
) -> None:
    """既存原本だけを渡し、全集合の受理と、機械判定後に残る空commitの意味確認を得る。"""
    if empty_commit:
        commit_mapping.mapping_path(receipt_case["plans"][0]).unlink()
        receipt_case["rows"]["adoptを延期したAWIとcommit"] = json.dumps([{"awi": _DEFER, "commit": ""}])
    original = receipt_case["record"].read_bytes()
    code, output, diagnostic = _invoke(receipt_case, capsys)
    assert code == 0, diagnostic
    result = json.loads(output)
    assert result["head"] == receipt_case["after"]
    assert result["plans"] == ["first.md", "second.md"]
    assert result["documents"] == ["AGENTS.md"]
    assert result["empty_commit_wis"] == ([_DEFER] if empty_commit else [])
    assert receipt_case["record"].read_bytes() == original
    assert git_repository.git_output(receipt_case["repo"], "rev-parse", "HEAD") == receipt_case["after"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("保存した計画ファイル", "["),
        ("保存した計画ファイル", '["second.md","first.md"]'),
        ("保存した計画ファイル", '["first.md","first.md"]'),
        ("adoptしたWI", _DEFER),
        ("adoptを延期したAWIとcommit", json.dumps([{"awi": _DEFER, "commit": "", "extra": True}])),
        ("adoptを延期したAWIとcommit", json.dumps([{"awi": _DEFER, "commit": ""}])),
        ("変更したエージェント向け文書", "[]"),
        ("変更したエージェント向け文書", '["../AGENTS.md"]'),
        ("変更したエージェント向け文書", '["AGENTS.md","AGENTS.md"]'),
    ],
)
def test_public_receipt_rejects_invalid_arrays_and_mismatched_sets(
    receipt_case: dict[str, Any], capsys: pytest.CaptureFixture[str], field: str, value: str
) -> None:
    """型・キー・順序・集合・OIDの不適合を、原本を更新せず差戻し可能な失敗として返す。"""
    receipt_case["rows"][field] = value
    original = receipt_case["record"].read_bytes()
    code, output, diagnostic = _invoke(receipt_case, capsys)
    assert code == 1 and not output
    assert "次の操作:" in diagnostic
    assert receipt_case["record"].read_bytes() == original


@pytest.mark.parametrize("mismatch", ["head", "commit", "unfinished", "selection-mark"])
def test_public_receipt_checks_git_and_completed_finalization(
    receipt_case: dict[str, Any], capsys: pytest.CaptureFixture[str], mismatch: str
) -> None:
    """存在する別OIDと未完了の最終化を、返却の自己申告から独立して拒否する。"""
    if mismatch == "head":
        receipt_case["rows"]["統合後のHEAD"] = receipt_case["before"][:12]
    elif mismatch == "commit":
        receipt_case["rows"]["adoptを延期したAWIとcommit"] = json.dumps(
            [{"awi": _DEFER, "commit": receipt_case["before"][:12]}]
        )
    else:
        record = json.loads(receipt_case["record"].read_text(encoding="utf-8"))
        record["integration"]["completed"].remove("selection-mark" if mismatch == "selection-mark" else f"wis:{_ADOPT}:0")
        receipt_case["record"].write_text(json.dumps(record), encoding="utf-8")
    code, output, diagnostic = _invoke(receipt_case, capsys)
    assert code == 1 and not output and "次の操作:" in diagnostic


@pytest.mark.parametrize("condition", ["leave-open", "previous-reply", "plan", "handoff"])
def test_public_receipt_resolves_original_sources_and_missing_launch_facts(
    receipt_case: dict[str, Any], capsys: pytest.CaptureFixture[str], condition: str
) -> None:
    """原本に残る事実と、所在・累積返却・終端不要の明示指定を組み合わせて受領する。"""
    record = json.loads(receipt_case["record"].read_text(encoding="utf-8"))
    manifest = record["integration"]["input"]["manifest"]
    extra: list[str] = []
    if condition == "leave-open":
        extra = ["--leave-open", _DEFER]
        receipt_case["rows"]["adoptを延期したAWIとcommit"] = "[]"
    elif condition == "previous-reply":
        previous = receipt_case["reply"].with_name("previous.txt")
        _invoke(receipt_case, capsys)
        previous.write_bytes(receipt_case["reply"].read_bytes())
        record["integration"]["input"]["no_merge"] = True
        extra = ["--previous-reply", str(previous)]
    elif condition == "plan":
        manifest["plans"][0]["saved"] = "/missing/first.md"
        extra = ["--plan", str(receipt_case["plans"][0])]
    else:
        manifest["handoff"] = str(receipt_case["plans"][0])
        manifest["plans"] = []
        receipt_case["rows"]["保存した計画ファイル"] = "[]"
    receipt_case["record"].write_text(json.dumps(record), encoding="utf-8")
    code, output, diagnostic = _invoke(receipt_case, capsys, *extra)
    assert code == 0, diagnostic
    assert json.loads(output)["documents"] == ["AGENTS.md"]
