"""実Gitの差分と読者別探索の申告を、公開CLIの返却・batchから検証する。"""

from __future__ import annotations

import json
import pathlib

import check_exec_review_evidence as check
import pytest
import reader_fit_declaration
import yaml

from agent_toolkit._testing import git_repository as git

SESSION = "11111111-2222-4333-8444-555555555555"


# 受理側の入力は、runtime-routing.md「実行手段」が認める起動手段ごとに実際に観測した識別子から作成する。
# agents_serverのsession_id（UUID）、Claude CodeのAgentツールのagent ID（16進列と`agent-`付き16進列）、
# Codexの組み込み委譲`spawn_agent`の返却`task_name`（2026年10月19日の実機記録の値）である。
@pytest.mark.parametrize("identifier", [SESSION, "a3aa408fdf18a760f", "agent-a3aa408fdf18a760f"])
def test_launch_identifiers_roundtrip_return_and_batch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], identifier: str
) -> None:
    """集約起動の識別子を全ファイルとexternalへ申告し、生成済みbatchでも受理する。"""
    _roundtrip(tmp_path, monkeypatch, capsys, identifier)


@pytest.mark.parametrize("identifier", ["/root/reader_review", "/root/recurrence_check_r2"])
def test_accepts_codex_spawn_agent_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], identifier: str
) -> None:
    """Codexの組み込み委譲が返すagentのパスを、返却生成とbatchの両方で受理する。"""
    _roundtrip(tmp_path, monkeypatch, capsys, identifier)


@pytest.mark.parametrize("identifier", ["lane-01-reader-fit", "reader_review", "/tmp/x", "/root/", "/root", "/root/a b"])
def test_rejects_non_launch_paths_with_launch_means(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], identifier: str
) -> None:
    """起動手段が返さない名前とパスを拒否し、受理する3種の起動手段と識別子を示す。"""
    repo = git.init_repository(tmp_path / "repo", commit_message="開始")
    start = git.git_output(repo, "rev-parse", "HEAD")
    (repo / "a.md").write_text("本文\n", encoding="utf-8")
    head = git.commit_all(repo, "文章")
    monkeypatch.chdir(repo)
    declaration = json.dumps({"files": {"a.md": {"sessions": [identifier]}}})
    assert check.main(_args(tmp_path, start, head, declaration)) == 1
    error = capsys.readouterr().err
    assert all(means in error for means in ("agents_server", "Agentツール", "spawn_agent"))


def _roundtrip(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], identifier: str
) -> None:
    repo = git.init_repository(tmp_path / "repo", commit_message="開始")
    start = git.git_output(repo, "rev-parse", "HEAD")
    for name in ("a.md", "b.md"):
        (repo / name).write_text("本文\n", encoding="utf-8")
    head = git.commit_all(repo, "文章")
    monkeypatch.chdir(repo)
    draft = tmp_path / "draft.md"
    draft.write_text("回答\n", encoding="utf-8")
    declaration = json.dumps(
        {
            "files": {name: {"sessions": [identifier]} for name in ("a.md", "b.md")},
            "external": {str(draft): {"sessions": [identifier]}},
        }
    )
    assert check.main([*_args(tmp_path, start, head, declaration), "--external-artifact", str(draft)]) == 0
    output = capsys.readouterr().out
    batch = next(
        line.removeprefix("受領確認入力のパス: ") for line in output.splitlines() if line.startswith("受領確認入力のパス: ")
    )
    assert check.main(["--batch", batch]) == 0
    capsys.readouterr()


