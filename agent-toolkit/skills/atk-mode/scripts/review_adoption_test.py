from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pytest
from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.session_evidence_support import write_jsonl
from review_adoption import _confirmed_free_review_tables, _table_models

_ROLE_PROMPT = "出所: /plugin/agent-toolkit/skills/atk-mode/roles/exec-review.subagent.md"

_LEGACY_ROLE_PROMPT = "出所: /plugin/agent-toolkit/share/exec-review.subagent.md"


def test_free_review_uses_own_successful_add_and_formal_result(tmp_path: Path) -> None:
    table = "lane-04.exec-review.tsv"
    plan = "/plans/lane-04.md"
    path = f"/plans/{table}"
    kickoff = f'<atk-auto source="agents-server" kind="delivery">\n独立実行レビュー。計画 {plan} を確認。\n</atk-auto>'
    result = f"状態: completed\nレビューHEAD: {'a' * 40}\n未解決の指摘数: 1\n- レビュー指摘管理表: {path}\n"
    addition = f"成功: 指摘行を1件追加した: {path}（1件）\n"

    original = "/results/own.md"
    notice = f"状態: completed\n正式原本: {original}"

    def write(name: str, prompt: str, *, add: int, returned: str, final: str = "", model: str = "gpt-6.2-sol") -> None:
        payloads = [
            {"type": "message", "role": "user", "content": [{"text": prompt}]},
            {"type": "custom_tool_call", "call_id": "add", "input": f"atk review-table add {path} --track exec-review"},
            {"type": "custom_tool_call_output", "call_id": "add", "output": json.dumps({"exit_code": add, "output": addition})},
            {"type": "custom_tool_call", "call_id": "result", "input": f"cat {original}"},
            {
                "type": "custom_tool_call_output",
                "call_id": "result",
                "output": json.dumps({"exit_code": 0, "output": returned}),
            },
            {"type": "message", "role": "assistant", "phase": "final_answer", "content": [{"text": final}]},
        ]
        write_jsonl(
            tmp_path / f"rollout-{name}.jsonl",
            [
                {"type": "session_meta", "payload": {"id": name}},
                {"type": "turn_context", "payload": {"model": model, "effort": "medium"}},
                *({"type": "response_item", "payload": payload} for payload in payloads),
            ],
        )

    write("review", kickoff, add=0, returned="", final=result)
    write("duplicate", kickoff, add=0, returned=result, final=notice)
    for name in ("review", "duplicate"):
        assert _confirmed_free_review_tables((tmp_path / f"rollout-{name}.jsonl").read_text(encoding="utf-8"), {path}) == {
            table
        }
    write("support", kickoff.replace("独立実行レビュー", "補助調査"), add=0, returned="", final=result, model="gpt-6.1-sol")
    write("failed", kickoff, add=1, returned="", final=result, model="gpt-6.1-sol")
    write("incomplete", kickoff, add=0, returned=result, model="gpt-6.1-sol")
    write(
        "unmatched",
        kickoff,
        add=0,
        returned=result + f"正式原本: {original}\n",
        final=notice.replace("own", "other"),
        model="gpt-6.1-sol",
    )
    assert _table_models([table], [tmp_path]) == {table: "codex:gpt-6.2-sol/medium"}


def _row(round_value: str, issue: str, level: str, response: str, reason: str, *, location: str = "file.py") -> str:
    cells = [round_value, "exec-review", location, issue, level, response, reason]
    return "\t".join(json.dumps(cell, ensure_ascii=False) for cell in cells) + "\n"


def _commit(repo: Path, time: str) -> None:
    git_repository.commit_all(repo, "試験データ", env={**os.environ, "GIT_AUTHOR_DATE": time, "GIT_COMMITTER_DATE": time})


