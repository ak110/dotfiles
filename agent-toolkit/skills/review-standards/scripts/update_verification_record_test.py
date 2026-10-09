"""公開run-scriptから未判定記録を生成・再生成・根拠更新する契約を検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from agent_toolkit._atk import review_table, run_script
from agent_toolkit._atk.wi import entries, repo, sync
from agent_toolkit._testing import git_repository

WI = "20261008-043514-001.md"


def _run(*args: str, name: str = "verification-record") -> int:
    return run_script.dispatch(argparse.Namespace(script_name=name, script_args=list(args)))


def _plan(tmp_path: pathlib.Path, mode: str) -> pathlib.Path:
    path = tmp_path / "計画.md"
    origin = f"エージェント由来のWI ({WI})" if mode == "plan-wi" else "ユーザー指示"
    path.write_text(
        "## 実施内容\n\n| 実施内容 | 由来 | 採否 | 根拠 |\n| --- | --- | --- | --- |\n"
        f"| 要約 | {origin} | 採用 | 原文を採用 |\n"
        + (
            ""
            if mode == "plan-wi"
            else "\n## 変更履歴\n\n### ユーザー発言1\n\n```text\n保存する。\n```\n\n"
            "### ユーザー発言2\n\n```text\n保存する。\n```\n"
        ),
        encoding="utf-8",
    )
    return path


def _wi(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(sync, "ensure_environment", lambda _home: tmp_path)
    monkeypatch.setattr(repo, "resolve_repo_id", lambda _path: "example")
    monkeypatch.setattr(
        entries,
        "read_named_entries",
        lambda _notes, _names, **_kwargs: (
            [
                (
                    tmp_path / WI,
                    "example",
                    "---\ntype: awi\nsource: agent\n---\n## 完成条件\n- 保存する。\n- 再読込する。\n",
                    "processing",
                    "awi",
                )
            ],
            [],
        ),
    )


def test_public_generation_guides_non_git_cwd_without_writing_and_recovers(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """未判定記録の生成も対象worktreeへ同じ引数を持ち込める案内を返す。"""
    _wi(monkeypatch, tmp_path)
    plan = _plan(tmp_path, "plan-user")
    record = tmp_path / "pending.json"
    monkeypatch.chdir(tmp_path)
    argv = ["--output", str(record), "--plan", str(plan)]
    assert _run(*argv) == 1
    assert not record.exists()
    assert "Git worktree" in capsys.readouterr().err
    repository = tmp_path / "repo"
    repository.mkdir()
    git_repository.init_repository(repository)
    monkeypatch.chdir(repository)
    assert _run(*argv) == 0
    assert len(json.loads(record.read_text())["user_requirements"]) == 2


@pytest.mark.parametrize("mode", ["wi", "plan-wi", "plan-user"])
def test_public_record_roundtrip(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """入力形ごとの原文集合が雛形と一致し、同文別出所の一方だけを更新・保持できる。"""
    _wi(monkeypatch, tmp_path)
    record, template = tmp_path / "record.json", tmp_path / "template.json"
    plan = _plan(tmp_path, mode)
    args = ["--wi", WI] if mode == "wi" else ["--plan", str(plan)]
    assert _run("--output", str(record), *args) == 0
    first = json.loads(record.read_text(encoding="utf-8"))
    template_args = [WI] if mode == "wi" else ["--plan", str(plan)]
    assert _run("--template", str(template), *template_args, name="exec-review-evidence-check") == 0
    assert json.loads(template.read_text(encoding="utf-8")) == first
    capsys.readouterr()
    assert _run("--output", str(record), "--list") == 0
    listed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(listed) == 2
    chosen = listed[1]
    assert chosen["original"] != "要約"
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("保存操作で再読込値を観測", encoding="utf-8")
    update = [
        "--output",
        str(record),
        "--section",
        chosen["section"],
        "--row",
        "2",
        "--source",
        chosen["source"],
        "--evidence-file",
        str(evidence),
    ]
    assert _run(*update, "--mode", "append") == 0
    evidence.write_text("別条件で再確認", encoding="utf-8")
    assert _run(*update, "--mode", "append") == 0
    added = json.loads(record.read_text(encoding="utf-8"))
    assert added[chosen["section"]][1]["evidence"] == "保存操作で再読込値を観測\n別条件で再確認"
    assert _run(*update, "--mode", "replace") == 0
    assert _run("--output", str(record), *args) == 0
    final = json.loads(record.read_text(encoding="utf-8"))
    expected = first
    expected[chosen["section"]][1]["evidence"] = "別条件で再確認"
    assert final == expected


def test_public_regeneration_adds_missing_row_and_keeps_existing_evidence(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じoutputの再生成で不足行を補い、原文別出所の既存根拠を保持する。"""
    plan = _plan(tmp_path, "plan-user")
    record = tmp_path / "pending.json"
    assert _run("--output", str(record), "--plan", str(plan)) == 0
    assert _run("--output", str(record), "--list") == 0
    listed = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("修正前から保持する根拠", encoding="utf-8")
    assert (
        _run(
            "--output",
            str(record),
            "--section",
            "user_requirements",
            "--row",
            "2",
            "--source",
            listed[1]["source"],
            "--evidence-file",
            str(evidence),
            "--mode",
            "replace",
        )
        == 0
    )
    before = json.loads(record.read_text(encoding="utf-8"))["user_requirements"]
    current = plan.read_text(encoding="utf-8")
    plan.write_text(current + "\n### ユーザー発言3\n\n```text\n不足行を追加する。\n```\n", encoding="utf-8")
    assert _run("--output", str(record), "--plan", str(plan)) == 0
    after = json.loads(record.read_text(encoding="utf-8"))["user_requirements"]
    assert after[:2] == before
    assert after[2]["requirement"] == "不足行を追加する。"
    assert all(row["outcome"] == row["reviewed_head"] == "" for row in after)


