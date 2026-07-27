"""agent-toolkit/scripts/_response_language_check.py のテスト。"""

import json
import pathlib

import pytest
from _response_language_check import CheckOutcome, check, detailed_check

# テスト用の他言語文字検体（コードポイントのエスケープ表記）。
# テストファイル内のdocstring・コメントが日本語であるため、
# そのままの文字を埋め込むとテストファイルの編集が言語判定により遮断される。
# 各テストはこれらの定数の繰り返しで検体を組み立てる。
_HANGUL_SAMPLE = "\uac00"  # U+AC00: ハングル音節文字
_CYRILLIC_SAMPLE = "\u0400"  # U+0400: キリル文字
_HALFWIDTH_HANGUL_SAMPLE = "\uffa0"  # U+FFA0: 半角ハングル


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


class TestRatioBoundary:
    """語数比の境界値テスト。比率 = 日本語文字数 / (日本語文字数 + 英単語数)。"""

    @pytest.mark.parametrize(
        ("japanese_count", "english_word_count", "expect_warn"),
        [
            # ratio=0.00: 英単語のみ → 警告
            (0, 20, True),
            # ratio=0.2857 (<0.30): 警告
            (14, 35, True),
            # ratio=0.30 ちょうど: 警告なし
            (15, 35, False),
            # ratio=0.40 (>0.30): 警告なし
            (20, 30, False),
            # ratio=1.00: 日本語のみ → 警告なし
            (50, 0, False),
        ],
    )
    def test_ratio(self, tmp_path: pathlib.Path, japanese_count: int, english_word_count: int, expect_warn: bool):
        text = _make_mixed(japanese_count, english_word_count)
        path = _write_transcript(tmp_path, [_text_block(text)])
        result = check(path)
        assert (result is not None) is expect_warn
        if expect_warn:
            assert result is not None
            assert "英語主体" in result


class TestPlainTextLengthBoundary:
    """プレーンテキスト長の境界値テスト（下限50）。"""

    @pytest.mark.parametrize(
        ("length", "expect_warn"),
        [
            # 49文字（下限未満）: 検査スキップ → None
            (49, False),
            # 50文字（下限）: 検査実行 → 英単語のみ（語数比0.0）で警告
            (50, True),
            # 51文字（下限超過）: 検査実行 → 英単語のみ（語数比0.0）で警告
            (51, True),
        ],
    )
    def test_length(self, tmp_path: pathlib.Path, length: int, expect_warn: bool):
        text = "A" * length
        path = _write_transcript(tmp_path, [_text_block(text)])
        result = check(path)
        assert (result is not None) is expect_warn


class TestMasking:
    """コードブロック・インラインコード・URLは地の文から除外する。"""

    def test_fenced_code_block_only_english(self, tmp_path: pathlib.Path):
        """フェンス付きコードブロック内が英字でも、地の文が日本語なら警告しない。"""
        # フェンス内: 英字を多数含む。フェンス外: 日本語のみ50文字以上。
        fenced = "```python\nprint('hello world English only here')\n```\n"
        text = "これは日本語の文章です。コード例を示します。日本語で続けて記述します。\n" + fenced
        path = _write_transcript(tmp_path, [_text_block(text)])
        assert check(path) is None

    def test_inline_code_only_english(self, tmp_path: pathlib.Path):
        """インラインコード内の英字は地の文から除外する。"""
        text = "これは日本語の説明です。コマンドは`grep -rn pattern path/to/files`で実行します。さらに日本語で続けます。"
        path = _write_transcript(tmp_path, [_text_block(text)])
        assert check(path) is None

    def test_url_only_english(self, tmp_path: pathlib.Path):
        """URL文字列は地の文から除外する。"""
        text = (
            "詳細は https://example.com/very/long/path/to/some/document/page.html を参照してください。日本語で説明を続けます。"
        )
        path = _write_transcript(tmp_path, [_text_block(text)])
        assert check(path) is None

    def test_bare_english_identifiers_do_not_trigger(self, tmp_path: pathlib.Path):
        """裸の英識別子が多数並んでも、語数比で日本語が優勢なら誤発火しない。

        各識別子は連続英字列1語として数えるため、文字数では英字が嵩んでも
        語数比では日本語が分子を占める。文字数比方式での誤発火を解消する回帰ケース。
        """
        text = (
            "クラス構成を順に確認する。"
            " ResponseLanguageChecker TranscriptReader PreToolUseHandler"
            " PostToolUseHandler SessionStateManager JapaneseRatioCalculator"
            " EnglishWordTokenizer MaskedPlainTextExtractor NonAsciiCharacterMatcher."
        )
        path = _write_transcript(tmp_path, [_text_block(text)])
        assert check(path) is None