def _claude_review(path: Path, table: str, model: str, *, role_prompt: str = _ROLE_PROMPT) -> None:
    write_jsonl(
        path,
        [
            {
                "type": "user",
                "timestamp": "2026-11-03T00:00:00Z",
                "message": {"role": "user", "content": f"{role_prompt}\nレビュー指摘管理表: /home/u/.claude/plans/{table}"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-11-03T00:00:01Z",
                "effort": "high",
                "message": {"role": "assistant", "model": model, "content": [{"type": "text", "text": "完了"}]},
            },
        ],
    )


@pytest.fixture(name="dataset")
def _dataset(tmp_path: Path) -> tuple[Path, Path, Path]:
    notes = git_repository.init_repository(tmp_path / "notes")
    plans = notes / "plans/2026/10"
    plans.mkdir(parents=True)
    (plans / "28-0000_old.exec-review.tsv").write_text(_row("1", "古い指摘", "詳細", "修正した", ""), encoding="utf-8")
    _commit(notes, "2026-10-28T12:00:00+00:00")
    plans = notes / "plans/2026/11"
    plans.mkdir(parents=True)
    (plans / "03-0000_known.exec-review.tsv").write_text(
        _row("9", "実装時の解釈: 入力境界", "要件", "", "判断を記録")
        + _row("9", "実装時の判断: 共通読込", "実装", "", "判断を記録")
        + _row("9", "実装中の判断: 検証範囲", "詳細", "", "判断を記録")
        + _row("9", "記録", "仕様", "", "実装中の判断の引き継ぎであり、指摘への拒否ではない")
        + _row("9", "記録", "実装", "", "", location="実装時の技術判断: 呼出側")
        + _row("1", "別track", "詳細", "", "").replace('"exec-review"', '"plan-review"')
        + _row("1", "実装時の判断を確認する指摘", "仕様", "修正した", "")
        + _row("1", "見送り", "仕様", "", "根拠の所在: レビュー担当の指摘をレーン担当が対象外と判断\n判断主体: レーン担当")
        + _row("2", "撤回", "詳細", "", "判断主体: レビュー担当\n根拠の所在: 再確認で撤回")
        + _row("2", "移管", "仕様", "", "根拠の所在: 後続へ移す\n判断主体: ユーザー\n移管先: 20261104-183257-001.md")
        + _row(
            "2",
            "旧形式",
            "詳細",
            "",
            "根拠の所在: レビュー担当が後続AWIへ移せると区分し、メインが次回へ回した。AWI 20261104-183257-001.mdへ登録した",
        ),
        encoding="utf-8",
    )
    (plans / "03-0001_unknown.exec-review.tsv").write_text(_row("1", "未応答", "実装", "", ""), encoding="utf-8")
    (plans / "03-0002_judgments.exec-review.tsv").write_text(
        "".join(
            _row(
                "9",
                f"記録{number}",
                "要件",
                "",
                "実装時の判断: 記録" if number == 0 else "",
                location="file.py" if number == 0 else "実装時の技術判断: 記録",
            )
            for number in range(3)
        ),
        encoding="utf-8",
    )
    _commit(notes, "2026-11-03T12:00:00+00:00")
    claude = tmp_path / "claude"
    _claude_review(claude / "p/review.jsonl", "03-0000_known.exec-review.tsv", "claude-opus-5-5")
    _claude_review(
        claude / "p/judgments.jsonl", "03-0002_judgments.exec-review.tsv", "claude-opus-5-5", role_prompt=_LEGACY_ROLE_PROMPT
    )

    write_jsonl(
        claude / "p/main.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-11-03T00:00:00Z",
                "message": {"role": "user", "content": "03-0001_unknown.exec-review.tsv を確認する"},
            }
        ],
    )
    return notes, claude, tmp_path / "codex"