def test_public_reference_updates_and_inheritance_reach_final_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """両配列へ単一・配列更新した新参照が未判定根拠の継承を経て最終確認まで到達する。"""
    _wi(monkeypatch, tmp_path)
    repository = git_repository.init_repository(tmp_path / "repo", files={"base.txt": "基準\n"}, commit_message="基準")
    monkeypatch.chdir(repository)
    head = git_repository.git_output(repository, "rev-parse", "HEAD")
    plan = _plan(tmp_path, "plan-user")
    record, template = tmp_path / "pending.json", tmp_path / "review.json"
    assert _run("--output", str(record), "--wi", WI, "--plan", str(plan)) == 0
    assert _run("--template", str(template), WI, "--plan", str(plan), name="exec-review-evidence-check") == 0
    data = json.loads(record.read_text())
    observation = tmp_path / "saved output.txt"
    observation.write_text("保存結果\n再読込結果\n要求の保存結果\n要求の再確認結果\n", encoding="utf-8")
    updates, judged = [], []
    for section in ("wi_conditions", "user_requirements"):
        for index, row in enumerate(data[section], 1):
            number = index if section == "wi_conditions" else index + 2
            evidence = tmp_path / f"{section}-{index}.txt"
            evidence.write_text(f'stdout="{observation}:{number}": 観測した結果を確認\n', encoding="utf-8")
            update = {
                "section": section,
                "row": index,
                "source": row["source"],
                "evidence_file": str(evidence),
                "mode": "replace",
            }
            if index == 1:
                assert (
                    _run(
                        "--output",
                        str(record),
                        "--section",
                        section,
                        "--row",
                        str(index),
                        "--source",
                        row["source"],
                        "--evidence-file",
                        str(evidence),
                        "--mode",
                        "replace",
                    )
                    == 0
                )
            else:
                updates.append(update)
            judged.append(
                {
                    "section": section,
                    "row": index,
                    "source": row["source"],
                    "verification_source": row["source"],
                    "mode": "replace",
                    "outcome": "達成",
                    "reviewed_head": head,
                }
            )
    update_path = tmp_path / "updates.json"
    update_path.write_text(json.dumps(updates), encoding="utf-8")
    assert _run("--output", str(record), "--updates-file", str(update_path)) == 0
    preserved = record.read_bytes()
    assert _run("--output", str(record), "--wi", WI, "--plan", str(plan)) == 0
    assert record.read_bytes() == preserved
    update_path.write_text(json.dumps(judged), encoding="utf-8")
    assert (
        _run(
            str(template),
            "--verification-record",
            str(record),
            "--updates-file",
            str(update_path),
            "--output",
            str(template),
            name="exec-review-evidence-check",
        )
        == 0
    )
    assert _run(str(template), WI, "--plan", str(plan), "--expected-head", head, name="exec-review-evidence-check") == 0, (
        capsys.readouterr().err
    )
    assert record.read_bytes() == preserved


