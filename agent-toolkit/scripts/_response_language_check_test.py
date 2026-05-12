"""agent-toolkit/scripts/_response_language_check.py のテスト。"""

import json
import pathlib

import pytest
from _response_language_check import check


def _write_transcript(tmp_path: pathlib.Path, content_blocks: list[dict], *, is_sidechain: bool = False) -> str:
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
    path = tmp_path / "transcript.jsonl"
    path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path)


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _make_mixed(japanese_count: int, english_count: int) -> str:
    """指定数の日本語ひらがな・英字を結合した文字列を返す。"""
    return "あ" * japanese_count + "A" * english_count


class TestRatioBoundary:
    """日本語文字比率の境界値テスト。比率 = 日本語文字数 / (日本語文字数 + 英字数)。"""

    @pytest.mark.parametrize(
        ("japanese_count", "english_count", "expect_warn"),
        [
            # ratio=0.0: 英字のみ → 警告
            (0, 50, True),
            # ratio=0.28 (<0.30): 警告
            (14, 36, True),
            # ratio=0.30 ちょうど: 警告なし
            (15, 35, False),
            # ratio=0.32 (>0.30): 警告なし
            (16, 34, False),
            # ratio=1.0: 日本語のみ → 警告なし
            (50, 0, False),
        ],
    )
    def test_ratio(self, tmp_path: pathlib.Path, japanese_count: int, english_count: int, expect_warn: bool):
        text = _make_mixed(japanese_count, english_count)
        path = _write_transcript(tmp_path, [_text_block(text)])
        result = check(path)
        assert (result is not None) is expect_warn


class TestPlainTextLengthBoundary:
    """プレーンテキスト長の境界値テスト（下限50）。"""

    @pytest.mark.parametrize(
        ("length", "expect_warn"),
        [
            # 49文字（下限未満）: 検査スキップ → None
            (49, False),
            # 50文字（下限）: 検査実行 → 英字のみで警告
            (50, True),
            # 51文字（下限超過）: 検査実行 → 英字のみで警告
            (51, True),
        ],
    )
    def test_length(self, tmp_path: pathlib.Path, length: int, expect_warn: bool):
        text = "A" * length
        path = _write_transcript(tmp_path, [_text_block(text)])
        result = check(path)
        assert (result is not None) is expect_warn


class TestMasking:
    """コードブロック・インラインコード・URLは比率分母から除外する。"""

    def test_fenced_code_block_only_english(self, tmp_path: pathlib.Path):
        """フェンス付きコードブロック内が英字でも、地の文が日本語なら警告しない。"""
        # フェンス内: 英字を多数含む。フェンス外: 日本語のみ50文字以上。
        fenced = "```python\nprint('hello world English only here')\n```\n"
        text = "これは日本語の文章です。コード例を示します。日本語で続けて記述します。\n" + fenced
        path = _write_transcript(tmp_path, [_text_block(text)])
        assert check(path) is None

    def test_inline_code_only_english(self, tmp_path: pathlib.Path):
        """インラインコード内の英字は分母から除外する。"""
        text = "これは日本語の説明です。コマンドは`grep -rn pattern path/to/files`で実行します。さらに日本語で続けます。"
        path = _write_transcript(tmp_path, [_text_block(text)])
        assert check(path) is None

    def test_url_only_english(self, tmp_path: pathlib.Path):
        """URL文字列はscript系文字計算から除外する。"""
        text = (
            "詳細は https://example.com/very/long/path/to/some/document/page.html を参照してください。日本語で説明を続けます。"
        )
        path = _write_transcript(tmp_path, [_text_block(text)])
        assert check(path) is None


class TestSpecialInputs:
    """空応答・サブエージェントのみ・transcript不在等の異常系。"""

    def test_empty_response(self, tmp_path: pathlib.Path):
        """テキストブロックが空でも例外を送出せずにNoneを返す。"""
        path = _write_transcript(tmp_path, [])
        assert check(path) is None

    def test_sidechain_only(self, tmp_path: pathlib.Path):
        """サブエージェント（isSidechain=true）の応答のみなら検査対象外。"""
        text = "A" * 100  # 英字のみ100文字（メイン応答なら警告対象）
        path = _write_transcript(tmp_path, [_text_block(text)], is_sidechain=True)
        assert check(path) is None

    def test_empty_transcript_path(self):
        """空文字列パスはNoneを返す。"""
        assert check("") is None

    def test_nonexistent_path(self, tmp_path: pathlib.Path):
        """存在しないパスでもNoneを返す。"""
        assert check(str(tmp_path / "missing.jsonl")) is None
