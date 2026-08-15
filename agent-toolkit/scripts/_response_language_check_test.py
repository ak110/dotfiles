"""agent-toolkit/scripts/_response_language_check.py のテスト。"""

import json
import pathlib

from _response_language_check import CheckOutcome, detailed_check
from conftest import _write_transcript


def _write_assistant_transcript(
    tmp_path: pathlib.Path,
    content_blocks: list[dict],
    *,
    is_sidechain: bool = False,
) -> str:
    """単一のassistantエントリをJSONLとして書き込みパスを返す。"""
    entry: dict = {
        "type": "assistant",
        "message": {
            "id": "m1",
            "role": "assistant",
            "content": content_blocks,
            "stop_reason": "end_turn",
        },
    }
    if is_sidechain:
        entry["isSidechain"] = True
    return str(_write_transcript(tmp_path, [entry]))


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _make_mixed(japanese_count: int, english_word_count: int) -> str:
    """指定数の日本語文字（ひらがな）とスペース区切りの英単語を結合した文字列を返す。

    語数比が japanese_count / (japanese_count + english_word_count) となる文字列を生成する。
    英単語は連続英字列1つを1語として数えるため、スペースで区切って独立させる。
    """
    japanese = "あ" * japanese_count
    words = " ".join(["word"] * english_word_count)
    if japanese and words:
        return japanese + " " + words
    return japanese or words


class TestDetailedCheck:
    """detailed_check()のOutcome・message ID返却の検証。"""

    def test_warn_with_english_text(self, tmp_path: pathlib.Path):
        """英語テキスト50文字以上でWARNを返す。"""
        path = _write_assistant_transcript(tmp_path, [_text_block("A" * 50)])
        outcome, body, msg_id = detailed_check(path)
        assert outcome is CheckOutcome.WARN
        assert body is not None
        assert "英語主体" in body
        assert msg_id == "m1"

    def test_pass_with_japanese_text(self, tmp_path: pathlib.Path):
        """日本語テキスト50文字以上で比率≧0.30のときPASSを返す。"""
        path = _write_assistant_transcript(tmp_path, [_text_block("あ" * 50)])
        outcome, body, msg_id = detailed_check(path)
        assert outcome is CheckOutcome.PASS
        assert body is None
        assert msg_id == "m1"

    def test_skip_short_text(self, tmp_path: pathlib.Path):
        """49文字のテキストはSKIPを返す。"""
        path = _write_assistant_transcript(tmp_path, [_text_block("A" * 49)])
        outcome, body, msg_id = detailed_check(path)
        assert outcome is CheckOutcome.SKIP
        assert body is None
        assert msg_id == "m1"

    def test_skip_empty_transcript_path(self):
        """空文字列パスはSKIPを返す。"""
        outcome, body, msg_id = detailed_check("")
        assert outcome is CheckOutcome.SKIP
        assert body is None
        assert msg_id == ""

    def test_skip_nonexistent_path(self, tmp_path: pathlib.Path):
        """存在しないパスはSKIPを返す。"""
        outcome, body, _ = detailed_check(str(tmp_path / "missing.jsonl"))
        assert outcome is CheckOutcome.SKIP
        assert body is None

    def test_skip_sidechain(self, tmp_path: pathlib.Path):
        """サブエージェント応答はSKIPを返す（テキストが空になるため）。"""
        path = _write_assistant_transcript(tmp_path, [_text_block("A" * 100)], is_sidechain=True)
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.SKIP
        assert body is None

    def test_skip_when_latest_turn_ends_with_api_error(self, tmp_path: pathlib.Path):
        """APIエラー終端時は以前の英語assistant本文を言語判定へ含めない。"""
        transcript = _write_transcript(
            tmp_path,
            [
                {
                    "type": "assistant",
                    "message": {
                        "id": "previous",
                        "role": "assistant",
                        "content": [_text_block("A" * 100)],
                        "stop_reason": "end_turn",
                    },
                },
                {"type": "user", "message": {"role": "user", "content": "再開"}},
                {
                    "type": "assistant",
                    "isApiErrorMessage": True,
                    "message": {
                        "id": "api-error",
                        "role": "assistant",
                        "content": [_text_block("The API request failed.")],
                        "stop_reason": None,
                    },
                },
                {"type": "system", "subtype": "turn_duration"},
            ],
        )

        outcome, body, msg_id = detailed_check(str(transcript))

        assert outcome is CheckOutcome.SKIP
        assert body is None
        assert msg_id == ""

    def test_boundary_ratio_0_2857_warns(self, tmp_path: pathlib.Path):
        """語数比0.2857 (<0.30) でWARNを返す。"""
        text = _make_mixed(14, 35)
        path = _write_assistant_transcript(tmp_path, [_text_block(text)])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_boundary_ratio_0_30_passes(self, tmp_path: pathlib.Path):
        """語数比0.30ちょうどでPASSを返す。"""
        text = _make_mixed(15, 35)
        path = _write_assistant_transcript(tmp_path, [_text_block(text)])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.PASS
        assert body is None

    def test_boundary_ratio_0_40_passes(self, tmp_path: pathlib.Path):
        """語数比0.40 (>0.30) でPASSを返す。"""
        text = _make_mixed(20, 30)
        path = _write_assistant_transcript(tmp_path, [_text_block(text)])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.PASS
        assert body is None

    def test_boundary_text_length_50(self, tmp_path: pathlib.Path):
        """テキスト長50文字で検査が実行される。"""
        path = _write_assistant_transcript(tmp_path, [_text_block("A" * 50)])
        outcome, _, _ = detailed_check(path)
        assert outcome is CheckOutcome.WARN

    def test_boundary_text_length_51(self, tmp_path: pathlib.Path):
        """テキスト長51文字で検査が実行される。"""
        path = _write_assistant_transcript(tmp_path, [_text_block("A" * 51)])
        outcome, _, _ = detailed_check(path)
        assert outcome is CheckOutcome.WARN

    def test_message_id_from_transcript(self, tmp_path: pathlib.Path):
        """transcriptから正しいmessage IDが取得される。"""
        entry = {
            "type": "assistant",
            "message": {
                "id": "msg_custom_123",
                "role": "assistant",
                "content": [{"type": "text", "text": "A" * 100}],
                "stop_reason": "end_turn",
            },
        }
        path = tmp_path / "transcript.jsonl"
        path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
        outcome, _, msg_id = detailed_check(str(path))
        assert outcome is CheckOutcome.WARN
        assert msg_id == "msg_custom_123"