@pytest.mark.parametrize("invalid", ["source", "row", "outcome", "reviewed_head", "json", "empty-evidence", "missing-argument"])
def test_public_record_rejects_invalid_update_without_write(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """指定不成立・判定済み・不正書式は非0となり、記録のバイト列を保持する。"""
    record = tmp_path / "record.json"
    plan = _plan(tmp_path, "plan-user")
    assert _run("--output", str(record), "--plan", str(plan)) == 0
    data = json.loads(record.read_text(encoding="utf-8"))
    if invalid in {"outcome", "reviewed_head"}:
        data["user_requirements"][0][invalid] = "達成" if invalid == "outcome" else "a" * 40
        record.write_text(json.dumps(data), encoding="utf-8")
    if invalid == "json":
        record.write_text("{", encoding="utf-8")
    before = record.read_bytes()
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("" if invalid == "empty-evidence" else "観測", encoding="utf-8")
    args = [
        "--output",
        str(record),
        "--section",
        "user_requirements",
        "--row",
        "9" if invalid == "row" else "1",
        "--source",
        "別出所" if invalid == "source" else data["user_requirements"][0]["source"],
        "--evidence-file",
        str(evidence),
    ]
    if invalid != "missing-argument":
        args.extend(["--mode", "replace"])
    assert _run(*args) == 1
    assert record.read_bytes() == before
    assert "次の操作:" in capsys.readouterr().err


def _saved_diagnostics(tmp_path: pathlib.Path, diagnostics: list[dict], sources: dict[str, str], name: str) -> dict:
    """pyfltrの公開JSONL形式を有限コマンドで保存し、実行版と保存本文を付ける。"""
    producer = tmp_path / f"{name}_producer.py"
    producer.write_text("print(" + repr("\n".join(json.dumps(entry) for entry in diagnostics)) + ")\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_toolkit.atk",
            "run-command",
            "--cwd",
            str(tmp_path),
            "--timeout",
            "30",
            "--",
            sys.executable,
            str(producer),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    saved = json.loads(result.stdout[result.stdout.index("{") :])
    paths = {}
    for index, (filename, content) in enumerate(sources.items()):
        snapshot = tmp_path / f"{name}_{index}.txt"
        snapshot.write_text(content, encoding="utf-8")
        paths[filename] = str(snapshot)
    return {
        "path": saved["stdout_path"],
        "run_record": saved["record_path"],
        "head": saved["git_head"],
        "conditions": {"scope": ["対象"], "options": [], "dependencies": {}, "parallelism": 1, "environment": {"OS": "検証"}},
        "sources": paths,
    }


def test_public_diagnostics_comparison_distinguishes_movement_multiplicity_and_incomparability(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Gitの改名と保存本文の行移動、多重集合と位置なし警告を公開結果一覧で対応付ける。"""
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "試験担当")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    old_text = "\n".join(f"value_{number} = {number}" for number in range(20)) + "\n"
    (tmp_path / "old.py").write_text(old_text, encoding="utf-8")
    _git(tmp_path, "add", "old.py")
    _git(tmp_path, "commit", "--no-verify", "-m", "基準")

    def message(text: str, line: int | None) -> dict:
        return {"line": line, "col": 1 if line else None, "severity": "warning", "rule": "W001", "msg": text}

    before = _saved_diagnostics(
        tmp_path,
        [
            {"kind": "command", "command": "lint", "status": "succeeded"},
            {
                "kind": "diagnostic",
                "command": "lint",
                "file": "old.py",
                "messages": [message("同じ診断", 2), message("同じ診断", 2), message("旧内容", 3)],
            },
            {"kind": "diagnostic", "command": "lint", "file": None, "messages": [message(f"PID=123 cwd={tmp_path}", None)]},
        ],
        {"old.py": old_text},
        "before",
    )
    (tmp_path / "old.py").rename(tmp_path / "new.py")
    new_text = "# 追加行\n" + old_text
    (tmp_path / "new.py").write_text(new_text, encoding="utf-8")
    _git(tmp_path, "add", "--", "old.py", "new.py")
    _git(tmp_path, "commit", "--no-verify", "-m", "改名と行移動")
    after = _saved_diagnostics(
        tmp_path,
        [
            {
                "kind": "diagnostic",
                "command": "lint",
                "file": "new.py",
                "messages": [message("同じ診断", 3)] * 3 + [message("新内容", 4)],
            },
            {"kind": "diagnostic", "command": "lint", "file": None, "messages": [message(f"PID=456 cwd={tmp_path}", None)]},
        ],
        {"new.py": new_text},
        "after",
    )
    results = _json(tmp_path / "results.json", {"diagnostics": after, "baseline_diagnostics": before})
    output = tmp_path / "record.json"
    assert _run("--output", str(output), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    capsys.readouterr()
    assert _run("--output", str(output), "--results-file", str(results), "--list-results") == 0
    listed = json.loads(capsys.readouterr().out)["diagnostics"]
    assert len([key for key in listed if key.startswith("unchanged:")]) == 3
    assert len([key for key in listed if key.startswith("added:")]) == 2
    assert len([key for key in listed if key.startswith("deleted:")]) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    updates = _json(
        tmp_path / "updates.json",
        [
            {
                "section": "user_requirements",
                "row": 1,
                "source": payload["user_requirements"][0]["source"],
                "diagnostics": ["added:1"],
                "mode": "replace",
            }
        ],
    )
    assert _run("--output", str(output), "--results-file", str(results), "--updates-file", str(updates)) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))["user_requirements"][0]["evidence"]
    assert f"after.path: `{after['path']}`" in evidence
    capsys.readouterr()
    for change in ("sources", "conditions", "head"):
        current = (
            {**after, "sources": {}}
            if change == "sources"
            else {**after, "conditions": {**after["conditions"], "parallelism": 2}}
            if change == "conditions"
            else {**after, "head": "a" * 40}
        )
        _json(results, {"diagnostics": current, "baseline_diagnostics": before})
        if change == "head":
            assert _run("--output", str(output), "--results-file", str(results), "--list-results") == 1
            assert "次の操作:" in capsys.readouterr().err
        else:
            assert _run("--output", str(output), "--results-file", str(results), "--list-results") == 0
            compared = json.loads(capsys.readouterr().out)["diagnostics"]
            assert any(key.startswith("incomparable:") for key in compared)
            assert not any(
                item.get("after", {}).get("file") == "new.py" for key, item in compared.items() if key.startswith("unchanged:")
            )


@pytest.mark.parametrize("invalid", ["missing-history", "missing-fence", "empty-utterance"])
def test_public_record_rejects_invalid_plan_without_write(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """逐語発言を取得できない計画では既存の検証記録を書き換えない。"""
    record = tmp_path / "record.json"
    plan = _plan(tmp_path, "plan-user")
    assert _run("--output", str(record), "--plan", str(plan)) == 0
    before = record.read_bytes()
    text = plan.read_text(encoding="utf-8")
    if invalid == "missing-history":
        text = text.split("## 変更履歴", 1)[0]
    elif invalid == "missing-fence":
        text = text.replace("```text\n", "").replace("```\n", "")
    else:
        text = text.replace("保存する。\n", "")
    plan.write_text(text, encoding="utf-8")
    assert _run("--output", str(record), "--plan", str(plan)) == 1
    assert record.read_bytes() == before
    assert "次の操作:" in capsys.readouterr().err


def test_public_record_ignores_history_heading_inside_fence(tmp_path: pathlib.Path) -> None:
    """例示の変更履歴を原文出所にせず、実際の逐語発言だけを抽出する。"""
    record = tmp_path / "record.json"
    plan = _plan(tmp_path, "plan-user")
    original = plan.read_text(encoding="utf-8")
    plan.write_text(
        "````text\n## 変更履歴\n### ユーザー発言9\n```text\n例示だけ。\n```\n````\n" + original,
        encoding="utf-8",
    )
    assert _run("--output", str(record), "--plan", str(plan)) == 0
    rows = json.loads(record.read_text(encoding="utf-8"))["user_requirements"]
    assert [row["requirement"] for row in rows] == ["保存する。", "保存する。"]
    assert all("ユーザー発言9" not in row["source"] for row in rows)


def _json(path: pathlib.Path, value: object) -> pathlib.Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _git(path: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True, timeout=30
    ).stdout.strip()


def _saved_trial(tmp_path: pathlib.Path, text: str, name: str) -> dict[str, str]:
    """隔離Gitで実pytestを実行し、公開run-commandの記録とXMLを受け取る。"""
    if not (tmp_path / ".git").is_dir():
        _git(tmp_path, "init", "--quiet")
        _git(tmp_path, "config", "user.name", "試験担当")
        _git(tmp_path, "config", "user.email", "test@example.invalid")
        _git(tmp_path, "commit", "--allow-empty", "--no-verify", "-m", "基準")
    trial = tmp_path / "trial_test.py"
    trial.write_text(text, encoding="utf-8")
    xml = tmp_path / f"{name}.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_toolkit.atk",
            "run-command",
            "--cwd",
            str(tmp_path),
            "--timeout",
            "60",
            "--",
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "-p",
            "no:cacheprovider",
            "-o",
            "junit_family=xunit1",
            f"--junitxml={xml}",
            str(trial),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert result.returncode in {0, 1}, result.stderr
    saved = json.loads(result.stdout[result.stdout.index("{") :])
    assert saved["child_exit_code"] in {0, 1}
    return {"xml": str(xml), "run_record": saved["record_path"]}


def test_public_import_uses_junit_states_and_updates_selected_rows(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """実XMLの状態・クラス・パラメーターと基準版との差を公開操作で関連付ける。"""
    before = _saved_trial(tmp_path, "def test_old():\n    assert True\ndef test_changed():\n    assert True\n", "before")
    after = _saved_trial(
        tmp_path,
        'import pytest\n@pytest.mark.parametrize("value", ["PASSED", "FAILED"])\n'
        "def test_names(value):\n    assert value\n"
        "def test_changed():\n    assert False\n"
        'class TestStates:\n    @pytest.fixture\n    def broken(self):\n        raise RuntimeError("診断")\n'
        "    def test_error(self, broken):\n        pass\n"
        '    @pytest.mark.skip(reason="条件")\n    def test_skipped(self):\n        pass\n',
        "after",
    )
    results = _json(tmp_path / "results.json", {"junit": after, "baseline_junit": before})
    record = tmp_path / "record.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    capsys.readouterr()
    assert _run("--output", str(record), "--results-file", str(results), "--list-results") == 0
    listed = json.loads(capsys.readouterr().out)
    tests = listed["tests"]
    assert tests["trial_test.py::test_names[PASSED]"]["status"] == "passed"
    assert tests["trial_test.py::test_names[FAILED]"]["status"] == "passed"
    assert tests["trial_test.py::TestStates::test_error"]["status"] == "error"
    assert tests["trial_test.py::TestStates::test_skipped"]["status"] == "skipped"
    assert {item["change"] for item in listed["test_changes"]} == {"added", "deleted", "changed"}
    payload = json.loads(record.read_text(encoding="utf-8"))
    updates = _json(
        tmp_path / "updates.json",
        [
            {
                "section": "user_requirements",
                "row": index,
                "source": row["source"],
                "tests": ["trial_test.py::test_names[PASSED]"],
                "mode": "replace",
            }
            for index, row in enumerate(payload["user_requirements"], 1)
        ],
    )
    assert _run("--output", str(record), "--results-file", str(results), "--updates-file", str(updates)) == 0
    final = json.loads(record.read_text(encoding="utf-8"))
    for row in final["user_requirements"]:
        assert not row["outcome"] and not row["reviewed_head"]
        assert f"xml: `{after['xml']}`" in row["evidence"]
        assert "`trial_test.py::test_names[PASSED]` passed" in row["evidence"]


def test_public_import_roundtrip_reaches_return_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """実pytestの根拠を未判定取込み、同一証拠へのレビュー更新、返却生成まで通す。"""
    _wi(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    text = "def test_save_and_reload():\n    assert {'saved': 1}.get('saved') == 1\n"
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "試験担当")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "trial_test.py").write_text(text, encoding="utf-8")
    plan = _plan(tmp_path, "plan-user")
    _git(tmp_path, "add", "--", "trial_test.py", plan.name)
    _git(tmp_path, "commit", "--no-verify", "-m", "受入試験")
    head = _git(tmp_path, "rev-parse", "HEAD")
    spec = _saved_trial(tmp_path, text, "acceptance")
    results = _json(tmp_path / "results.json", {"junit": spec})
    pending, evidence = tmp_path / "pending.json", tmp_path / "evidence.json"
    assert _run("--output", str(pending), "--plan", str(plan)) == 0
    payload = json.loads(pending.read_text(encoding="utf-8"))
    updates = [
        {
            "section": "user_requirements",
            "row": index,
            "source": row["source"],
            "tests": ["trial_test.py::test_save_and_reload"],
            "mode": "replace",
        }
        for index, row in enumerate(payload["user_requirements"], 1)
    ]
    updates_path = _json(tmp_path / "updates.json", updates)
    assert _run("--output", str(pending), "--results-file", str(results), "--updates-file", str(updates_path)) == 0
    pending_bytes = pending.read_bytes()
    assert _run("--template", str(evidence), "--plan", str(plan), name="exec-review-evidence-check") == 0
    for update in updates:
        update.pop("tests")
        update.update(outcome="達成", reviewed_head=head, verification_source=update["source"])
    _json(updates_path, updates)
    assert (
        _run(
            str(evidence),
            "--verification-record",
            str(pending),
            "--updates-file",
            str(updates_path),
            "--output",
            str(evidence),
            name="exec-review-evidence-check",
        )
        == 0
    )
    assert pending.read_bytes() == pending_bytes
    table = tmp_path / "計画.exec-review.tsv"
    review_table.init(table)
    capsys.readouterr()
    assert (
        _run(
            str(evidence),
            "--plan",
            str(plan),
            "--expected-head",
            head,
            "--review-table",
            str(table),
            "--round",
            "1",
            "--return-result",
            name="exec-review-evidence-check",
        )
        == 0
    ), capsys.readouterr().err
    returned = capsys.readouterr().out
    assert "状態: completed" in returned and "未解決の指摘数: 0" in returned


@pytest.mark.parametrize("invalid", ["xml", "file", "duplicate", "head", "exit", "argv", "source", "row"])
def test_public_import_rejects_ambiguous_or_incomplete_results(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """破損・不整合と複数行の一部不正で、既存記録のバイト列を保持する。"""
    spec = _saved_trial(tmp_path, "def test_one():\n    assert True\n", "one")
    xml = pathlib.Path(spec["xml"])
    metadata = json.loads(pathlib.Path(spec["run_record"]).read_text(encoding="utf-8"))
    metadata_path = tmp_path / "metadata.json"
    spec["run_record"] = str(metadata_path)
    if invalid == "xml":
        xml.write_text("<", encoding="utf-8")
    elif invalid == "file":
        xml.write_text(xml.read_text(encoding="utf-8").replace('file="trial_test.py"', ""), encoding="utf-8")
    elif invalid == "duplicate":
        root = ET.parse(xml).getroot()
        suite = root.find("testsuite")
        assert suite is not None
        suite.append(ET.fromstring(ET.tostring(next(root.iter("testcase")))))
        ET.ElementTree(root).write(xml, encoding="utf-8")
    elif invalid == "head":
        metadata["git_head"] = None
    elif invalid == "exit":
        metadata["child_exit_code"] = None
    elif invalid == "argv":
        metadata["argv"] = ["pytest"]
    _json(metadata_path, metadata)
    results = _json(tmp_path / "results.json", {"junit": spec})
    record = tmp_path / "record.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    before = record.read_bytes()
    payload = json.loads(before)
    updates = [
        {
            "section": "user_requirements",
            "row": i,
            "source": row["source"],
            "tests": ["trial_test.py::test_one"],
            "mode": "append",
        }
        for i, row in enumerate(payload["user_requirements"], 1)
    ]
    if invalid == "source":
        updates[1]["source"] = "別出所"
    if invalid == "row":
        updates[1]["row"] = 10
    assert (
        _run(
            "--output",
            str(record),
            "--results-file",
            str(results),
            "--updates-file",
            str(_json(tmp_path / "updates.json", updates)),
        )
        == 1
    )
    assert record.read_bytes() == before
    assert "次の操作:" in capsys.readouterr().err
