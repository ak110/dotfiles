"""セッション振り返りの試験（session_review_evidence_support）。"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

from agent_toolkit._atk.run_script import registered_script_path
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import read_jsonl

# 単体の解析・実行でも、登録済みスクリプトと同じディレクトリから補助モジュールを解決する。
sys.path.insert(0, str(registered_script_path("session-review-evidence").parent))
import session_review_evidence as evidence  # noqa: E402  # pylint: disable=wrong-import-position,wrong-import-order,import-error


def _bundle_failed_codex_commands(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    cases: list[tuple[list[str], int, str]],
) -> list[dict]:
    """CommandExecutionの失敗列を公開されたbundleコマンドへ渡し、候補レコードを返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "failed",
                        "command": command,
                        "exit_code": code,
                        "stderr": diagnostic,
                    },
                },
            }
            for command, code, diagnostic in cases
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys)
    return [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]


def _user_events(argv: list[str], capsys: pytest.CaptureFixture[str]) -> list[dict]:
    """`--user-events`を実行し、終了コード0を確かめて全イベントを返す。"""
    assert evidence.main([*argv, "--user-events", "--since", "2026-09-01T00:00:00Z"]) == 0
    return read_jsonl(capsys, raw=True)


def _bundle_delegate_return_texts(tmp_path: pathlib.Path, delegate_entries: list[dict[str, object]]) -> list[str]:
    """委譲先記録を1件持つtranscriptから`--bundle`で候補を生成し、`delegate-return`候補と`escalation`候補の本文を返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "最初の依頼"}}])
    subagents = transcript.with_suffix("") / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-delegate.jsonl").write_text(
        "\n".join(json.dumps({**entry, "isSidechain": True}, ensure_ascii=False) for entry in delegate_entries) + "\n",
        encoding="utf-8",
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0

    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    return [
        record["text"]
        for record in records
        if record["kind"] == "candidate" and record["candidate_kind"] in {"delegate-return", "escalation"}
    ]


def _bundle_candidates_and_evidence(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], entries: list[dict]
) -> tuple[list[dict], dict[str, dict]]:
    """transcriptから`--bundle`を実行し、候補の行と候補ID別の個別証拠を返す。"""
    transcript = _write_transcript(tmp_path, entries)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    capsys.readouterr()
    candidates = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    items = {
        item["candidate_id"]: json.loads(
            (bundle_dir / "candidate-evidence" / f"{item['candidate_id']}.json").read_text("utf-8")
        )
        for item in candidates
        if item["kind"] == "candidate"
    }
    return candidates, items


def _bundle_failed_commands_of_runtime(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    runtime: str,
    cases: list[tuple[str, int, str]],
) -> tuple[list[dict], list[int]]:
    """シェルのコマンド文字列の失敗列を、Claude CodeのBashかCodexの`CommandExecution`の記録として公開bundleへ渡す。

    候補レコードと、各ケースの失敗を記録した行番号を返す。
    """
    if runtime == "codex":
        records = _bundle_failed_codex_commands(
            tmp_path, capsys, [(["bash", "-lc", command], code, output) for command, code, output in cases]
        )
        return records, list(range(1, len(cases) + 1))
    entries: list[dict] = []
    for index, (command, code, output) in enumerate(cases):
        call_id = f"call-{index}"
        entries += [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": command}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "is_error": True,
                            "content": f"Exit code {code}" + (f"\n{output}" if output else ""),
                        }
                    ],
                },
            },
        ]
    transcript = _write_transcript(tmp_path, entries)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    read_jsonl(capsys)
    records = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    return records, [2 * index + 2 for index in range(len(cases))]