@pytest.mark.parametrize("sessions", [["label"], ["日本語"], [], [1], [None]])
@pytest.mark.parametrize("external", [False, True])
def test_invalid_launch_identifiers_are_actionable(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    sessions: list[object],
    external: bool,
) -> None:
    """管理内外の不正識別子は、修正と未起動探索の起動を案内する。"""
    repo = git.init_repository(tmp_path / "repo", commit_message="開始")
    start = git.git_output(repo, "rev-parse", "HEAD")
    (repo / "a.md").write_text("本文\n", encoding="utf-8")
    head = git.commit_all(repo, "文章")
    monkeypatch.chdir(repo)
    draft = tmp_path / "draft.md"
    draft.write_text("回答\n", encoding="utf-8")
    value = {"files": {"a.md": {"sessions": [SESSION] if external else sessions}}}
    if external:
        value["external"] = {str(draft): {"sessions": sessions}}
    args = _args(tmp_path, start, head, json.dumps(value))
    if external:
        args.extend(["--external-artifact", str(draft)])
    assert check.main(args) == 1
    captured = capsys.readouterr()
    assert not captured.out and "起動" in captured.err and "再実行" in captured.err


def _args(
    tmp_path: pathlib.Path, start: str, head: str, declaration: str, *, previous: tuple[str, str] | None = None
) -> list[str]:
    table = tmp_path / "review.tsv"
    table.touch()
    result = [
        "なし",
        "--expected-head",
        head,
        "--review-table",
        str(table),
        "--round",
        "2" if previous else "1",
        "--return-result",
        "--review-start",
        start,
        "--reader-fit-review",
        declaration,
    ]
    if previous:
        result.extend(["--previous-review-start", previous[0], "--previous-review-head", previous[1]])
    return result


@pytest.mark.parametrize(
    "invalid",
    [
        "省略: 同系統の差分",
        "文章成果物なし",
        '{"files":{}}',
        '{"files":{"new.md":{"sessions":["label"]},"template.md.tmpl":{"sessions":["label"]}}}',
    ],
)
def test_public_reader_fit_declaration_covers_markdown_changes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """改名後・テンプレートの申告欠落と規定外の省略を拒否し、全件のUUID申告で返却する。"""
    repo = git.init_repository(tmp_path / "repo", files={"old.md": "元\n", "deleted.md": "削除\n"}, commit_message="開始")
    start = git.git_output(repo, "rev-parse", "HEAD")
    (repo / "old.md").rename(repo / "new.md")
    (repo / "deleted.md").unlink()
    (repo / "template.md.tmpl").write_text("生成元\n", encoding="utf-8")
    head = git.commit_all(repo, "変更")
    monkeypatch.chdir(repo)
    assert check.main(_args(tmp_path, start, head, invalid)) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "new.md" in captured.err and "template.md.tmpl" in captured.err
    assert "起動" in captured.err and "再実行" in captured.err
    declared = json.dumps({"files": {path: {"sessions": [SESSION]} for path in ("new.md", "template.md.tmpl")}})
    assert check.main(_args(tmp_path, start, head, declared)) == 0
    result = capsys.readouterr().out
    assert (
        f"レビュー開始時点: {git.git_output(repo, 'rev-parse', '--short=7', start)}" in result.splitlines()
        and "状態: completed" in result
    )
    assert check.main(_args(tmp_path, head, head, "文章成果物なし")) == 0


def test_public_reader_fit_recheck_compares_diffs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """基点と行位置が違っても同じ変更は省略でき、文字と空白の変更は省略できない。"""
    repo = git.init_repository(tmp_path / "repo", files={"a.md": "前置き\n旧\n"}, commit_message="開始")
    start = git.git_output(repo, "rev-parse", "HEAD")
    (repo / "a.md").write_text("前置き\n新\n", encoding="utf-8")
    previous_head = git.commit_all(repo, "初回レビュー")
    git.run_git(repo, "checkout", "--detach", start)
    (repo / "a.md").write_text("追加の文脈\n前置き\n旧\n", encoding="utf-8")
    new_start = git.commit_all(repo, "上流変更")
    (repo / "a.md").write_text("追加の文脈\n前置き\n新\n", encoding="utf-8")
    head = git.commit_all(repo, "基点を変更したレビュー")
    monkeypatch.chdir(repo)
    declaration = json.dumps({"files": {"a.md": {"omission": "再点検対象なし"}}}, ensure_ascii=False)
    assert check.main(_args(tmp_path, new_start, head, declaration, previous=(start, previous_head))) == 0
    assert (
        f"前回確認版HEAD: {git.git_output(repo, 'rev-parse', '--short=7', previous_head)}"
        in capsys.readouterr().out.splitlines()
    )
    (repo / "a.md").write_text("追加の文脈\n前置き\n新 \n", encoding="utf-8")
    changed = git.commit_all(repo, "空白も変更")
    assert check.main(_args(tmp_path, new_start, changed, declaration, previous=(start, previous_head))) == 1
    assert "変更内容が異なる" in capsys.readouterr().err