def _run(dataset: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    notes, claude, codex = dataset
    arguments = [
        "--",
        "--since",
        "2026-11-01T00:00:00+00:00",
        "--until",
        "2026-11-07T00:00:00+00:00",
        "--private-notes",
        str(notes),
        "--claude-projects-dir",
        str(claude),
        "--codex-sessions-dir",
        str(codex),
    ]
    assert run_script.dispatch(argparse.Namespace(script_name="review-adoption", script_args=arguments)) == 0
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_review_adoption_counts_periods_levels_reasons_and_models(dataset, capsys) -> None:
    events = _run(dataset, capsys)
    periods = {event["period"]: event for event in events if event["kind"] == "review-adoption-period"}
    current = periods["current"]
    assert (current["table_count"], current["adopted"], current["rejected"], current["transferred"], current["unanswered"]) == (
        3,
        1,
        3,
        1,
        1,
    )
    assert current["by_level"]["仕様"] == {"adopted": 1, "rejected": 1, "transferred": 1, "unanswered": 0}
    assert current["by_reason"] == {"判断による見送り": 1, "レビュー担当の前提の誤り・撤回": 1, "判断主体不明": 1}
    assert current["by_model"]["claude:claude-opus-5-5/high"] == {
        "adopted": 1,
        "rejected": 3,
        "transferred": 1,
        "unanswered": 0,
        "tables": 2,
    }
    assert current["by_model"]["モデル不明"] == {
        "adopted": 0,
        "rejected": 0,
        "transferred": 0,
        "unanswered": 1,
        "tables": 1,
    }
    assert periods["previous"]["table_count"] == 1
    assert periods["previous"]["adopted"] == 1
    rejections = [event for event in events if event["kind"] == "review-adoption-rejection"]
    assert {(event["row"], event["reason_class"]) for event in rejections} == {
        (8, "判断による見送り"),
        (9, "レビュー担当の前提の誤り・撤回"),
        (11, "判断主体不明"),
    }
    notes, _, _ = dataset
    assert current["unknown_model_tables"] == [str(notes / "plans/2026/11/03-0001_unknown.exec-review.tsv")]
    assert current["excluded_judgment_count"] == 8
    assert current["excluded_judgments"] == [
        {"table": str(notes / "plans/2026/11" / name), "row": number}
        for name, numbers in (
            ("03-0000_known.exec-review.tsv", range(1, 6)),
            ("03-0002_judgments.exec-review.tsv", range(1, 4)),
        )
        for number in numbers
    ]
    assert "要件" not in current["by_level"]
    assert events[-1]["kind"] == "review-adoption-coverage"


def _codex_review(path: Path, table: str, *, preamble_bytes: int) -> None:
    """実記録と同じ並び（道具の定義、developerの本文、標識の無い規範の配送、環境の状態、依頼文）のCodexの記録を書く。"""
    filler = "規" * (preamble_bytes // 3)
    time = "2026-11-03T00:00:00Z"
    write_jsonl(
        path,
        [
            {"type": "session_meta", "timestamp": time, "payload": {"id": "review"}},
            {"type": "response_item", "timestamp": time, "payload": {"type": "additional_tools", "tools": []}},
            {
                "type": "response_item",
                "timestamp": time,
                "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "方針"}]},
            },
            {
                "type": "response_item",
                "timestamp": time,
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": filler}]},
            },
            {"type": "world_state", "timestamp": time, "payload": {"state": "x"}},
            {
                "type": "response_item",
                "timestamp": time,
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": f"{_ROLE_PROMPT}\nレビュー指摘管理表: /p/{table}"}],
                },
            },
            {
                "type": "turn_context",
                "timestamp": time,
                "payload": {"model": "gpt-6.1-sol", "effort": "medium"},
            },
            {"type": "response_item", "timestamp": time, "payload": {"type": "function_call", "call_id": "c", "name": "shell"}},
        ],
    )


def test_review_adoption_finds_request_after_large_preamble_and_ignores_quotes(dataset, capsys) -> None:
    """依頼文より前の内容が256KiBを超えるCodexの記録へ帰属させ、標識を引用しただけの記録へは帰属させない。"""
    _notes, claude, codex = dataset
    _codex_review(codex / "2026/11/03/rollout-review.jsonl", "03-0001_unknown.exec-review.tsv", preamble_bytes=300 * 1024)

    write_jsonl(
        claude / "p/quote.jsonl",
        [
            {"type": "user", "timestamp": "2026-11-03T00:00:00Z", "message": {"role": "user", "content": "振り返りを行う"}},
            {
                "type": "assistant",
                "timestamp": "2026-11-03T00:00:01Z",
                "effort": "low",
                "message": {"role": "assistant", "model": "claude-quote-1", "content": [{"type": "text", "text": "読む"}]},
            },
            {
                "type": "user",
                "timestamp": "2026-11-03T00:00:02Z",
                "message": {"role": "user", "content": f"{_ROLE_PROMPT} 03-0000_known.exec-review.tsv"},
            },
        ],
    )
    events = _run(dataset, capsys)
    current = next(event for event in events if event.get("period") == "current")
    assert "モデル不明" not in current["by_model"]
    assert [model for model in current["by_model"] if model.startswith("codex:")] == ["codex:gpt-6.1-sol/medium"]
    assert not any("claude-quote-1" in model for model in current["by_model"])


def test_one_review_record_attributes_every_matching_table(dataset, capsys):
    _, claude, _ = dataset
    first = "03-0000_known.exec-review.tsv"
    second = "03-0001_unknown.exec-review.tsv"
    _claude_review(claude / "multi-review.jsonl", f"{first}、{second}", "claude-opus-5-5")
    periods = {row["period"]: row for row in _run(dataset, capsys) if row["kind"] == "review-adoption-period"}
    assert not periods["current"]["unknown_model_tables"]
    assert periods["current"]["by_model"]["claude:claude-opus-5-5/high"]["tables"] == 3


def test_codex_spawn_attributes_only_the_primary_reviewer(dataset, capsys) -> None:
    _, _, codex = dataset
    table = "03-0001_unknown.exec-review.tsv"
    role_doc = "roles/exec-review.subagent.md"

    def record(session: str, model: str, first_path: str, *, parent: str | None = None, marker: str = "") -> None:
        source = {"subagent": {"thread_spawn": {"parent_thread_id": parent}}} if parent else "vscode"
        table_ref = "" if marker else table
        write_jsonl(
            codex / f"rollout-{session}.jsonl",
            [
                {"type": "session_meta", "payload": {"id": session, "source": source}},
                {"type": "turn_context", "payload": {"model": model, "effort": "low"}},
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": marker},
                },
                {"type": "response_item", "payload": {"type": "reasoning"}},
                {
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call", "name": "exec", "input": f"cat {first_path}"},
                },
                {
                    "type": "response_item",
                    "payload": {"type": "function_call", "arguments": f"cat {role_doc}; echo {table_ref}"},
                },
            ],
        )

    record("parent", "codex-parent-model", role_doc)
    record("review", "codex-primary-model", role_doc, parent="parent")
    record("support", "codex-support-model", role_doc, parent="review")
    record("other", "codex-other-model", "roles/exec.subagent.md", parent="parent")
    record("other-path", "codex-other-path-model", "/plugin/notroles/exec-review.subagent.md", parent="parent")
    record("orphan", "codex-orphan-model", role_doc, parent="missing")
    record("explicit-review", "", role_doc, marker=_ROLE_PROMPT)
    record("explicit-support", "codex-explicit-support-model", role_doc, parent="explicit-review")
    current = next(
        event for event in _run(dataset, capsys) if event["kind"] == "review-adoption-period" and event["period"] == "current"
    )
    models = current["by_model"]
    assert table not in current["unknown_model_tables"]
    assert models["codex:codex-primary-model/low"]["tables"] == 1
    assert not any(
        f"codex-{name}-model" in identity
        for identity in models
        for name in ("parent", "support", "other", "other-path", "orphan", "explicit-support")
    )


