"""agent-toolkit/scripts/_transcript.py のテスト。"""

import json
import pathlib

from _transcript import iter_latest_assistant_messages


def _write_transcript(tmp_path: pathlib.Path, lines: list[dict]) -> pathlib.Path:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )
    return transcript


def _assistant_entry(content: list[dict], *, msg_id: str = "msg_a", stop_reason: str = "end_turn") -> dict:
    return {
        "type": "assistant",
        "message": {"id": msg_id, "role": "assistant", "content": content, "stop_reason": stop_reason},
    }


def _user_entry(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


class TestIterLatestAssistantMessages:
    """直前ターン抽出の単体テスト。"""

    def test_extracts_single_assistant_turn(self, tmp_path: pathlib.Path):
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry("質問"),
                _assistant_entry([_text_block("回答")], msg_id="m1"),
            ],
        )
        messages = list(iter_latest_assistant_messages(str(transcript)))
        assert len(messages) == 1
        assert messages[0]["id"] == "m1"

    def test_merges_same_message_id(self, tmp_path: pathlib.Path):
        """同一message.idの複数エントリは1ターンに統合する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry("質問"),
                _assistant_entry([_text_block("part1")], msg_id="m1"),
                _assistant_entry([_text_block("part2")], msg_id="m1"),
            ],
        )
        messages = list(iter_latest_assistant_messages(str(transcript)))
        assert len(messages) == 2
        assert all(m["id"] == "m1" for m in messages)

    def test_excludes_sidechain(self, tmp_path: pathlib.Path):
        """isSidechain=trueのエントリは除外する。"""
        sidechain_entry = {
            "type": "assistant",
            "isSidechain": True,
            "message": {"id": "side", "role": "assistant", "content": [_text_block("sub")], "stop_reason": "end_turn"},
        }
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry("質問"),
                _assistant_entry([_text_block("回答")], msg_id="m1"),
                sidechain_entry,
            ],
        )
        messages = list(iter_latest_assistant_messages(str(transcript)))
        assert len(messages) == 1
        assert messages[0]["id"] == "m1"

    def test_breaks_on_non_assistant_after_assistant(self, tmp_path: pathlib.Path):
        """アシスタントエントリの間にuser等が介在するとターン境界とみなす。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _assistant_entry([_text_block("前ターン")], msg_id="m0"),
                _user_entry("中断"),
                _assistant_entry([_text_block("今ターン")], msg_id="m1"),
            ],
        )
        messages = list(iter_latest_assistant_messages(str(transcript)))
        assert len(messages) == 1
        assert messages[0]["id"] == "m1"

    def test_caps_at_three_entries(self, tmp_path: pathlib.Path):
        """同一ターンで4エントリ以上ある場合は最大3エントリで止まる。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry("質問"),
                _assistant_entry([_text_block("a")], msg_id="m1"),
                _assistant_entry([_text_block("b")], msg_id="m1"),
                _assistant_entry([_text_block("c")], msg_id="m1"),
                _assistant_entry([_text_block("d")], msg_id="m1"),
            ],
        )
        messages = list(iter_latest_assistant_messages(str(transcript)))
        assert len(messages) == 3

    def test_empty_transcript_path(self, tmp_path: pathlib.Path):
        """空文字列パスは空のイテレータを返す。"""
        del tmp_path  # 未使用
        messages = list(iter_latest_assistant_messages(""))
        assert not messages

    def test_nonexistent_path(self, tmp_path: pathlib.Path):
        """存在しないパスでも例外を送出せず空を返す。"""
        path = tmp_path / "missing.jsonl"
        messages = list(iter_latest_assistant_messages(str(path)))
        assert not messages

    def test_skips_invalid_json_lines(self, tmp_path: pathlib.Path):
        """JSON破損行は無視して走査を継続する。"""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    "broken json line",
                    json.dumps(_assistant_entry([_text_block("回答")], msg_id="m1"), ensure_ascii=False),
                ],
            )
            + "\n",
            encoding="utf-8",
        )
        messages = list(iter_latest_assistant_messages(str(transcript)))
        assert len(messages) == 1
        assert messages[0]["id"] == "m1"
