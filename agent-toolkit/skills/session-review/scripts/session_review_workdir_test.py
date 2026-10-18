"""準備コマンドが通常起動の領域を確保し、生成物と失敗診断を保持する契約。"""

import json
import pathlib

import session_review_prepare as prepare

from agent_toolkit._testing import delegated_threads
from agent_toolkit._testing.helpers import _write_transcript


def test_prepare_allocates_distinct_workdirs_and_keeps_documents(tmp_path: pathlib.Path, capsys) -> None:
    """事前作成なしの再実行でも両方の文書を後から読める。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])
    outputs = []
    for _ in range(2):
        assert prepare.main(["--transcript", str(transcript)]) == 0
        result = json.loads(capsys.readouterr().out)
        outputs.append(result)
    assert outputs[0]["work_dir"] != outputs[1]["work_dir"]
    for result in outputs:
        for key in ("conversation_path", "candidates_path", "stats_path", "comparison_materials_path"):
            assert pathlib.Path(result[key]).is_file()


def test_prepare_allocation_error_is_actionable(tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
    """領域確保の失敗は成功JSONへ変換しない。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    def fail(*args, **kwargs):
        raise OSError("領域確保失敗")

    monkeypatch.setattr(prepare.managed_temp, "resolve_session_temp", fail)
    assert prepare.main(["--transcript", str(transcript)]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "領域確保失敗" in captured.err
    assert "次の操作" in captured.err


def test_prepare_keeps_allocated_directory_on_extraction_failure(tmp_path: pathlib.Path, capsys) -> None:
    """確保後の抽出失敗は保持先を診断に示す。"""
    transcript = tmp_path / "broken.jsonl"
    transcript.write_bytes(b"\xff")
    assert prepare.main(["--transcript", str(transcript)]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "診断用に保持した作業領域:" in captured.err


def test_prepare_allocates_workdir_from_codex_thread(tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
    """thread指定の通常起動と引き継ぎの会話だけの起動も事前作成を要さない。"""
    family = delegated_threads.write_delegated_threads(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(family.codex_home))
    assert prepare.main(["--codex-thread-id", delegated_threads.CODEX_THREAD_ID, "--conversation-only"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert pathlib.Path(record["work_dir"]).is_dir()
    assert pathlib.Path(record["conversation_path"]).read_text(encoding="utf-8")