def test_default_codex_roots_keep_archived_review_model(
    dataset: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    notes, claude, codex = dataset
    monkeypatch.setenv("CODEX_HOME", str(codex))
    archive = codex / "archived_sessions"
    _codex_review(archive / "rollout-review.jsonl", "03-0001_unknown.exec-review.tsv", preamble_bytes=0)
    arguments = [
        "--",
        "--since",
        "2026-11-01",
        "--until",
        "2026-11-07",
        "--claude-projects-dir",
        str(claude),
        "--private-notes",
        str(notes),
    ]
    assert run_script.dispatch(argparse.Namespace(script_name="review-adoption", script_args=arguments)) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    current = next(row for row in rows if row["period"] == "current")
    assert "codex:gpt-6.1-sol/medium" in current["by_model"]
    assert "モデル不明" not in current["by_model"]


def _written_file(path: str, body: str) -> dict[str, Any]:
    return _completed_item("FileChange", changes={path: {"type": "add", "content": body}})


def _completed_item(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": kind, "status": "completed", **fields}}}


def _shell_event(source: str, output: str, *, exit_code: int = 0) -> dict[str, Any]:
    return _completed_item(
        "CommandExecution",
        command=["bash", "-c", source],
        cwd="file:/scratch/work/tree",
        aggregated_output=output,
        exit_code=exit_code,
    )


def _body_review(path: Path, events: list[dict[str, Any]], *, marked: bool = True, model: str = "gpt-6.2-sol") -> None:
    write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"id": path.stem}},
            {"type": "turn_context", "payload": {"model": model, "effort": "high"}},
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": _ROLE_PROMPT if marked else "参照だけ"},
            },
            *events,
        ],
    )


