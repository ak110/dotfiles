# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""セッション振り返りの試験（session_review_prepare_candidates_test）。"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
import session_review_prepare as prepare

from agent_toolkit._atk.wi import layout
from agent_toolkit._testing import session_evidence_support as support
from agent_toolkit._testing.session_review_prepare_support import _FIXED_NOW, _LONG_INTERVENTION, _bundle_rows, _work_dir
from agent_toolkit._testing.session_review_prepare_support import _isolate_failure_ledger as _isolate_failure_ledger


def _write_wi(root: pathlib.Path, state: str, name: str, frontmatter: dict[str, str], body: str) -> None:
    """private-notesへWIを1件書く。frontmatterの形は`atk wi add`が保存する形に合わせる。"""
    directory = root / state
    directory.mkdir(parents=True, exist_ok=True)
    head = "".join(f"{key}: {value}\n" for key, value in frontmatter.items())
    (directory / name).write_text(f"---\n{head}---\n{body}", encoding="utf-8")


def _uwi_body(question: str, answer: str) -> str:
    return (
        f"\n## 質問\n\n{question}\n\n## 判断材料\n\n- 材料\n\n## 回答\n\n"
        f"<!-- ユーザーはこの行以降に回答を追記する -->\n{answer}\n"
    )


def _bash_entry(timestamp: str, call_id: str, command: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": command}}],
        },
    }


