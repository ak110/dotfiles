"""agent-toolkit/scripts/stop_advisor.py のテスト。

`_stop_gate.is_real_session_end`ゲートを通じた
未コミット変更ブロックとセッション振り返り提案ブロックの判定を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "stop_advisor.py"


def _run(
    payload: object,
    *,
    state_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
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
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _write_transcript(tmp_path: pathlib.Path, user_texts: str | list[str]) -> pathlib.Path:
    """ユーザー発話をJSONL形式のtranscriptとして書き込む。

    文字列1つを渡すとuser turn 1件、リストを渡すと複数のuser turnとして書き込む。
    """
    if isinstance(user_texts, str):
        user_texts = [user_texts]
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False) for text in user_texts
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transcript


def _write_transcript_with_assistant_last(
    tmp_path: pathlib.Path,
    assistant_content: list[dict],
    user_text: str = "hello",
) -> pathlib.Path:
    """アシスタントターンを末尾に持つtranscriptを書き込む。

    完了文言ゲート・質問中判定の検証で共有する。
    """
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": user_text}}, ensure_ascii=False),
        json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": assistant_content},
            },
            ensure_ascii=False,
        ),
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transcript


# 完了文言・未完了文言・質問付き完了文言の典型例。
# 真のセッション終了ゲート（`_stop_gate.is_real_session_end`）の判定を再現する。
_COMPLETION_TEXT = "作業が完了しました。"
_NO_COMPLETION_TEXT = "調査を続けます。"
_COMPLETION_WITH_QUESTION = "実装が完了しました。どうしますか？"


class TestSessionReviewBlock:
    """セッション振り返り提案ブロックの基本動作。

    `is_real_session_end`ゲートが真の場合に発火する。
    """

    def test_completion_blocks(self, tmp_path: pathlib.Path):
        """完了文言ありかつ質問なしのアシスタントターンは振り返り提案を発火する。"""
        transcript = _write_transcript_with_assistant_last(
            tmp_path,
            [{"type": "text", "text": _COMPLETION_TEXT}],
        )
        result = _run(
            {"session_id": "review-block", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        reason = decision["reason"]
        # LLM宛てメッセージ規約: プレフィックスとサフィックスが付与されていること。
        assert "[auto-generated: agent-toolkit/stop_advisor]" in reason
        assert "Auto-generated hook notice" in reason
        # 自己完結化の注意書きが含まれていること（履歴参照を避ける指示）。
        assert "stand alone" in reason

    def test_no_completion_approves(self, tmp_path: pathlib.Path):
        """完了文言がないアシスタントターンは振り返り提案を発火しない。"""
        transcript = _write_transcript_with_assistant_last(
            tmp_path,
            [{"type": "text", "text": _NO_COMPLETION_TEXT}],
        )
        result = _run(
            {"session_id": "review-no-completion", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_question_approves(self, tmp_path: pathlib.Path):
        """完了文言があっても質問中であれば発火しない。"""
        transcript = _write_transcript_with_assistant_last(
            tmp_path,
            [{"type": "text", "text": _COMPLETION_WITH_QUESTION}],
        )
        result = _run(
            {"session_id": "review-question", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_gate_failure_does_not_record_advice_given(self, tmp_path: pathlib.Path):
        """ゲート不通過時は`stop_advice_given`を記録せず、再評価を許容する。"""
        transcript = _write_transcript_with_assistant_last(
            tmp_path,
            [{"type": "text", "text": _NO_COMPLETION_TEXT}],
        )
        first = _run(
            {"session_id": "review-rerun", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        second = _run(
            {"session_id": "review-rerun", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert _parse_decision(first)["decision"] == "approve"
        assert _parse_decision(second)["decision"] == "approve"


class TestStopAdviceOnce:
    """1セッション1回の制限。"""

    def test_second_stop_approves(self, tmp_path: pathlib.Path):
        _write_state(tmp_path, "once", {"stop_advice_given": True})
        result = _run(
            {"session_id": "once", "transcript_path": "/nonexistent"},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_block_records_advice_given(self, tmp_path: pathlib.Path):
        """ゲート通過 → blockの直後は再Stopで即approveする。"""
        transcript = _write_transcript_with_assistant_last(
            tmp_path,
            [{"type": "text", "text": _COMPLETION_TEXT}],
        )
        first = _run(
            {"session_id": "once-block", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        second = _run(
            {"session_id": "once-block", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert _parse_decision(first)["decision"] == "block"
        assert _parse_decision(second)["decision"] == "approve"


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


class TestUncommittedChanges:
    """未コミット変更の検出。"""

    def test_blocks_with_uncommitted_changes(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """未コミット変更ありかつ振り返り未発火の初回Stopは、両通知を1blockにまとめて返す。"""
        repo = make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": _COMPLETION_TEXT}]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "dirty", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        reason = decision.get("reason", "")
        # 未コミット通知と振り返り提案の両方が含まれること。
        assert "uncommitted" in reason.lower()
        assert "stand alone" in reason
        # 2通知それぞれに自動生成プレフィックスが付与されること（連結時の境界保持）。
        assert reason.count("[auto-generated: agent-toolkit/stop_advisor]") == 2
        assert "Auto-generated hook notice" in reason

    def test_blocks_uncommitted_only_after_review_given(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """振り返り発火済みかつ未コミット未発火なら、未コミット通知のみを返す。"""
        repo = make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": _COMPLETION_TEXT}]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        _write_state(tmp_path, "uncommitted-only", {"stop_advice_given": True})
        result = _run(
            {"session_id": "uncommitted-only", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        reason = decision.get("reason", "")
        assert "uncommitted" in reason.lower()
        assert "stand alone" not in reason
        assert reason.count("[auto-generated: agent-toolkit/stop_advisor]") == 1

    def test_approves_clean_repo(self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]):
        repo = make_clean_repo(tmp_path)
        transcript = _write_transcript(tmp_path, "no corrections")
        result = _run(
            {"session_id": "clean", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_allows_after_block_limit(self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]):
        repo = make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": _COMPLETION_TEXT}]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        # 1回ブロック後、後続の振り返り提案ブロックへフォールスルーする。
        # `stop_advice_given`を併せて設定し振り返り側も即approveとし、未コミット側のみ通過することを示す。
        _write_state(tmp_path, "limit", {"uncommitted_block_count": 1, "stop_advice_given": True})
        result = _run(
            {"session_id": "limit", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_untracked_only_approves(self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]):
        """untrackedファイルのみの場合はブロックしない。"""
        repo = make_clean_repo(tmp_path)
        (repo / "untracked.txt").write_text("new file")
        transcript = _write_transcript(tmp_path, "no corrections")
        result = _run(
            {"session_id": "untracked", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_no_completion_keyword_approves(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """未コミット変更があっても、直前アシスタントターンに完了文言がなければblockしない。"""
        repo = make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": _NO_COMPLETION_TEXT}]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "no-completion", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestCompletionKeyword:
    """作業完了文言ゲート: 直前アシスタントターンに完了文言を含むときのみ未コミットblockする。"""

    @pytest.mark.parametrize(
        ("keyword_label", "assistant_text"),
        [
            ("shimashita", "実装が完了しました。"),
            ("itashimashita", "実装が完了いたしました。"),
            ("itashimashita_kanji", "実装が完了致しました。"),
            ("desu", "作業は以上で完了です。"),
        ],
    )
    def test_completion_keyword_triggers_block(
        self,
        tmp_path: pathlib.Path,
        keyword_label: str,
        assistant_text: str,
        make_dirty_repo: Callable[[pathlib.Path], pathlib.Path],
    ):
        repo = make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": assistant_text}]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": f"completion-{keyword_label}", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        assert "uncommitted" in decision.get("reason", "").lower()


class TestQuestionSuppressesUncommittedBlock:
    """Claudeがユーザーに質問中の場合は完了文言があっても未コミット変更ブロックを抑制する。"""

    def test_ask_user_question_tool_suppresses_block(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """AskUserQuestionツール呼び出しが最後にある場合はブロックしない。"""
        repo = make_dirty_repo(tmp_path)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "実装が完了しました。どちらを選びますか？"},
            {"type": "tool_use", "id": "x", "name": "AskUserQuestion", "input": {}},
        ]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "ask-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_fullwidth_question_mark_suppresses_block(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """テキストが全角「？」で終わる場合はブロックしない。"""
        repo = make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": "実装が完了しました。ステージ済みファイルをどうしますか？"}]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "fw-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_halfwidth_question_mark_suppresses_block(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """テキストが半角「?」で終わる場合はブロックしない。"""
        repo = make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": "実装が完了しました。Which option do you prefer?"}]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "hw-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_mid_text_question_mark_suppresses_block(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """テキストの末尾でなくとも「?」や「？」が含まれていれば質問扱いでブロックしない。"""
        repo = make_dirty_repo(tmp_path)
        content = [
            {
                "type": "text",
                "text": "実装が完了しました。続行してよいですか？ 判断をいただき次第、残タスクを進めます。",
            }
        ]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "mid-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_question_in_earlier_block_suppresses_block(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """同一ターンの先頭テキストブロックに「?」があり、末尾ブロックに無い場合も質問扱いでブロックしない。"""
        repo = make_dirty_repo(tmp_path)
        content = [
            {"type": "text", "text": "実装が完了しました。この方針で進めますか？"},
            {"type": "text", "text": "ご判断をお願いします。"},
        ]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "split-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_non_question_text_still_blocks(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """完了文言があり「?」を含まないテキストの場合は通常通りブロックする。"""
        repo = make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": "コミット前の実装が完了しました。"}]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "no-q", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        assert "uncommitted" in decision.get("reason", "").lower()

    def test_desuka_period_suppresses_block(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """「ですか。」（句点止めの確認文）もクエスチョンマークと同様にブロックを抑制する。"""
        repo = make_dirty_repo(tmp_path)
        content = [{"type": "text", "text": "実装が完了しました。この案でよいですか。"}]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "desuka", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_split_entry_question_suppresses_block(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """同一message.idのエントリが分割された場合、前のエントリの質問テキストを検出する。

        テキストエントリの後にツール呼び出しのみのエントリが続く場合（競合状態:
        ツールエントリが最後にフラッシュされた状態でフックが発火）、
        前のエントリの質問テキストを確認してブロックを抑制する。
        """
        repo = make_dirty_repo(tmp_path)
        msg_id = "msg_test_split123"
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": msg_id,
                        "role": "assistant",
                        "content": [{"type": "text", "text": "実装が完了しました。コミットしますか？"}],
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": msg_id,
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "x", "name": "Bash", "input": {}}],
                    },
                },
                ensure_ascii=False,
            ),
        ]
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = _run(
            {"session_id": "split-entry", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_different_turn_tool_use_does_not_suppress_block(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """前のターンに質問があっても、最新ターンが質問でなければブロックする。

        異なるmessage.idを持つエントリは別ターンとして扱い、
        ユーザー応答が介在した後のテキスト＋ツール呼び出しエントリ（完了文言あり・質問なし）で
        ブロックを通過させる。
        """
        repo = make_dirty_repo(tmp_path)
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_old_turn",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "コミットしますか？"}],
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps({"type": "user", "message": {"role": "user", "content": "はい"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_new_turn",
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "作業が完了しました。"},
                            {"type": "tool_use", "id": "y", "name": "Bash", "input": {}},
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ]
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = _run(
            {"session_id": "diff-turn", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        assert "uncommitted" in decision.get("reason", "").lower()


class TestWaitingKeywordSuppressesBlock:
    """待機語・非同期待機ツールがある場合はblockを抑制する。

    テスト対象: 未コミット変更ブロック（uncommitted changes）および
    セッション振り返り提案ブロック（stop advice）の両方。
    `_stop_gate.is_real_session_end`がFalseを返すため、
    どちらのblockも発火しない。
    """

    def test_waiting_keyword_suppresses_uncommitted_block(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """待機語を含むアシスタントターンは完了文言があっても未コミット変更ブロックを抑制する。"""
        repo = make_dirty_repo(tmp_path)
        # 完了文言 + 待機語
        content = [{"type": "text", "text": "作業が完了しました。バックグラウンドで処理中です。完了を待ちます。"}]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "waiting-uncommitted", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_agent_tool_suppresses_uncommitted_block(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """最後のtool_useがAgentのターンは未コミット変更ブロックを抑制する。"""
        repo = make_dirty_repo(tmp_path)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "作業が完了しました。"},
            {"type": "tool_use", "id": "x", "name": "Agent", "input": {}},
        ]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "agent-tool-uncommitted", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_bash_background_suppresses_uncommitted_block(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """最後のtool_useがBash+run_in_background=Trueのターンは未コミット変更ブロックを抑制する。"""
        repo = make_dirty_repo(tmp_path)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "作業が完了しました。"},
            {
                "type": "tool_use",
                "id": "x",
                "name": "Bash",
                "input": {"command": "long_task.sh", "run_in_background": True},
            },
        ]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "bg-bash-uncommitted", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_waiting_keyword_suppresses_advice_block(self, tmp_path: pathlib.Path):
        """待機語を含むアシスタントターンはセッション振り返り提案ブロックを抑制する。"""
        content = [{"type": "text", "text": "作業が完了しました。バックグラウンドで処理中です。完了を待ちます。"}]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "waiting-advice", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_agent_tool_suppresses_advice_block(self, tmp_path: pathlib.Path):
        """最後のtool_useがAgentのターンはセッション振り返り提案ブロックを抑制する。"""
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "作業が完了しました。"},
            {"type": "tool_use", "id": "x", "name": "Agent", "input": {}},
        ]
        transcript = _write_transcript_with_assistant_last(tmp_path, content)
        result = _run(
            {"session_id": "agent-tool-advice", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestGitStatusDisplay:
    """approve時のgit status表示。"""

    def test_dirty_repo_shows_git_status(self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]):
        """未コミット変更がある場合、approve時にsystemMessageでgit statusを表示する。"""
        repo = make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, "no corrections")
        # ブロック上限を超過させてapproveパスに到達させる。
        _write_state(tmp_path, "gs-dirty", {"uncommitted_block_count": 2, "stop_advice_given": True})
        result = _run(
            {"session_id": "gs-dirty", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" in decision
        assert "git status" in decision["systemMessage"]
        assert "file.txt" in decision["systemMessage"]

    def test_clean_repo_no_system_message(
        self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """clean repoではsystemMessageを出力しない。"""
        repo = make_clean_repo(tmp_path)
        transcript = _write_transcript(tmp_path, "no corrections")
        result = _run(
            {"session_id": "gs-clean", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" not in decision

    def test_no_cwd_no_system_message(self, tmp_path: pathlib.Path):
        """cwd未指定時はsystemMessageを出力しない。"""
        transcript = _write_transcript(tmp_path, "no corrections")
        result = _run(
            {"session_id": "gs-nocwd", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" not in decision

    def test_untracked_only_no_system_message(
        self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """untrackedファイルのみの場合はsystemMessageを出力しない。"""
        repo = make_clean_repo(tmp_path)
        (repo / "untracked.txt").write_text("new file")
        transcript = _write_transcript(tmp_path, "no corrections")
        result = _run(
            {"session_id": "gs-untracked", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" not in decision