def _register_one(body: str, *, exit_code: int = 0) -> dict[str, Any]:
    source = (
        'D=$(dirname "$PWD"); P="$D/draft.exec-review.tsv"\n'
        "cat >/scratch/issue.md <<'ISSUE'\n"
        f"{body}\nISSUE\n"
        'atk review-table add "$P" --round 1 --track=exec-review '
        "--location-file /scratch/location --issue-file /scratch/issue.md"
    )
    receipt = "成功: 指摘行を1件追加した: /scratch/work/draft.exec-review.tsv（1件）\n"
    return _shell_event(source, receipt, exit_code=exit_code)


def test_adoption_attributes_a_copied_table_from_successful_own_registration(dataset, capsys) -> None:
    notes, claude, codex = dataset
    plans = notes / "plans/2026/11"
    copied = "03-0101_official.exec-review.tsv"
    unmatched = "03-0102_incomplete.exec-review.tsv"
    read_only = "03-0103_read.exec-review.tsv"
    primary = "03-0104_primary.exec-review.tsv"
    body = "要約: 一覧に出ない\n根拠: 読み取りの境界"
    read_body = "要約: 読み取っただけ\n根拠: 別の担当が書いた"
    (plans / copied).write_text(
        _row("1", body.replace("\n", "\r\n") + "\n<!-- review-issue-v2:acceptance -->", "仕様", "修正した", ""),
        encoding="utf-8",
    )
    (plans / unmatched).write_text(
        _row("1", body, "仕様", "修正した", "") + _row("1", body + "。", "仕様", "", ""), encoding="utf-8"
    )
    (plans / read_only).write_text(_row("1", read_body, "仕様", "", ""), encoding="utf-8")
    (plans / primary).write_text(_row("1", body, "仕様", "修正した", ""), encoding="utf-8")
    _commit(notes, "2026-11-04T12:00:00+00:00")
    _body_review(codex / "rollout-writer.jsonl", [_register_one(body)])
    _body_review(codex / "rollout-unmarked.jsonl", [_register_one(body), _register_one(read_body)], marked=False)
    _body_review(
        codex / "rollout-reader.jsonl",
        [
            _shell_event(
                "cat /scratch/issue.md",
                f"成功: 指摘行を1件追加した: /scratch/draft.exec-review.tsv（1件）\n{body}\n{read_body}",
            )
        ],
        model="gpt-6.1-sol",
    )
    _body_review(codex / "rollout-failed.jsonl", [_register_one(body, exit_code=1)], model="gpt-6.1-sol")
    _claude_review(claude / "p/primary.jsonl", primary, "claude-opus-5-5")
    current = next(event for event in _run(dataset, capsys) if event.get("period") == "current")
    assert current["by_model"]["codex:gpt-6.2-sol/high"] == {
        "adopted": 1,
        "rejected": 0,
        "transferred": 0,
        "unanswered": 0,
        "tables": 1,
    }
    assert current["by_model"]["claude:claude-opus-5-5/high"]["tables"] == 3
    assert {Path(path).name for path in current["unknown_model_tables"]} == {
        "03-0001_unknown.exec-review.tsv",
        unmatched,
        read_only,
    }


