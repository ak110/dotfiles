"""受理済み判定の参照を公開レビュー更新と返却前確認から検証する。"""

from __future__ import annotations

import json
import pathlib

import check_exec_review_evidence as check
import pytest

from agent_toolkit._testing import git_repository as git


def _inputs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, pathlib.Path, pathlib.Path, pathlib.Path]:
    repository = git.init_repository(tmp_path / "repo", commit_message="検証の基準")
    head = git.git_output(repository, "rev-parse", "HEAD")
    monkeypatch.chdir(repository)
    rows = [
        {
            "awi": f"20261010-100000-00{number}.md",
            "condition": f"保存契約{number}",
            "outcome": "",
            "source": f"要求の出所{number}",
            "evidence": "",
            "reviewed_head": "",
        }
        for number in (1, 2)
    ]
    current = tmp_path / "current.json"
    current.write_text(json.dumps({"wi_conditions": rows, "user_requirements": []}, ensure_ascii=False), encoding="utf-8")
    accepted = tmp_path / "accepted.json"
    accepted.write_text(
        json.dumps(
            {
                "wi_conditions": [
                    {
                        **row,
                        "outcome": "達成",
                        "reviewed_head": head,
                        "evidence": "回収済みの根拠 /gone/proof.txt:1 は以前の判定で確認した。",
                    }
                    for row in rows
                ],
                "user_requirements": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reason = tmp_path / "reason.txt"
    reason.write_text("今回の差分は対象の成果物、試験の入力と実行条件を変えていない。", encoding="utf-8")
    return head, current, accepted, reason


def _update_args(head: str, current: pathlib.Path, accepted: pathlib.Path, reason: pathlib.Path) -> list[str]:
    return [
        str(current),
        "--output",
        str(current),
        "--select-row",
        "wi_conditions:1",
        "--select-row",
        "wi_conditions:2",
        "--row-outcome",
        "wi_conditions:1",
        "達成",
        "--row-outcome",
        "wi_conditions:2",
        "達成",
        "--reviewed-head",
        head,
        "--mode",
        "replace",
        "--evidence-file",
        str(reason),
        "--accepted-evidence",
        str(accepted),
        "--accepted-row",
        "wi_conditions:1",
        "wi_conditions:1",
        "--accepted-row",
        "wi_conditions:2",
        "wi_conditions:2",
    ]


def _check_args(tmp_path: pathlib.Path, head: str, current: pathlib.Path) -> list[str]:
    inputs = tmp_path / "inputs.txt"
    inputs.write_text("レビュー対象", encoding="utf-8")
    return [str(current), "--input-record", str(inputs), "--expected-head", head]


def test_public_review_references_accepted_rows(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """異なるWIの複数行を共通の説明で記録し、旧根拠の回収後も判定を参照できる。"""
    head, current, accepted, reason = _inputs(tmp_path, monkeypatch)
    source_bytes = accepted.read_bytes()
    assert check.main(_update_args(head, current, accepted, reason)) == 0
    capsys.readouterr()
    rows = json.loads(current.read_text(encoding="utf-8"))["wi_conditions"]
    for number, row in enumerate(rows, start=1):
        assert row["outcome"] == "達成" and row["reviewed_head"] == head
        reference = json.loads(row["evidence"].splitlines()[0].removeprefix("受理済み判定: "))
        assert reference == {
            "path": str(accepted),
            "section": "wi_conditions",
            "row": number,
            "outcome": "達成",
            "reviewed_head": head,
        }
        assert reason.read_text(encoding="utf-8") in row["evidence"]
        assert "回収済みの根拠" not in row["evidence"]
    assert check.main(_check_args(tmp_path, head, current)) == 0
    assert accepted.read_bytes() == source_bytes
    rewrite = tmp_path / "rewrite.json"
    rewrite.write_text(json.dumps({head: "b" * 40}), encoding="utf-8")
    before = current.read_bytes()
    assert check.main([str(current), "--rewrite-map", str(rewrite)]) == 0
    assert current.read_bytes() == before


@pytest.mark.parametrize("invalid", ["unjudged", "wi", "condition", "missing", "section", "pending"])
def test_public_review_rejects_invalid_accepted_rows_without_write(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """1行の対応不成立でも全更新を保存せず、未判定継承の拒否も維持する。"""
    head, current, accepted, reason = _inputs(tmp_path, monkeypatch)
    payload = json.loads(accepted.read_text(encoding="utf-8"))
    row = payload["wi_conditions"][0]
    if invalid == "unjudged":
        row["reviewed_head"] = ""
    elif invalid == "wi":
        row["awi"] = "20261010-999999-001.md"
    elif invalid == "condition":
        row["condition"] = "異なる条件"
    accepted.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    args = _update_args(head, current, accepted, reason)
    if invalid == "missing":
        accepted.unlink()
    elif invalid == "section":
        args[args.index("--accepted-row") + 2] = "user_requirements:1"
    elif invalid == "pending":
        args = [
            str(current),
            "--output",
            str(current),
            "--select-row",
            "wi_conditions:1",
            "--row-outcome",
            "wi_conditions:1",
            "達成",
            "--reviewed-head",
            head,
            "--mode",
            "replace",
            "--verification-record",
            str(accepted),
            "--verification-row",
            "wi_conditions:1",
            "wi_conditions:1",
        ]
    before = current.read_bytes()
    assert check.main(args) == 1
    captured = capsys.readouterr()
    assert "次の操作:" in captured.err
    assert current.read_bytes() == before


@pytest.mark.parametrize("invalid", ["missing", "other-row"])
def test_public_accepted_reference_check_does_not_reopen_old_artifacts(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """旧根拠のファイルは読まず、参照先の証拠と要求単位そのものの不成立を拒否する。"""
    head, current, accepted, reason = _inputs(tmp_path, monkeypatch)
    assert check.main(_update_args(head, current, accepted, reason)) == 0
    assert check.main(_check_args(tmp_path, head, current)) == 0
    capsys.readouterr()
    if invalid == "missing":
        accepted.unlink()
    else:
        payload = json.loads(current.read_text(encoding="utf-8"))
        row = payload["wi_conditions"][0]
        lines = row["evidence"].splitlines()
        reference = json.loads(lines[0].removeprefix("受理済み判定: "))
        reference["row"] = 2
        lines[0] = "受理済み判定: " + json.dumps(reference, ensure_ascii=False)
        row["evidence"] = "\n".join(lines)
        current.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert check.main(_check_args(tmp_path, head, current)) == 1
    assert "受理済み" in capsys.readouterr().err or invalid == "missing"
