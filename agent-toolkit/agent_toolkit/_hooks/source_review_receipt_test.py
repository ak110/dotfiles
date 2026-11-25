import pathlib

from agent_toolkit._hooks import source_review_receipt


def test_only_the_current_full_read_counts(tmp_path: pathlib.Path, monkeypatch) -> None:
    reads: dict = {}
    monkeypatch.setattr(source_review_receipt, "read_state", lambda _: reads)
    monkeypatch.setattr(source_review_receipt, "update_state", lambda _, update: reads.update(update(reads)))
    manifest = tmp_path / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir()
    manifest.write_text('{"name":"agent-basics"}', encoding="utf-8")
    document = tmp_path / "skills/writing-standards/references/design-heuristics.md"
    document.parent.mkdir(parents=True)
    content = "# 評価基準\n\n本文\n"
    document.write_text(content, encoding="utf-8")
    assert not source_review_receipt.ready("case")
    args = {"file_path": str(document)}
    for shown in ("# 評価基準\n", content):
        source_review_receipt.observe({"tool_response": {"file": {"content": shown}}}, "case", "Read", args, str(tmp_path))
        assert source_review_receipt.ready("case") is (shown == content)
    document.write_text(content + "追記\n", encoding="utf-8")
    assert not source_review_receipt.ready("case")