def test_body_evidence_requires_one_record_for_every_row(tmp_path: Path) -> None:
    _body_review(tmp_path / "rollout-first.jsonl", [_register_one("重複"), _register_one("先の指摘")])
    _body_review(tmp_path / "rollout-second.jsonl", [_register_one("重複"), _register_one("後の指摘")])
    rows = {
        "unique.exec-review.tsv": ["先の指摘"],
        "ambiguous.exec-review.tsv": ["重複"],
        "mixed.exec-review.tsv": ["先の指摘", "後の指摘"],
        "empty.exec-review.tsv": [],
    }
    assert _table_models(list(rows), [tmp_path], rows) == {"unique.exec-review.tsv": "codex:gpt-6.2-sol/high"}


def test_batch_and_recheck_can_use_recorded_write_inputs(tmp_path: Path) -> None:
    source = """set -e; D=$(realpath ..); M=/scratch/issues; P="$D/draft.exec-review.tsv"
python3 - "$M" "$D" <<'PYTHON'
import json, pathlib, sys
m, d = map(pathlib.Path, sys.argv[1:])
items = [dict(location="file.py", issue=f"指摘先: {d}/file.py"), {"location": "file.py", "issue": "次の指摘"}]
(m / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\\n")
PYTHON
atk review-table add "$P" --round 1 --track exec-review --items-file "$M/items.json"
"""
    carried = "根拠: まだ足りない"
    _body_review(
        tmp_path / "rollout-writer.jsonl",
        [
            _shell_event(source, "成功: 指摘行を2件追加した: /scratch/work/draft.exec-review.tsv（2件）"),
            _written_file("/scratch/issues/remaining.md", carried + "\n"),
            _shell_event("cat /scratch/issues/remaining.md", "他者の発話"),
            _shell_event(
                "atk review-table recheck /scratch/work/draft.exec-review.tsv --row-id 2 --round 2 --verdict 未対応 "
                "--issue-file /scratch/issues/remaining.md",
                "成功: 再確認を記録した: /scratch/work/draft.exec-review.tsv（row-id 2、再確認: ラウンド2 未対応 行3）",
            ),
        ],
    )
    rows = {
        "official.exec-review.tsv": [
            "指摘先: /scratch/work/file.py",
            "次の指摘",
            f"区分: 前回から残存\n元の指摘: 行2\n{carried}",
        ],
        "wrong-origin.exec-review.tsv": [f"区分: 前回から残存\n元の指摘: 行1\n{carried}"],
    }
    assert _table_models(list(rows), [tmp_path], rows) == {"official.exec-review.tsv": "codex:gpt-6.2-sol/high"}


