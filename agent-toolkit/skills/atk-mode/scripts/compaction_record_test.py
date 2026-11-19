"""会話IDから圧縮後に示す記録の一意な解決を確かめる。"""

import json
import pathlib
import runpy

import pytest


@pytest.mark.parametrize("copies", [0, 1, 2])
def test_compaction_record_uses_unique_current_session(tmp_path, monkeypatch, capsys, copies):
    """別会話を除き、重複・不在時は標準圧縮のため非0を返す。"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    records: list[pathlib.Path] = []
    for index in range(copies):
        directory = tmp_path / "projects" / f"project-{index}"
        directory.mkdir(parents=True)
        record = directory / "current.jsonl"
        record.write_text("{}\n", encoding="utf-8")
        (directory / "other.jsonl").write_text("{}\n", encoding="utf-8")
        records.append(record)
    main = runpy.run_path(str(pathlib.Path(__file__).with_name("compaction_record.py")))["main"]
    code = main(["--session-id", "current"])
    captured = capsys.readouterr()
    if copies == 1:
        assert code == 0
        assert json.loads(captured.out) == {"record_path": str(records[0].resolve())}
        assert not captured.err
    else:
        assert code == 2
        assert not captured.out
        assert "標準の圧縮" in captured.err
