"""固定値解決から公開prepareの処理済みWI候補までを検証する。"""

import datetime
import json
from pathlib import Path

import pytest
import session_review_prepare as prepare

from agent_toolkit._common import state_paths
from agent_toolkit._testing import session_evidence_support as support


@pytest.mark.parametrize("tool", ["Bash", "functions.exec"])
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("f=20261030-120000-001.md; atk wi adopt $f", True),
        ("D=/wi; f=${D}/20261030-120000-001.md; atk wi adopt $f", True),
        ("for f in 20261030-120000-{001,002}.md; do atk wi adopt $f; done", True),
        ("atk wi adopt $f; f=20261030-120000-001.md", False),
        ("f=20261030-120000-001.md; read f; atk wi adopt $f", False),
    ],
)
def test_prepare_resolves_processed_wi_from_the_same_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool: str,
    command: str,
    expected: bool,
) -> None:
    """同一入力の固定値だけを処理済みWIへ対応付け、コメントの振り返りへ渡す。"""
    root = tmp_path / "private-notes"
    directory = root / "processing"
    directory.mkdir(parents=True)
    name = "20261030-120000-001.md"
    (directory / name).write_text(
        "---\ntarget_repo: github.com/example/repo\ntype: awi\n---\n# 対象\n\n## ユーザーコメント\n\nコメントの是正\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(root))
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    entry = support.claude_call("2026-10-31T00:00:02Z", "adopt", tool, {"command": command})
    if tool == "functions.exec":
        entry = support.codex_item(
            "2026-10-31T00:00:02Z",
            {
                "type": "custom_tool_call",
                "name": tool,
                "call_id": "adopt",
                "input": f"await tools.exec_command({{cmd: {json.dumps(command)}}});",
            },
        )
    transcript = tmp_path / "record.jsonl"
    support.write_jsonl(transcript, [entry])
    work = tmp_path / "prepared"
    work.mkdir()
    assert (
        prepare.main(
            ["--transcript", str(transcript), "--work-dir", str(work)],
            now=datetime.datetime(2026, 10, 31, tzinfo=datetime.UTC),
        )
        == 0
    )
    capsys.readouterr()
    rows = [json.loads(line) for line in (work / "bundle/candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    responses = [row["wi_file"] for row in rows if row.get("candidate_kind") == "wi-user-response"]
    assert responses == ([name] if expected else [])