def test_registration_does_not_reuse_content_at_another_path_or_after_overwrite(tmp_path: Path) -> None:
    add = "atk review-table add /scratch/new.exec-review.tsv --round 1 --track exec-review --issue-file "
    receipt = "成功: 指摘行を1件追加した: /scratch/new.exec-review.tsv（1件）"
    _body_review(
        tmp_path / "rollout-writer.jsonl",
        [
            _written_file("/scratch/author/issue.md", "以前の本文"),
            _shell_event(add + "/scratch/elsewhere/issue.md", receipt),
            _shell_event("cp /scratch/other.md /scratch/author/issue.md", ""),
            _shell_event(add + "/scratch/author/issue.md", receipt),
        ],
    )
    rows = {"official.exec-review.tsv": ["以前の本文"]}
    assert _table_models(list(rows), [tmp_path], rows) == {}


@pytest.mark.parametrize(("prefix", "suffix"), [("false && ", ""), ("if false; then ", "fi\n")])
def test_registration_ignores_an_unexecuted_conditional_heredoc(tmp_path: Path, prefix: str, suffix: str) -> None:
    source = (
        f"{prefix}cat >/scratch/issue.md <<'ISSUE'\n"
        "実際にはこの本文を書いていない\n"
        "ISSUE\n"
        f"{suffix}atk review-table add /scratch/draft.exec-review.tsv --round 1 --track exec-review "
        "--issue-file /scratch/issue.md"
    )
    receipt = "成功: 指摘行を1件追加した: /scratch/draft.exec-review.tsv（1件）"
    _body_review(tmp_path / "rollout-writer.jsonl", [_shell_event(source, receipt)])
    rows = {"official.exec-review.tsv": ["実際にはこの本文を書いていない"]}
    assert _table_models(list(rows), [tmp_path], rows) == {}


@pytest.mark.parametrize("path", ["/scratch/dir/../issue.md", "/scratch/./issue.md", "//scratch/issue.md"])
def test_registration_rejects_stale_body_after_alias_overwrite(tmp_path: Path, path: str) -> None:
    _body_review(
        tmp_path / "rollout-writer.jsonl",
        [
            _written_file("/scratch/issue.md", "上書き前の本文"),
            _shell_event(f"printf '%s' '別の本文' >{path}", ""),
            _shell_event(
                "atk review-table add /scratch/draft.exec-review.tsv --round 1 --track exec-review "
                "--issue-file /scratch/issue.md",
                "成功: 指摘行を1件追加した: /scratch/draft.exec-review.tsv（1件）",
            ),
        ],
    )
    rows = {"official.exec-review.tsv": ["上書き前の本文"]}
    assert _table_models(list(rows), [tmp_path], rows) == {}


@pytest.mark.parametrize("diagnostic", ["", "Traceback (most recent call last):\nPermissionError: write blocked\n"])
def test_batch_without_errexit_does_not_prove_that_the_source_was_written(tmp_path: Path, diagnostic: str) -> None:
    source = """python3 - /scratch <<'PYTHON'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
items = [dict(location="file.py", issue="別の内容を登録した")]
(out / "items.json").write_text(json.dumps(items))
PYTHON
atk review-table add /scratch/draft.exec-review.tsv --round 1 --track exec-review --items-file /scratch/items.json
"""
    receipt = "成功: 指摘行を1件追加した: /scratch/draft.exec-review.tsv（1件）"
    _body_review(tmp_path / "rollout-writer.jsonl", [_shell_event(source, diagnostic + receipt)])
    rows = {"official.exec-review.tsv": ["別の内容を登録した"]}
    assert _table_models(list(rows), [tmp_path], rows) == {}


@pytest.mark.parametrize("redirect", ["", " 2>/dev/null"])
def test_later_read_cannot_supply_the_registration_receipt(tmp_path: Path, redirect: str) -> None:
    source = (
        "atk review-table add /scratch/draft.exec-review.tsv --round 1 --track exec-review "
        f"--issue-file /scratch/issue.md{redirect}\n"
        "cat /scratch/old-receipt.txt"
    )
    receipt = "成功: 指摘行を1件追加した: /scratch/draft.exec-review.tsv（1件）"
    _body_review(
        tmp_path / "rollout-writer.jsonl", [_written_file("/scratch/issue.md", "未登録の本文"), _shell_event(source, receipt)]
    )
    rows = {"official.exec-review.tsv": ["未登録の本文"]}
    assert _table_models(list(rows), [tmp_path], rows) == {}