def test_public_batch_checks_reader_fit_declaration(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """batchでも同じ対象集合を使い、完全な申告と欠落の結果を別々に返す。"""
    repo = git.init_repository(tmp_path / "repo", commit_message="開始")
    start = git.git_output(repo, "rev-parse", "HEAD")
    (repo / "a.md").write_text("本文\n", encoding="utf-8")
    head = git.commit_all(repo, "文書を追加")
    monkeypatch.chdir(repo)
    table = tmp_path / "review.tsv"
    table.touch()
    item = {
        "plan": "なし",
        "evidence": "なし",
        "wi": [],
        "reviewed_head": head,
        "review_table": str(table),
        "round": 1,
        "plans": [],
        "input_records": [],
        "review_start": start,
        "external_artifacts": [],
    }
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            {
                "version": 1,
                "reviews": [
                    {**item, "reader_fit_review": json.dumps({"files": {"a.md": {"sessions": [SESSION]}}})},
                    {**item, "reader_fit_review": "文章成果物なし"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert check.main(["--batch", str(batch)]) == 1
    result = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["exit_code"] for item in result] == [0, 1]


@pytest.mark.parametrize("batch_mode", [False, True])
def test_external_artifacts_require_complete_structured_declaration(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], batch_mode: bool
) -> None:
    """回答下書きの欠落と自由記述を公開CLIで拒否し、UUIDと同一複製だけを受理する。"""
    repo = git.init_repository(tmp_path / "repo", commit_message="開始")
    head = git.git_output(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(repo)
    draft, previous = tmp_path / "draft.md", tmp_path / "previous.md"
    different = tmp_path / "different.md"
    draft.write_bytes(b"answer\n")
    previous.write_bytes(draft.read_bytes())
    different.write_bytes(b"old answer\n")
    declarations = [
        ("文章成果物なし", False),
        (json.dumps({"files": {}, "external": "同系統なので省略"}), False),
        (json.dumps({"files": {}, "external": {}}), False),
        (json.dumps({"files": {}, "external": {str(draft): {"sessions": [SESSION]}}}), True),
        (json.dumps({"files": {}, "external": {str(draft): {"omission": "再点検対象なし", "previous": str(previous)}}}), True),
        (
            json.dumps({"files": {}, "external": {str(draft): {"omission": "再点検対象なし", "previous": str(different)}}}),
            False,
        ),
    ]
    table = tmp_path / "review.tsv"
    table.touch()
    for value, accepted in declarations:
        review_round = 2 if '"omission"' in value else 1
        if batch_mode:
            batch = tmp_path / "batch.json"
            item = {
                "plan": "なし",
                "evidence": "なし",
                "wi": [],
                "reviewed_head": head,
                "review_table": str(table),
                "round": review_round,
                "plans": [],
                "input_records": [],
                "review_start": head,
                "reader_fit_review": value,
                "external_artifacts": [str(draft)],
                "previous_review_start": head,
                "previous_review_head": head,
            }
            batch.write_text(json.dumps({"version": 1, "reviews": [item]}), encoding="utf-8")
            assert check.main(["--batch", str(batch)]) == (0 if accepted else 1)
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            output = "\n".join([*result["diagnostics"], *result["result"]])
        else:
            args = _args(tmp_path, head, head, value, previous=(head, head) if review_round == 2 else None)
            assert check.main([*args, "--external-artifact", str(draft)]) == (0 if accepted else 1)
            captured = capsys.readouterr()
            output = captured.out + captured.err
            if not accepted:
                assert not captured.out
        if not accepted:
            assert str(draft) in output and "起動" in output and "再実行" in output
    with pytest.raises(ValueError, match="対象集合に無い"):
        reader_fit_declaration.check(declarations[3][0], repo, head, head, 2)


def test_contract_assigns_markdown_and_shared_lines_to_final_writer(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """他計画だけの文書と上書き済みの行を除外し、独立commitと自計画の差分を残す。"""
    repo = git.init_repository(tmp_path / "repo", files={"shared.md": "元1\n元2\n"}, commit_message="開始")
    start = git.git_output(repo, "rev-parse", "HEAD")
    (repo / "shared.md").write_text("自計画1\n元2\n", encoding="utf-8")
    (repo / "own.md").write_text("自計画\n", encoding="utf-8")
    own = git.commit_all(repo, "自計画")
    (repo / "shared.md").write_text("自計画1\n他計画2\n", encoding="utf-8")
    (repo / "other.md").write_text("他計画\n", encoding="utf-8")
    other = git.commit_all(repo, "他計画")
    contract = tmp_path / "contract.yaml"
    contract.write_text(
        yaml.safe_dump(
            {"version": 1, "clauses": [{"clause": "別計画の認可: 他", "content": f"対象commit: {other}。", "source": "要求"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    assert set(reader_fit_declaration.changed_markdown(repo, start, other, contract)) == {"shared.md", "own.md"}
    patch = reader_fit_declaration.patch_text(repo, start, other, "shared.md", contract)
    assert "+自計画1" in patch and "他計画2" not in patch
    monkeypatch.chdir(repo)
    missing = json.dumps({"files": {}})
    assert check.main([*_args(tmp_path, start, other, missing), "--review-contract", str(contract)]) == 1
    diagnostics = capsys.readouterr().err
    assert "own.md" in diagnostics and "shared.md" in diagnostics and "other.md" not in diagnostics
    complete = json.dumps({"files": {name: {"sessions": [SESSION]} for name in ("own.md", "shared.md")}})
    assert check.main([*_args(tmp_path, start, other, complete), "--review-contract", str(contract)]) == 0
    capsys.readouterr()
    other_contract = tmp_path / "other-contract.yaml"
    other_contract.write_text(
        yaml.safe_dump(
            {"version": 1, "clauses": [{"clause": "別計画の認可: 自", "content": f"対象commit: {own}。", "source": "要求"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    assert set(reader_fit_declaration.changed_markdown(repo, start, other, other_contract)) == {"shared.md", "other.md"}
    other_patch = reader_fit_declaration.patch_text(repo, start, other, "shared.md", other_contract)
    assert "+他計画2" in other_patch and "自計画1" not in other_patch
    declaration = json.dumps({"files": {"shared.md": {"omission": "再点検対象なし"}, "own.md": {"sessions": [SESSION]}}})
    reader_fit_declaration.check(declaration, repo, start, other, 2, start, own, review_contract=contract)
    (repo / "shared.md").write_text("他計画による最終行\n他計画2\n", encoding="utf-8")
    final = git.commit_all(repo, "他計画が同じ行を上書き")
    contract.write_text(contract.read_text(encoding="utf-8").replace(f"{other}。", f"{other}, {final}。"), encoding="utf-8")
    assert not reader_fit_declaration.patch_text(repo, start, final, "shared.md", contract)
    (repo / "independent.md").write_text("即時対応\n", encoding="utf-8")
    head = git.commit_all(repo, "独立commit")
    assert "independent.md" in reader_fit_declaration.changed_markdown(repo, start, head, contract)