class TestSpecialInputs:
    """空応答・サブエージェントのみ・記号のみ・transcript不在等の異常系。"""

    def test_empty_response(self, tmp_path: pathlib.Path):
        """テキストブロックが空でも例外を送出せずにNoneを返す。"""
        path = _write_transcript(tmp_path, [])
        assert check(path) is None

    def test_symbols_only(self, tmp_path: pathlib.Path):
        """日本語も英単語も無い記号・数字列は分母ゼロのため判定対象外としてNoneを返す。"""
        text = "12345 67890 !@#$% ^&*() 13579 24680 +++++ ===== ///// ..... ----- &&&&&"
        path = _write_transcript(tmp_path, [_text_block(text)])
        assert check(path) is None

    def test_sidechain_only(self, tmp_path: pathlib.Path):
        """サブエージェント（isSidechain=true）の応答のみなら検査対象外。"""
        text = "A" * 100  # ASCIIのみ100文字（メイン応答なら警告対象）
        path = _write_transcript(tmp_path, [_text_block(text)], is_sidechain=True)
        assert check(path) is None

    def test_empty_transcript_path(self):
        """空文字列パスはNoneを返す。"""
        assert check("") is None

    def test_nonexistent_path(self, tmp_path: pathlib.Path):
        """存在しないパスでもNoneを返す。"""
        assert check(str(tmp_path / "missing.jsonl")) is None


class TestNonJapaneseCharacterTypes:
    """日本語以外の非ASCII文字が日本語と判定されないことを検証する。"""

    def test_hangul_returns_none_then_warn(self, tmp_path: pathlib.Path) -> None:
        """ハングル40字＋英単語20語で比率が閾値をまたぐ。

        是正前（U+0000-U+007Fの範囲外全て）：
          日本語文字数=40（ハングル）、英単語数=20 → 比率40/60≈0.67 > 0.30 → PASS → None
        是正後（日本語範囲のみ）：
          日本語文字数=0（ハングル非検出）、英単語数=20 → 比率0/20=0 < 0.30 → WARN → 警告本文
        """
        # ハングル（U+AC00-U+D7AF）40字 + 英単語20語
        text = _HANGUL_SAMPLE * 40 + " " + " ".join(["word"] * 20)
        path = _write_transcript(tmp_path, [_text_block(text)])
        # 是正後の実装でハングルが検出されないため警告を返す
        assert check(path) is not None

    def test_cyrillic_returns_none_then_warn(self, tmp_path: pathlib.Path) -> None:
        """キリル40字＋英単語20語で比率が閾値をまたぐ。

        是正前（U+0000-U+007Fの範囲外全て）：
          日本語文字数=40（キリル）、英単語数=20 → 比率40/60≈0.67 > 0.30 → PASS → None
        是正後（日本語範囲のみ）：
          日本語文字数=0（キリル非検出）、英単語数=20 → 比率0/20=0 < 0.30 → WARN → 警告本文
        """
        # キリル（U+0400-U+04FF）40字 + 英単語20語
        text = _CYRILLIC_SAMPLE * 40 + " " + " ".join(["word"] * 20)
        path = _write_transcript(tmp_path, [_text_block(text)])
        # 是正後の実装でキリルが検出されないため警告を返す
        assert check(path) is not None

    def test_halfwidth_hangul_returns_none_then_warn(self, tmp_path: pathlib.Path) -> None:
        """半角ハングル40字＋英単語20語で比率が閾値をまたぐ。

        是正前（U+0000-U+007Fの範囲外全て）：
          日本語文字数=40（半角ハングル）、英単語数=20 → 比率40/60≈0.67 > 0.30 → PASS → None
        是正後（日本語範囲U+3000-U+FF9Fのみ）：
          日本語文字数=0（半角ハングル U+FFA0-U+FFDCは範囲外）、英単語数=20 → 比率0/20=0 < 0.30 → WARN
        """
        # 半角ハングル（U+FFA0-U+FFDC）40字 + 英単語20語
        text = _HALFWIDTH_HANGUL_SAMPLE * 40 + " " + " ".join(["word"] * 20)
        path = _write_transcript(tmp_path, [_text_block(text)])
        # 是正後の実装で半角ハングルが検出されないため警告を返す
        assert check(path) is not None

    def test_japanese_characters_detected_correctly(self, tmp_path: pathlib.Path) -> None:
        """ひらがな・カタカナ・漢字・全角記号・半角カナ40字＋英単語20語で日本語が優勢。

        是正後の実装で各日本語文字が正しく検出され、比率が閾値以上（0.30以上）となる。
        範囲を狭めすぎた場合の退行防止テスト。
        """
        # 日本語文字（U+3000-U+FF9Fの各範囲）40字 + 英単語20語
        # あいうえお(5) + カキクケコ(5) + 漢字です(4) + 　、(2) + ｡｢(2) = 18字 * 2 + word*20
        text = ("あいうえお" + "カキクケコ" + "漢字です" + "　、" + "｡｢") * 2 + " " + " ".join(["word"] * 20)
        path = _write_transcript(tmp_path, [_text_block(text)])
        # 日本語が優勢（ratio ≥ 0.30）なため警告なし（None）
        assert check(path) is None


