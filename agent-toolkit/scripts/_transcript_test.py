"""agent-toolkit/scripts/_transcript.py のテスト。"""

import json
import pathlib

from _transcript import assistant_text, iter_latest_assistant_messages, latest_main_assistant_entry


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


def _system_entry(subtype: str = "turn_duration") -> dict:
    return {"type": "system", "subtype": subtype}


def _api_error_entry(text: str) -> dict:
    return {
        "type": "assistant",
        "isApiErrorMessage": True,
        "message": {"id": "err", "role": "assistant", "content": [_text_block(text)], "stop_reason": None},
    }


class TestLatestMainAssistantEntry:
    """末尾entry取得の単体テスト。"""

    def test_returns_entry_with_top_level_flag(self, tmp_path: pathlib.Path):
        """APIエラーエントリの後にsystemエントリが続いてもentry全体を返し、トップレベルフラグを保持する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry("質問"),
                _api_error_entry("The model's tool call could not be parsed (retry also failed)."),
                _system_entry(),
            ],
        )
        entry = latest_main_assistant_entry(str(transcript))
        assert entry is not None
        assert entry["isApiErrorMessage"] is True

    def test_excludes_sidechain(self, tmp_path: pathlib.Path):
        """末尾がsidechainのassistantなら遡って非sidechainのassistantを返す。"""
        sidechain_entry = {
            "type": "assistant",
            "isSidechain": True,
            "message": {"id": "side", "role": "assistant", "content": [_text_block("sub")], "stop_reason": "end_turn"},
        }
        transcript = _write_transcript(
            tmp_path,
            [
                _assistant_entry([_text_block("回答")], msg_id="m1"),
                sidechain_entry,
            ],
        )
        entry = latest_main_assistant_entry(str(transcript))
        assert entry is not None
        assert entry["message"]["id"] == "m1"

    def test_returns_none_without_assistant(self, tmp_path: pathlib.Path):
        """assistantエントリが無ければNoneを返す。"""
        transcript = _write_transcript(tmp_path, [_user_entry("質問")])
        assert latest_main_assistant_entry(str(transcript)) is None

    def test_returns_none_for_missing_path(self, tmp_path: pathlib.Path):
        """空文字列パス・存在しないパスはNoneを返す。"""
        assert latest_main_assistant_entry("") is None
        assert latest_main_assistant_entry(str(tmp_path / "missing.jsonl")) is None


class TestAssistantText:
    """message dictからのテキスト抽出の単体テスト。"""

    def test_concatenates_text_blocks(self):
        message = {"content": [_text_block("a"), {"type": "tool_use", "name": "x"}, _text_block("b")]}
        assert assistant_text(message) == "ab"

    def test_returns_string_content_as_is(self):
        assert assistant_text({"content": "plain"}) == "plain"

    def test_returns_empty_for_non_dict(self):
        assert assistant_text(None) == ""
        assert assistant_text([]) == ""