@pytest.mark.parametrize(
    ("dated", "past_state"),
    [(False, "adopted"), (True, "adopted"), (True, "future-state")],
    ids=["legacy-layout", "dated-layout", "future-state"],
)
def test_prepare_marks_wi_candidates_and_similar_past_records(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, dated: bool, past_state: str
) -> None:
    """投入したUWIを確認の候補、WIの記入欄の是正を是正の候補として載せ、ユーザーの是正へ過去の同種記録を添える。

    報告用UWI、選択肢と一致する回答、読んだだけのWI、引用の中のコマンド名は候補にしない。
    過去の同種記録はユーザーの発話を保持する節だけと比べ、記入元のWI自身と短い文の一致を含めない。
    """
    session_id = "11111111-2222-3333-4444-555555555555"
    root = tmp_path / "private-notes"
    correction = "エージェントが判断すべきことをユーザーに委ねないで、判断できないことだけを聞いてほしい"
    uwi = {"target_repo": "github.com/example/repo", "type": "uwi", "question_type": "choice", "choices": "進める,止める"}
    _write_wi(
        root, "adopted", "20261006-100000-001.md", {**uwi, "submitter_session": session_id}, _uwi_body("確認A？", "進める")
    )
    _write_wi(
        root, "adopted", "20261006-100000-002.md", {**uwi, "submitter_session": session_id}, _uwi_body("確認B？", correction)
    )
    _write_wi(
        root,
        "inbox",
        "20261006-100000-003.md",
        {**uwi, "submitter_session": session_id},
        _uwi_body("2026年10月6日のrepoの作業結果と振り返りについて、この対応で問題ありませんか？", "問題がある"),
    )
    _write_wi(root, "adopted", "20261006-100000-004.md", uwi, _uwi_body("他のセッションの確認？", "読んだだけの是正の記入"))
    _write_wi(root, "processing", "20261006-100000-005.md", uwi, _uwi_body("処理した確認？", "処理した是正の記入"))
    awi = {"target_repo": "github.com/example/repo", "type": "awi"}
    _write_wi(
        root, "processing", "20261006-100000-006.md", awi, "\n# 処理したAWI\n\n本文\n\n## ユーザーコメント\n\nコメントの是正\n"
    )
    _write_wi(root, "inbox", "20261006-100000-007.md", awi, "\n# 引用だけのAWI\n\n本文\n\n## ユーザーコメント\n\n引用の是正\n")
    _write_wi(
        root,
        "adopted",
        "20261001-163353-001.md",
        awi,
        f"\n# 過去の同種の是正への対策\n\n## ユーザー指摘の逐語引用\n\n```text\n{correction}。\n```\n",
    )
    _write_wi(root, "adopted", "20261001-163353-002.md", awi, f"\n# 本文だけに同じ文を持つ項目\n\n{correction}\n")
    if dated:
        destination = root / "2026" / "10"
        destination.mkdir(parents=True)
        for path in sorted(root.glob("*/*.md")):
            text = path.read_text(encoding="utf-8")
            text = text.replace("---\n", f"---\nstate: {path.parent.name}\n", 1)
            (destination / path.name).write_text(text, encoding="utf-8")
            path.unlink()
    if past_state == "future-state":
        original_entry_state_of = layout.entry_state_of
        monkeypatch.setattr(
            layout,
            "entry_state_of",
            lambda path, *args, **kwargs: (
                past_state if path.name == "20261001-163353-001.md" else original_entry_state_of(path, *args, **kwargs)
            ),
        )
    entries = [
        {"type": "user", "timestamp": "2026-10-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        _bash_entry("2026-10-06T12:00:10Z", "toolu_show", "atk wi show --skip-pull 20261006-100000-004.md"),
        _bash_entry("2026-10-06T12:00:20Z", "toolu_adopt", "atk wi adopt 20261006-100000-005.md --target-repo=/repo"),
        _bash_entry("2026-10-06T12:00:30Z", "toolu_start", "atk wi start-processing 20261006-100000-006.md"),
        _bash_entry("2026-10-06T12:00:40Z", "toolu_echo", "echo 'atk wi adopt 20261006-100000-007.md'"),
        {"type": "user", "timestamp": "2026-10-06T12:01:00Z", "message": {"role": "user", "content": correction}},
        {
            "type": "user",
            "timestamp": "2026-10-06T12:02:00Z",
            "message": {"role": "user", "content": "上限をリセットした。続けて"},
        },
    ]
    transcript = tmp_path / f"{session_id}.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW) == 0

    record = json.loads(capsys.readouterr().out)
    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    saved = _bundle_rows(record, "candidates.jsonl")
    confirmations = [item for item in saved if item.get("candidate_kind") == "confirmation-request"]
    responses = [item for item in saved if item.get("candidate_kind") == "wi-user-response"]
    assert [item["wi_file"] for item in confirmations] == [
        "20261006-100000-001.md",
        "20261006-100000-002.md",
    ]
    assert [item["wi_file"] for item in responses] == [
        "20261006-100000-002.md",
        "20261006-100000-005.md",
        "20261006-100000-006.md",
    ]
    assert record["candidate_counts"] == {"confirmation-request": 2, "user-intervention": 2, "wi-user-response": 3}
    assert sorted(record["mandatory_candidates"]) == sorted(
        line.split()[1] for line in candidates.splitlines() if "評価が必須）" in line
    )
    assert len(record["mandatory_candidates"]) == 7
    by_text = {line.split()[1]: line for line in candidates.splitlines() if line.startswith("- c") and "評価が必須" in line}
    intervention_id = next(
        candidate_id for candidate_id, line in by_text.items() if "user-intervention" in line and "委ねないで" in line
    )
    response_id = next(
        candidate_id for candidate_id, line in by_text.items() if "wi-user-response" in line and "委ねないで" in line
    )
    reset_id = next(candidate_id for candidate_id, line in by_text.items() if "上限をリセットした" in line)
    # ユーザーの発話を保持する節（逐語引用、回答）に同じ文を持つWIだけを添え、本文だけに同じ文を持つ項目は含めない。
    # 回答欄の是正では、記入元のUWI自身を過去の同種記録から除く。
    assert record["similar_records"][intervention_id] == ["20261001-163353-001.md", "20261006-100000-002.md"]
    assert record["similar_records"][response_id] == ["20261001-163353-001.md"]
    assert record["similar_records"][reset_id] == []
    saved_similar = {item["candidate_id"]: item["similar_records"] for item in _bundle_rows(record, "review-index.jsonl")}
    assert saved_similar[reset_id] == []
    assert saved_similar[response_id] == [["20261001-163353-001.md", past_state, "過去の同種の是正への対策"]]


@pytest.mark.parametrize(
    "command",
    [
        "for f in 20261006-100000-006.md; do atk wi adopt $f; done",
        'for f in 20261006-100000-006.md; do atk wi adopt "${f}"; done',
        "{ atk wi adopt 20261006-100000-006.md; } > o",
        "if true; then atk wi adopt 20261006-100000-006.md; fi",
    ],
    ids=["for-loop", "for-loop-braced", "group", "if"],
)
def test_prepare_counts_wi_processed_inside_compound_commands(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    """複合コマンドとグループの中で処理したWIも処理済みとして扱い、ユーザーの記入を評価の候補へ載せる。

    数え漏れると、処理したWIへのユーザーの是正が振り返りの評価から抜ける。
    """
    root = tmp_path / "private-notes"
    awi = {"target_repo": "github.com/example/repo", "type": "awi"}
    _write_wi(
        root, "processing", "20261006-100000-006.md", awi, "\n# 処理したAWI\n\n本文\n\n## ユーザーコメント\n\nコメントの是正\n"
    )
    entries = [
        {"type": "user", "timestamp": "2026-10-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        _bash_entry("2026-10-06T12:00:10Z", "toolu_adopt", command),
    ]
    transcript = tmp_path / "11111111-2222-3333-4444-555555555555.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW) == 0

    record = json.loads(capsys.readouterr().out)
    responses = [item for item in _bundle_rows(record, "candidates.jsonl") if item.get("candidate_kind") == "wi-user-response"]
    assert [item["wi_file"] for item in responses] == ["20261006-100000-006.md"]


@pytest.mark.parametrize(
    ("first_text", "expected_candidates", "expected_initial_request"),
    [
        (
            "<command-name>/goal</command-name>\n"
            '<command-args><atk-auto source="process-loop" kind="goal">'
            "`agent-toolkit:process-wi`を完遂してください。</atk-auto></command-args>",
            {"user-intervention": 2},
            None,
        ),
        ("この変更を実装して", {"user-intervention": 2}, 1),
    ],
    ids=["process-loop-start", "human-request"],
)
def test_prepare_keeps_first_human_intervention_after_process_loop_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    first_text: str,
    expected_candidates: dict[str, int],
    expected_initial_request: int | None,
) -> None:
    """process-loopの起動後に続く人間の発話は候補へ残し、人間の依頼で始まる記録では初期依頼を除く。"""
    monkeypatch.setenv("PATH", str(tmp_path / "no-atk"))
    entries = [
        {"type": "user", "timestamp": "2026-09-06T12:00:00Z", "message": {"role": "user", "content": first_text}},
        {"type": "user", "timestamp": "2026-09-06T12:00:30Z", "message": {"role": "user", "content": "対象を絞らないで"}},
        {"type": "user", "timestamp": "2026-09-06T12:01:00Z", "message": {"role": "user", "content": "それも直して"}},
    ]
    transcript = tmp_path / "11111111-2222-3333-4444-666666666666.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")

    exit_code = prepare.main(["--transcript", str(transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0
    record = json.loads(capsys.readouterr().out)
    assert record["candidate_counts"] == expected_candidates
    assert record["excluded_counts"].get("initial-request") == expected_initial_request


def test_prepare_keeps_improvement_lines_in_omitted_middle(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """予算内の長い発話を1行で通読し、改善点は全件の保存結果から取得できる。"""
    first_note = "気付いた改善点: atk agents waitの出力をjqで加工するスクリプトを3回作った（ツールの出力形式）"
    second_note = "  気付いた改善点: 全件そろうまで戻らない回収ループで先に届いた結果を待たせた（待ち方の手順）"
    filler = "作業の経過を説明する。" * 60
    report = "\n".join(
        ["作業完了報告。" + "冒頭の説明。" * 90, filler, first_note, filler, second_note, filler, "末尾の結び。" * 90]
    )
    entries = [
        {"type": "user", "timestamp": "2026-09-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-06T12:00:10Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": report}]},
        },
        {"type": "user", "timestamp": "2026-09-06T12:01:00Z", "message": {"role": "user", "content": _LONG_INTERVENTION}},
    ]
    transcript = tmp_path / "33333333-4444-5555-6666-777777777777.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW) == 0

    result = json.loads(capsys.readouterr().out)
    conversation = pathlib.Path(result["conversation_path"]).read_text(encoding="utf-8")
    assert report not in conversation
    assert result["improvement_lines"] == [first_note, second_note.strip()]
    assert " ".join(report.split()) in conversation
    assert _LONG_INTERVENTION in conversation
    assert len(conversation.encode("utf-8")) <= 100_000


def _delegate_adhoc_session(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """メインがCodexの委譲先（役割文書exec、mode task）を起動し、委譲先がその場のコードを実行したセッションを書く。

    記録の形は`agent_toolkit._testing.session_evidence_support`のCodex rollout（2026年10月4日の記録）を写す。
    """
    parent = support.tool_call_session(tmp_path, monkeypatch, "rg needle")
    entries = [json.loads(line) for line in parent.read_text(encoding="utf-8").splitlines()]
    entries[5]["message"]["content"][0]["input"]["mode"] = "task"
    support.write_jsonl(parent, entries)
    codex = next((tmp_path / "codex").rglob("*.jsonl"))
    entries = [json.loads(line) for line in codex.read_text(encoding="utf-8").splitlines()]
    entries[1:1] = [
        support.codex_item(
            "2026-10-06T00:00:05Z",
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "次の文書の手順を実行せよ（出所: /plugin/share/exec.subagent.md）。\n作業"}
                ],
            },
        ),
        support.codex_item(
            "2026-10-06T00:00:06Z",
            {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "c-py",
                "arguments": '{"cmd": "python3 -c \'print(2)\'"}',
            },
        ),
        support.codex_item("2026-10-06T00:00:06.500Z", {"type": "function_call_output", "call_id": "c-py", "output": "2"}),
    ]
    support.write_jsonl(codex, entries)
    return parent