class TestDetailedCheck:
    """detailed_check()のOutcome・message ID返却の検証。"""

    def test_warn_with_english_text(self, tmp_path: pathlib.Path):
        """英語テキスト50文字以上でWARNを返す。"""
        path = _write_transcript(tmp_path, [_text_block("A" * 50)])
        outcome, body, msg_id = detailed_check(path)
        assert outcome is CheckOutcome.WARN
        assert body is not None
        assert "英語主体" in body
        assert msg_id == "m1"

    def test_pass_with_japanese_text(self, tmp_path: pathlib.Path):
        """日本語テキスト50文字以上で比率≧0.30のときPASSを返す。"""
        path = _write_transcript(tmp_path, [_text_block("あ" * 50)])
        outcome, body, msg_id = detailed_check(path)
        assert outcome is CheckOutcome.PASS
        assert body is None
        assert msg_id == "m1"

    def test_skip_short_text(self, tmp_path: pathlib.Path):
        """49文字のテキストはSKIPを返す。"""
        path = _write_transcript(tmp_path, [_text_block("A" * 49)])
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
        path = _write_transcript(tmp_path, [_text_block("A" * 100)], is_sidechain=True)
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.SKIP
        assert body is None

    def test_boundary_ratio_0_2857_warns(self, tmp_path: pathlib.Path):
        """語数比0.2857 (<0.30) でWARNを返す。"""
        text = _make_mixed(14, 35)
        path = _write_transcript(tmp_path, [_text_block(text)])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_boundary_ratio_0_30_passes(self, tmp_path: pathlib.Path):
        """語数比0.30ちょうどでPASSを返す。"""
        text = _make_mixed(15, 35)
        path = _write_transcript(tmp_path, [_text_block(text)])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.PASS
        assert body is None

    def test_boundary_ratio_0_40_passes(self, tmp_path: pathlib.Path):
        """語数比0.40 (>0.30) でPASSを返す。"""
        text = _make_mixed(20, 30)
        path = _write_transcript(tmp_path, [_text_block(text)])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.PASS
        assert body is None

    def test_boundary_text_length_50(self, tmp_path: pathlib.Path):
        """テキスト長50文字で検査が実行される。"""
        path = _write_transcript(tmp_path, [_text_block("A" * 50)])
        outcome, _, _ = detailed_check(path)
        assert outcome is CheckOutcome.WARN

    def test_boundary_text_length_51(self, tmp_path: pathlib.Path):
        """テキスト長51文字で検査が実行される。"""
        path = _write_transcript(tmp_path, [_text_block("A" * 51)])
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
