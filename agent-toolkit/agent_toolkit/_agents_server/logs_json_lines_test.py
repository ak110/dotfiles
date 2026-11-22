import json
import pathlib

import pytest

from agent_toolkit._agents_server import logs_export


@pytest.mark.parametrize("output_format", ["text", "markdown"])
def test_export_logs_preserves_json_string_separators(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], output_format: str
) -> None:
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    path = home / "projects/proj/unicode.jsonl"
    path.parent.mkdir(parents=True)
    texts = [f"前{separator}後" for separator in ("\u2028", "\u2029", "\u0085")]
    path.write_text(
        "".join(json.dumps({"type": "user", "message": {"content": text}}, ensure_ascii=False) + "\n" for text in texts),
        encoding="utf-8",
    )

    code = logs_export.export_logs(
        session_id=None,
        project_dir=None,
        latest=None,
        output_format=output_format,
        output_dir=None,
        include_thinking=False,
        include_subagents=False,
        tool_details=False,
        verbose=True,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert not captured.err
    assert all(text in captured.out for text in texts)