@pytest.mark.parametrize("case", ["failed-before-summary", "failed-after-summary", "succeeded-before-summary"])
def test_later_registration_only_uses_writes_proven_before_the_summary_boundary(tmp_path: Path, case: str) -> None:
    show = "atk review-table show /scratch/draft.exec-review.tsv --round 1 --format summary\n"
    write = """python3 - /scratch <<'PYTHON'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
items = [dict(location="file.py", issue="今回の本文")]
(out / "items.json").write_text(json.dumps(items))
PYTHON
"""
    summary = json.dumps({"track": "exec-review", "round": 1, "total_count": 0, "unanswered_count": 0, "answered_count": 0})
    diagnostic = "Traceback (most recent call last):\nPermissionError: write blocked"
    if case == "failed-after-summary":
        source, output = show + write + ":", f"{summary}\n{diagnostic}"
    elif case == "failed-before-summary":
        source, output = write + show + ":", f"{diagnostic}\n{summary}"
    else:
        source, output = "set -e\n" + write + show + ":", summary
    add = "atk review-table add /scratch/draft.exec-review.tsv --round 1 --track exec-review --items-file /scratch/items.json"
    receipt = "成功: 指摘行を1件追加した: /scratch/draft.exec-review.tsv（1件）"
    _body_review(
        tmp_path / "rollout-writer.jsonl",
        [
            _written_file("/scratch/items.json", json.dumps([{"location": "file.py", "issue": "以前の本文"}])),
            _shell_event(source, output),
            _shell_event(add, receipt),
        ],
    )
    rows = {"new.exec-review.tsv": ["今回の本文"], "stale.exec-review.tsv": ["以前の本文"]}
    expected = {"new.exec-review.tsv": "codex:gpt-6.2-sol/high"} if case == "succeeded-before-summary" else {}
    assert _table_models(list(rows), [tmp_path], rows) == expected


def test_ambiguous_file_change_path_invalidates_without_adding_an_alias(tmp_path: Path) -> None:
    _body_review(
        tmp_path / "rollout-writer.jsonl",
        [
            _written_file("/scratch/issue.md", "以前の本文"),
            _written_file("//scratch/issue.md", "今回の本文"),
            _shell_event(
                "atk review-table add /scratch/draft.exec-review.tsv --round 1 --track exec-review "
                "--issue-file /scratch/issue.md",
                "成功: 指摘行を1件追加した: /scratch/draft.exec-review.tsv（1件）",
            ),
        ],
    )
    rows = {"new.exec-review.tsv": ["今回の本文"], "stale.exec-review.tsv": ["以前の本文"]}
    assert _table_models(list(rows), [tmp_path], rows) == {}


def test_windows_cwd_does_not_merge_unc_and_posix_looking_paths_for_invalidation(tmp_path: Path) -> None:
    overwrite = _shell_event("printf '%s' '別の本文' >//scratch/share/issue.md", "")
    overwrite["payload"]["item"]["cwd"] = "file:///C:/scratch/work/tree"
    _body_review(
        tmp_path / "rollout-writer.jsonl",
        [
            _written_file("/scratch/share/issue.md", "既知の本文"),
            overwrite,
            _shell_event(
                "atk review-table add /scratch/draft.exec-review.tsv --round 1 --track exec-review "
                "--issue-file /scratch/share/issue.md",
                "成功: 指摘行を1件追加した: /scratch/draft.exec-review.tsv（1件）",
            ),
        ],
    )
    rows = {"official.exec-review.tsv": ["既知の本文"]}
    assert _table_models(list(rows), [tmp_path], rows) == {"official.exec-review.tsv": "codex:gpt-6.2-sol/high"}