def test_prepare_adds_record_provenance_to_candidate_locations(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """委譲先の記録にある候補の記録位置へ、役割文書・起動mode・起動元とその役割を添える。

    要約の文言（コマンドやパス）から実行主体を推させないための契約であり、期待値は同じbundleの
    `record-provenance`の項目（`role_document`、`mode`、`source_record`）から導く。
    """
    parent = _delegate_adhoc_session(tmp_path, monkeypatch)

    exit_code = prepare.main(["--transcript", str(parent), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0, capsys.readouterr().err
    record = json.loads(capsys.readouterr().out)
    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    delegate = f"codex:{support.TOOL_CALL_THREAD}"
    provenance = next(line for line in candidates.splitlines() if f"由来: {delegate}=" in line)
    assert provenance.startswith(f"- r1: 由来: {delegate}=役割文書exec・mode task・起動元claude:parent-session:")
    assert provenance.endswith("（メイン）")


def test_prepare_marks_candidate_locations_without_provenance(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """由来を持たない証拠bundle（`record-provenance`を出力する前の版で作成した記録など）では、取得できなかったことを示す。"""
    parent = _delegate_adhoc_session(tmp_path, monkeypatch)
    original = prepare._read_jsonl  # pylint: disable=protected-access

    def read_without_provenance(path: pathlib.Path) -> list[dict[str, Any]]:
        return [event for event in original(path) if event.get("kind") != "record-provenance"]

    monkeypatch.setattr(prepare, "_read_jsonl", read_without_provenance)

    exit_code = prepare.main(["--transcript", str(parent), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0, capsys.readouterr().err
    record = json.loads(capsys.readouterr().out)
    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    assert f"由来: codex:{support.TOOL_CALL_THREAD}=由来を取得できなかった" in candidates


def test_escalation_candidate_requires_evaluation() -> None:
    """続行不能の返却は担当の作業遂行不能を示すため、後続で回復しても評価必須の候補にする。"""
    assert prepare.is_mandatory({"candidate_kind": "escalation"})
    assert not prepare.is_mandatory({"candidate_kind": "delegate-return"})
