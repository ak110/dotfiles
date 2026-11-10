"""公開prepareの統計から、保持されたhook計測と未記録の範囲を確認する。"""

import json
import pathlib

import pytest
import session_review_prepare as prepare  # pylint: disable=import-error

from agent_toolkit._atk import managed_temp


@pytest.mark.parametrize("recorded", [True, False, "unreadable"])
def test_prepare_reports_retained_hook_timings_by_stage(tmp_path, monkeypatch, capsys, recorded):
    transcript = tmp_path / "11111111-2222-3333-4444-555555555555.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "作業する"}}) + "\n", encoding="utf-8"
    )
    work = tmp_path / "work"
    work.mkdir()
    timing_dir = tmp_path / "timing"
    timing_dir.mkdir()
    monkeypatch.setattr(managed_temp, "find_session_temp", lambda *_args, **_kwargs: timing_dir)
    if recorded:
        rows = [
            {"event": "PreToolUse", "operation": "pretooluse", "stage": stage, "seconds": seconds, "status": "ok"}
            for stage, seconds in [("total", 1), ("total", 3), ("handler", 0.25), ("handler", 0.75)]
        ]
        (timing_dir / "hook-timings.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows) + "broken\n{", encoding="utf-8"
        )
    if recorded == "unreadable":
        original = pathlib.Path.read_bytes

        def read_bytes(path, **kwargs):
            if path == timing_dir / "hook-timings.jsonl":
                raise PermissionError("読取権限なし")
            return original(path, **kwargs)

        monkeypatch.setattr(pathlib.Path, "read_bytes", read_bytes)
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    summary = json.loads(pathlib.Path(result["hook_timings_path"]).read_text(encoding="utf-8"))
    text = pathlib.Path(result["stats_path"]).read_text(encoding="utf-8")
    assert "セッション全体の網羅は保証しない" in text
    assert "占有率へ変換しない" in text
    if recorded == "unreadable":
        assert summary["status"] == "unreadable"
        assert str(timing_dir / "hook-timings.jsonl") in text
        assert "読取権限なし" in text
        assert "次の操作:" in text and "権限" in text
        assert "統計を除いて" in text
        return
    if not recorded:
        assert summary["status"] == "missing"
        assert summary["count"] == 0
        assert not summary["groups"]
        return
    assert summary["status"] == "available"
    assert summary["count"] == 4
    assert summary["invalid_rows"] == 2
    groups = {row["stage"]: row for row in summary["groups"]}
    assert groups["total"]["count"] == 2
    assert groups["total"]["median_seconds"] == 2
    assert groups["total"]["total_seconds"] == 4
    assert groups["handler"]["count"] == 2
    assert groups["handler"]["median_seconds"] == 0.5
    assert groups["handler"]["total_seconds"] == 1
    assert "中央値2.000000秒、合計4.000000秒" in text
    assert "中央値0.500000秒、合計1.000000秒" in text
