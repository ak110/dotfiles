"""plugins/agent-toolkit/scripts/stop_advisor.py のテスト。

Stop hook のテスト。transcript 分析と codex resume count による
CLAUDE.md 更新提案の判定を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "stop_advisor.py"


def _run(
    payload: object,
    *,
    state_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    env = os.environ.copy()
    if state_dir is not None:
        env["TMPDIR"] = str(state_dir)
        env["TEMP"] = str(state_dir)
        env["TMP"] = str(state_dir)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _parse_decision(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def _write_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    path.write_text(json.dumps(state), encoding="utf-8")


def _write_transcript(tmp_path: pathlib.Path, content: str) -> pathlib.Path:
    transcript = tmp_path / "transcript.txt"
    transcript.write_text(content, encoding="utf-8")
    return transcript


class TestKeywordDetection:
    """修正キーワードによる発火判定。"""

    def test_below_threshold_approves(self, tmp_path: pathlib.Path):
        # 2 keywords (threshold is 3)
        transcript = _write_transcript(tmp_path, "user: \u9055\u3046\u3001\u305d\u308c\u306f\u9593\u9055\u3044\u3060")
        result = _run(
            {"session_id": "kw-low", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_at_threshold_blocks(self, tmp_path: pathlib.Path):
        # 3 keywords
        transcript = _write_transcript(
            tmp_path,
            "user: \u9055\u3046\u3001\u305d\u308c\u306f\u9593\u9055\u3044\u3060\u3002\u3084\u308a\u76f4\u3057\u3066",
        )
        result = _run(
            {"session_id": "kw-hit", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        assert "systemMessage" in decision


class TestCodexResumeDetection:
    """codex resume count による発火判定。"""

    def test_below_threshold_approves(self, tmp_path: pathlib.Path):
        _write_state(tmp_path, "cr-low", {"codex_resume_count": 1})
        transcript = _write_transcript(tmp_path, "no corrections here")
        result = _run(
            {"session_id": "cr-low", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_at_threshold_blocks(self, tmp_path: pathlib.Path):
        _write_state(tmp_path, "cr-hit", {"codex_resume_count": 2})
        transcript = _write_transcript(tmp_path, "no corrections here")
        result = _run(
            {"session_id": "cr-hit", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        assert "codex" in decision.get("systemMessage", "").lower()


class TestBothConditions:
    """両方の条件を同時に満たす場合。"""

    def test_both_triggered(self, tmp_path: pathlib.Path):
        _write_state(tmp_path, "both", {"codex_resume_count": 3})
        transcript = _write_transcript(
            tmp_path,
            "\u9055\u3046 \u9593\u9055 \u3084\u308a\u76f4\u3057\u3066 \u623b\u3057\u3066",
        )
        result = _run(
            {"session_id": "both", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        msg = decision.get("systemMessage", "")
        assert "correction" in msg.lower()
        assert "codex" in msg.lower()


class TestStopAdviceOnce:
    """1 セッション 1 回の制限。"""

    def test_second_stop_approves(self, tmp_path: pathlib.Path):
        _write_state(tmp_path, "once", {"stop_advice_given": True})
        result = _run(
            {"session_id": "once", "transcript_path": "/nonexistent"},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestEdgeCases:
    """エッジケース。"""

    def test_invalid_json_approves(self, tmp_path: pathlib.Path):
        result = _run("not json", state_dir=tmp_path)
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_missing_transcript_approves(self, tmp_path: pathlib.Path):
        result = _run(
            {"session_id": "no-transcript", "transcript_path": "/nonexistent/file"},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_empty_session_id_approves(self, tmp_path: pathlib.Path):
        result = _run({"session_id": "", "transcript_path": "/x"}, state_dir=tmp_path)
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
