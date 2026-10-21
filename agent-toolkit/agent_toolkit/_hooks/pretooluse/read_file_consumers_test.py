"""公開read-fileの同時後置オプションを3つのhook消費側へ渡す。"""

import json
import pathlib
import shlex

import pytest

from agent_toolkit import atk
from agent_toolkit._hooks.pretooluse import confirmation_reads, dispatch, large_reads


def _read_file_response(output: str) -> tuple[dict, str]:
    """read-fileのメタデータと無加工本文を分ける。"""
    metadata, separator, body = output.partition("\n")
    assert separator
    return json.loads(metadata), body


def test_public_read_file_accepts_both_trailing_options(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """startとmax-bytesを同時に後置しても、前置と同じ範囲・JSONを返す。"""
    path = tmp_path / "with space.md"
    path.write_text("範囲読取の原文\n" * 100, encoding="utf-8")
    outputs = []
    for arguments in (
        ["read-file", "--start", "3", "--max-bytes", "512", "--", str(path)],
        ["read-file", "--", str(path), "--start", "3", "--max-bytes", "512"],
    ):
        with pytest.raises(SystemExit, match="0"):
            atk.main(arguments)
        result = capsys.readouterr()
        assert not result.err
        outputs.append(_read_file_response(result.out))
    assert outputs[0] == outputs[1]
    metadata, _body = outputs[1]
    assert metadata["start"] == 3 and metadata["next"] > 3
    assert not metadata["eof"]


@pytest.mark.parametrize("trailing", [False, True])
def test_same_read_commands_reach_all_three_consumers(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, trailing: bool
) -> None:
    """同じ読取入力から大量読取・確認資料・commit資料の各消費側が対象パスを認識する。"""
    root = pathlib.Path(large_reads.__file__).resolve().parents[3]
    targets = [
        root / "skills/user-confirmation-and-report/references/approval-scope.md",
        root / "skills/user-confirmation-and-report/references/choice-construction.md",
        root / "skills/commit/references/message.md",
    ]
    commands = [
        shlex.join(
            [
                "atk",
                "read-file",
                *([] if trailing else ["--start", "0", "--max-bytes", "16384"]),
                "--",
                str(path),
                *(["--start", "0", "--max-bytes", "16384"] if trailing else []),
            ]
        )
        for path in targets
    ]
    command = "; ".join(commands)
    assert list(large_reads.bash_read_paths(command, str(tmp_path), include_partial=True)) == [(path,) for path in targets]
    # この識別処理自体が指定された消費側であり、実在するcommit資料を渡して判定する。
    assert dispatch._reads_commit_message_rules(command, str(tmp_path))  # pylint: disable=protected-access
    entries = [
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": "read-references", "name": "Bash", "input": {"command": command}}]
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "read-references", "content": "取得済み"}]},
        },
    ]
    transcript = tmp_path / "reads.jsonl"
    transcript.write_text("\n".join(json.dumps(entry) for entry in entries), encoding="utf-8")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    assert (
        confirmation_reads.unread_reference_warning(
            {"transcript_path": str(transcript), "cwd": str(tmp_path)}, "AskUserQuestion"
        )
        is None
    )
    transcript.write_text("", encoding="utf-8")
    assert (
        confirmation_reads.unread_reference_warning(
            {"transcript_path": str(transcript), "cwd": str(tmp_path)}, "AskUserQuestion"
        )
        is not None
    )
