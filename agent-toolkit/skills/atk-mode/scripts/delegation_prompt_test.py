from __future__ import annotations

import argparse
import json
import pathlib

import pytest
from agent_toolkit._agents_server import launch_requests
from agent_toolkit._atk import review_table, run_script
from agent_toolkit._common import session_registry, task_documents
from agent_toolkit._testing import git_repository, review_bodies

_ROLES_DIR = pathlib.Path(__file__).resolve().parents[3] / "skills" / "atk-mode" / "roles"


def _generate(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], request: dict) -> tuple[int, dict]:
    input_path = tmp_path / "委譲 要求.json"
    input_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    result = run_script.dispatch(argparse.Namespace(script_name="delegation-prompt", script_args=["--input", str(input_path)]))
    captured = capsys.readouterr()
    return result, json.loads(captured.out if result == 0 else captured.err)


def test_start_and_builtin_prompt_share_input_contract(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_script.dispatch(argparse.Namespace(script_name="delegation-prompt", script_args=["--help"])) == 0
    help_text = capsys.readouterr().out
    assert all(field in help_text for field in ("initial", "subagent_md_path", "extra_params", "--input"))
    (tmp_path / "lane-01.json").write_text('{"lane": "lane-01", "base_branch": "develop"}\n', encoding="utf-8")
    params = {
        "担当種別": "レーン担当",
        "引き継ぎ記録先": str(tmp_path / "引継ぎ 記録.md"),
        "書込担当一覧": '["session-main"]',
        "所有登録": str(tmp_path / "lane-01.json"),
        "統合先worktree": str(tmp_path),
        "統合先branch": "develop",
        "レーン開始時刻": "2026-11-15T13:17:00+09:00",
        "再開位置": r"C:\資料 フォルダー\計画.md",
        "固有指示": "最初の要求\n\n追加の条件",
    }
    result, generated = _generate(tmp_path, capsys, {"kind": "initial", "subagent_md_path": "exec", "extra_params": params})
    assert result == 0
    assert generated["kind"] == "initial"
    document = _ROLES_DIR / "exec.subagent.md"
    assert generated["prompt"].splitlines()[0] == f"{document}の手順を実行せよ。"
    assert task_documents.parse_named_inputs(generated["prompt"].splitlines()[1:]) == params
    assert "固有指示:\n  最初の要求\n  \n  追加の条件" in generated["prompt"]
    # サーバー側の生成は全体作業契約の解決を前提とするため、実行環境の会話を探索せずに解決できるよう明示する。
    whole = tmp_path / "work-contract.md"
    whole.write_text("目的: 入力契約の共有を確かめる\n", encoding="utf-8")
    _, server_prompt, _, _ = launch_requests.task_document_request("exec", params, whole_contract_path=str(whole))
    assert task_documents.task_prompt_inputs(server_prompt) == params
    for invalid in ({"引き継ぎ記録先": params["引き継ぎ記録先"]}, {**params, "余分な説明": "説明"}):
        result, failure = _generate(tmp_path, capsys, {"kind": "initial", "subagent_md_path": "exec", "extra_params": invalid})
        assert result == 1
        assert failure["error"]
        with pytest.raises(ValueError):
            launch_requests.task_document_request("exec", invalid, whole_contract_path=str(whole))


def test_continuation_uses_existing_launch_record(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_script.dispatch(argparse.Namespace(script_name="delegation-prompt", script_args=["--help"])) == 0
    help_text = capsys.readouterr().out
    assert all(field in help_text for field in ("continuation", "history_file", "instruction", "extra_params", "prompt"))
    document = _ROLES_DIR / "exec-review.subagent.md"
    history_path = tmp_path / "既存記録.json"
    history_path.write_text(
        json.dumps({"prompt": f"{document}の手順を実行せよ。", "followup_prompts": ["再レビューせよ\nround: 2"]}),
        encoding="utf-8",
    )
    result, generated = _generate(
        tmp_path,
        capsys,
        {
            "kind": "continuation",
            "history_file": str(history_path),
            "instruction": "再レビューせよ",
            "extra_params": {"round": "3"},
        },
    )
    assert result == 0
    assert generated == {"kind": "continuation", "prompt": "再レビューせよ\nround: 3"}
    result, failure = _generate(
        tmp_path,
        capsys,
        {
            "kind": "continuation",
            "history_file": str(history_path),
            "prompt": "再レビューせよ\nround: 4\n検証結果: 成功",
        },
    )
    assert result == 1
    assert "検証結果" in failure["error"]


@pytest.mark.parametrize(
    "heading_form",
    [
        "{document}の手順を実行せよ。",
        "{document} の手順を実行せよ。",
        "`{document}`の手順を実行せよ。",
        "{document}の手順を実行せよ",
        " `{document}` の手順を実行せよ ",
        None,
    ],
)
def test_continuation_checks_named_launch_inputs(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], heading_form: str | None
) -> None:
    document = _ROLES_DIR / "exec-review.subagent.md"
    plan = tmp_path / "計画.md"
    table = plan.with_suffix(".exec-review.tsv")
    history = tmp_path / "既存記録.json"
    request = {
        "kind": "continuation",
        "history_file": str(history),
        "instruction": "再レビューせよ",
        "extra_params": {"round": "3"},
    }
    if heading_form is not None:
        heading = heading_form.format(document=document)
        history.write_text(json.dumps({"prompt": heading}), encoding="utf-8")
        assert _generate(tmp_path, capsys, request) == (0, {"kind": "continuation", "prompt": "再レビューせよ\nround: 3"})
        references = ""
    else:
        heading = task_documents.task_prompt_header(document) + "\n入力:"
        references = task_documents.TASK_PROMPT_REFERENCES + "# 手順"
    launch_prompt = f"{heading}\n計画: {plan}"
    history.write_text(json.dumps({"prompt": launch_prompt + references}), encoding="utf-8")
    assert _generate(tmp_path, capsys, request) == (0, {"kind": "continuation", "prompt": "再レビューせよ\nround: 3"})
    review_table.init(table)
    review_table.add(table, "2", "exec-review", "module.py:10", "指摘")
    result, failure = _generate(tmp_path, capsys, request)
    assert result == 1
    assert "未応答の指摘" in failure["error"]
    assert str(table) in failure["error"]
    assert "atk review-table respond" in failure["next_action"]

    review_table.respond(table, "2", "exec-review", "module.py:10", "指摘", "", review_bodies.reason_body("対応不要"))
    capsys.readouterr()
    assert _generate(tmp_path, capsys, request) == (0, {"kind": "continuation", "prompt": "再レビューせよ\nround: 3"})

    history.write_text(json.dumps({"prompt": launch_prompt + "\n不正な入力" + references}), encoding="utf-8")
    result, failure = _generate(tmp_path, capsys, request)
    assert result == 1
    assert "不正な入力" in failure["error"]


def test_continuation_rejects_extra_text_on_launch_heading(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    document = _ROLES_DIR / "exec-review.subagent.md"
    history = tmp_path / "既存記録.json"
    history.write_text(json.dumps({"prompt": f"{document}の手順を実行せよ。別の指示"}), encoding="utf-8")
    result, failure = _generate(
        tmp_path,
        capsys,
        {
            "kind": "continuation",
            "history_file": str(history),
            "instruction": "再レビューせよ",
            "extra_params": {"round": "3"},
        },
    )
    assert result == 1
    assert "入力本文" in failure["error"]
    assert "別の指示" in failure["error"]


@pytest.mark.parametrize("registry", [False, True])
def test_continuation_resolves_ci_table_in_target_repository(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, registry: bool
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    repo = git_repository.init_repository(tmp_path / "対象", commit_message="対象の基点")
    full = git_repository.git_output(repo, "rev-parse", "HEAD")
    short = git_repository.git_output(repo, "rev-parse", "--short=7", "HEAD")
    table = tmp_path / ".claude" / "plans" / f"ci-{short}.exec-review.tsv"
    table.parent.mkdir(parents=True)
    review_table.init(table)
    review_table.add(table, "2", "exec-review", "module.py:10", "指摘")
    document = _ROLES_DIR / "exec-review.subagent.md"
    prompt = f"`{document}` の手順を実行せよ\nCI記録: 修正結果\n修正系列の開始時のHEAD: {full}"
    if registry:
        info = session_registry.ResumeInfo(
            "codex", str(repo), None, None, None, "delegate", 0, "completed", session_registry.LaunchInfo(prompt=prompt)
        )
        resolution = session_registry.SessionResolution(session_registry.Resolution.TERMINAL, resume_info=info)
        monkeypatch.setattr(session_registry, "resolve", lambda _: resolution)
        source = {"session_id": "既存セッション"}
    else:
        history = tmp_path / "既存記録.json"
        history.write_text(json.dumps({"prompt": prompt}), encoding="utf-8")
        source = {"history_file": str(history)}
    monkeypatch.chdir(tmp_path)
    request = {"kind": "continuation", **source, "instruction": "再レビューせよ", "extra_params": {"round": "3"}}
    if not registry:
        result, failure = _generate(tmp_path, capsys, request)
        assert result == 1
        assert "HEADを解決できない" in failure["error"]
        assert "対象のGit作業ツリー" in failure["next_action"]
        monkeypatch.chdir(repo)
    result, failure = _generate(tmp_path, capsys, request)
    assert result == 1
    assert str(table) in failure["error"]
    assert "row-id 1" in failure["error"]
    assert "atk review-table respond" in failure["next_action"]


def test_history_file_uses_saved_cwd_independently_of_caller(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    repo = git_repository.init_repository(tmp_path / "対象", commit_message="対象の基点")
    other = git_repository.init_repository(tmp_path / "別リポジトリ", commit_message="別の基点")
    full = git_repository.git_output(repo, "rev-parse", "HEAD")
    short = git_repository.git_output(repo, "rev-parse", "--short=7", "HEAD")
    table = tmp_path / ".claude" / "plans" / f"ci-{short}.exec-review.tsv"
    table.parent.mkdir(parents=True)
    document = _ROLES_DIR / "exec-review.subagent.md"
    prompt = f"{document}の手順を実行せよ。\nCI記録: 修正結果\n修正系列の開始時のHEAD: {full}"
    history = tmp_path / "セッション記録.json"
    history.write_text(json.dumps({"prompt": prompt, "cwd": str(repo), "followup_prompts": []}), encoding="utf-8")
    request = {
        "kind": "continuation",
        "history_file": str(history),
        "instruction": "再レビューせよ",
        "extra_params": {"round": "3"},
    }

    def from_all_callers() -> list[tuple[int, dict]]:
        results = []
        for caller in (tmp_path, other, repo):
            monkeypatch.chdir(caller)
            results.append(_generate(tmp_path, capsys, request))
        return results

    success = (0, {"kind": "continuation", "prompt": "再レビューせよ\nround: 3"})
    assert from_all_callers() == [success] * 3
    review_table.init(table)
    review_table.add(table, "2", "exec-review", "module.py:10", "指摘")
    rejected = from_all_callers()
    result, failure = rejected[0]
    assert result == 1
    assert str(table) in failure["error"]
    assert "row-id 1" in failure["error"]
    assert "atk review-table respond" in failure["next_action"]
    assert rejected == [(result, failure)] * 3
    review_table.respond(table, "2", "exec-review", "module.py:10", "指摘", "", review_bodies.reason_body("対応不要"))
    capsys.readouterr()
    assert from_all_callers() == [success] * 3


@pytest.mark.parametrize("record_kind", ["session", "legacy"])
@pytest.mark.parametrize("saved_cwd", ["absent", "empty", "absolute"])
@pytest.mark.parametrize("mode", ["ci", "nonci-plan", "nonci-wi", "nonci-other"])
@pytest.mark.parametrize("caller_inside", [False, True], ids=["outside", "inside"])
def test_history_file_cwd_matrix(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    record_kind: str,
    saved_cwd: str,
    mode: str,
    caller_inside: bool,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    repo = git_repository.init_repository(tmp_path / "対象", commit_message="対象の基点")
    full = git_repository.git_output(repo, "rev-parse", "HEAD")
    short = git_repository.git_output(repo, "rev-parse", "--short=7", "HEAD")
    plan = tmp_path / "計画.md"
    ci_inputs = f"CI記録: 合成の結果\n修正系列の開始時のHEAD: {full}"
    inputs = {
        "ci": ci_inputs,
        "nonci-plan": f"計画: {plan}\n{ci_inputs}",
        "nonci-wi": "レビュー基準: WI\nWI記録: 合成のWI",
        "nonci-other": ci_inputs,
    }[mode]
    document = _ROLES_DIR / ("exec.subagent.md" if mode == "nonci-other" else "exec-review.subagent.md")
    launch = f"{task_documents.task_prompt_header(document)}\n入力:\n{inputs}"
    record: dict[str, object] = {"prompt": launch}
    if record_kind == "session":
        record.update({"engine": "codex", "status": "completed", "followup_prompts": []})
    if saved_cwd != "absent":
        record["cwd"] = str(repo) if saved_cwd == "absolute" else ""
    history = tmp_path / "履歴.json"
    history.write_text(json.dumps(record), encoding="utf-8")
    instruction = "実装開始" if mode == "nonci-other" else "再レビューせよ"
    params = {} if mode == "nonci-other" else {"round": "3"}
    request = {
        "kind": "continuation",
        "history_file": str(history),
        "instruction": instruction,
        "extra_params": params,
    }
    monkeypatch.chdir(repo if caller_inside else tmp_path)
    if mode == "ci" and not caller_inside and saved_cwd != "absolute":
        result, failure = _generate(tmp_path, capsys, request)
        assert result == 1
        assert "HEADを解決できない" in failure["error"]
        assert "対象のGit作業ツリー" in failure["next_action"]
        return

    expected = instruction if mode == "nonci-other" else "再レビューせよ\nround: 3"
    success = (0, {"kind": "continuation", "prompt": expected})
    assert _generate(tmp_path, capsys, request) == success
    if mode in {"ci", "nonci-plan"}:
        table = (
            plan.with_suffix(".exec-review.tsv")
            if mode == "nonci-plan"
            else (tmp_path / ".claude" / "plans" / f"ci-{short}.exec-review.tsv")
        )
        table.parent.mkdir(parents=True, exist_ok=True)
        review_table.init(table)
        review_table.add(table, "2", "exec-review", "module.py:10", "指摘")
        result, failure = _generate(tmp_path, capsys, request)
        assert result == 1
        assert str(table) in failure["error"]
        assert "row-id 1" in failure["error"]
        assert "atk review-table respond" in failure["next_action"]
        review_table.respond(table, "2", "exec-review", "module.py:10", "指摘", "", review_bodies.reason_body("対応不要"))
        capsys.readouterr()
        assert _generate(tmp_path, capsys, request) == success


@pytest.mark.parametrize("cwd", ["相対パス", None])
def test_history_file_checks_invalid_saved_cwd_only_for_ci(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], cwd: str | None
) -> None:
    document = _ROLES_DIR / "exec-review.subagent.md"
    history = tmp_path / "セッション記録.json"
    heading = f"{task_documents.task_prompt_header(document)}\n入力:"
    request = {
        "kind": "continuation",
        "history_file": str(history),
        "instruction": "再レビューせよ",
        "extra_params": {"round": "3"},
    }
    record = {"prompt": f"{heading}\nレビュー基準: WI", "cwd": cwd}
    history.write_text(json.dumps(record), encoding="utf-8")
    assert _generate(tmp_path, capsys, request) == (0, {"kind": "continuation", "prompt": "再レビューせよ\nround: 3"})
    record["prompt"] = f"{heading}\nCI記録: 結果\n修正系列の開始時のHEAD: 1234567"
    history.write_text(json.dumps(record), encoding="utf-8")
    result, failure = _generate(tmp_path, capsys, request)
    assert result == 1
    assert "記録のcwdには対象作業ツリーの絶対パス" in failure["error"]


@pytest.mark.parametrize("multiple", [False, True])
def test_continuation_accepts_all_plan_tables(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], multiple: bool
) -> None:
    document = _ROLES_DIR / "exec.subagent.md"
    tables = [tmp_path / "計画1.tsv", tmp_path / "計画2 の表.tsv"]
    for table in tables:
        table.write_text("指摘の記録\n", encoding="utf-8")
    history = tmp_path / "history.json"
    history.write_text(json.dumps({"prompt": f"{document}の手順を実行せよ。"}), encoding="utf-8")
    value = json.dumps([str(table) for table in tables], ensure_ascii=False) if multiple else str(tables[0])
    request = {
        "kind": "continuation",
        "history_file": str(history),
        "instruction": "レビュー指摘の対応をせよ",
        "extra_params": {"レビュー指摘管理表": value},
    }
    result, generated = _generate(tmp_path, capsys, request)
    assert result == 0
    assert generated["prompt"] == f"レビュー指摘の対応をせよ\nレビュー指摘管理表: {value}"
    missing = str(tmp_path / "存在しない.tsv")
    invalid_values = [
        (json.dumps([str(tables[0]), missing]), missing),
        ('["relative.tsv"]', "relative.tsv"),
        ("[null]", "None"),
        ("[123]", "123"),
        ("[]", "[]"),
        ("[", "["),
        (json.dumps([str(tmp_path)]), str(tmp_path)),
    ]
    for invalid, rejected in invalid_values:
        result, failure = _generate(tmp_path, capsys, {**request, "extra_params": {"レビュー指摘管理表": invalid}})
        assert result == 1
        assert rejected in failure["error"]
        assert "最小の再実行形" in failure["next_action"]
    result, failure = _generate(tmp_path, capsys, {**request, "extra_params": {"レビュー指摘管理表": value, "round": "1"}})
    assert result == 1
    assert "round" in failure["error"]
