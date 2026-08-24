"""agent-toolkit/scripts/_response_language_check.py のテスト。"""

import json
import pathlib
import re

import pytest
from _response_language_check import CheckOutcome, check_text, detailed_check
from _test_helpers import _write_transcript

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
_RULES_FILE = _SCRIPTS_DIR.parent / "rules" / "01-agent.md"

# hookメッセージが規範文書の見出しを鉤括弧付きで引用する形式。
_RULE_HEADING_REFERENCE_PATTERN = re.compile(r"01-agent\.md「([^」]+)」")


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


class TestCheckText:
    """check_text()の文字列入力に対する判定契約を検証する。"""

    def test_warns_for_english_only_text(self):
        """日本語文字を含まない英単語2語以上の文字列はWARNを返す。"""
        outcome, body = check_text("Done here.")
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_warns_for_discourse_marker(self):
        """先頭の英語談話標識を含む短い文字列はWARNを返す。"""
        outcome, body = check_text("Now、調査を続ける。")
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_warns_below_japanese_word_ratio(self):
        """語数比が閾値未満の文字列はWARNを返す。"""
        outcome, body = check_text(_make_mixed(14, 35))
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_passes_japanese_text(self):
        """日本語主体の十分な長さの文字列はPASSを返す。"""
        outcome, body = check_text("あ" * 50)
        assert outcome is CheckOutcome.PASS
        assert body is None

    def test_skips_short_mixed_text(self):
        """長さ下限未満の混在文字列はSKIPを返す。"""
        outcome, body = check_text("あ word")
        assert outcome is CheckOutcome.SKIP
        assert body is None

    @pytest.mark.parametrize(
        "text",
        [
            "```text\nDone here.\n```",
            "`Done here.`",
            "https://example.com/done",
        ],
    )
    def test_excludes_code_and_url_from_language_check(self, text: str):
        """コードとURLは記述言語の判定対象から除外する。"""
        outcome, body = check_text(text)
        assert outcome is CheckOutcome.SKIP
        assert body is None


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


class TestEnglishOnlyShortText:
    """日本語文字を含まない短文に対する判定の検証。"""

    def test_warn_english_only_below_length_threshold(self, tmp_path: pathlib.Path):
        """日本語文字0・英単語2語の短文は長さ下限を適用せずWARNを返す。"""
        path = _write_assistant_transcript(tmp_path, [_text_block("Done here.")])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_skip_english_single_word(self, tmp_path: pathlib.Path):
        """日本語文字0でも英単語1語だけの短文は従来どおりSKIPを返す。"""
        path = _write_assistant_transcript(tmp_path, [_text_block("Done.")])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.SKIP
        assert body is None


class TestDiscourseMarker:
    """地の文の先頭に置かれた英語の談話標識に対する判定の検証。"""

    @pytest.mark.parametrize(
        "text",
        [
            "Now、調査を続ける。",
            "Next 実装単位へ進む。",
            "Then: 検証結果を確認する。",
            "First 対象ファイルを読む。",
            "Also、既存テストを維持する。",
            "Finally、コミットする。",
            "Let's 実装へ着手する。",
            "Let me 実装へ着手する。",
            "So、方針を確定する。",
            "Okay、方針を確定する。",
            "OK、方針を確定する。",
            "now、小文字表記でも検出する。",
        ],
    )
    def test_warn_when_plain_text_starts_with_marker(self, tmp_path: pathlib.Path, text: str):
        """長さ下限未満でも先頭の談話標識でWARNを返す。"""
        path = _write_assistant_transcript(tmp_path, [_text_block(text)])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_pass_product_name_form(self, tmp_path: pathlib.Path):
        """標識の直後が`.`と英字の並び（製品名形式）である場合は該当しない。"""
        path = _write_assistant_transcript(tmp_path, [_text_block("Next.jsの構成を確認する。" + "あ" * 50)])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.PASS
        assert body is None

    def test_pass_marker_inside_inline_code(self, tmp_path: pathlib.Path):
        """バッククォート内の標識は地の文の先頭判定へ現れない。"""
        path = _write_assistant_transcript(tmp_path, [_text_block("`Now` は識別子である。" + "あ" * 50)])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.PASS
        assert body is None

    def test_pass_marker_without_word_boundary(self, tmp_path: pathlib.Path):
        """標識の直後に語境界が無い場合は該当しない。"""
        path = _write_assistant_transcript(tmp_path, [_text_block("Nowここから確認する。" + "あ" * 50)])
        outcome, body, _ = detailed_check(path)
        assert outcome is CheckOutcome.PASS
        assert body is None


class TestRuleHeadingReference:
    """hookメッセージが引用する規範文書の見出しが実在することの検証。"""

    def test_quoted_headings_exist_in_rules(self):
        """scripts配下の引用名がすべて01-agent.mdの見出しに実在する。"""
        headings = {
            line.lstrip("#").strip() for line in _RULES_FILE.read_text(encoding="utf-8").splitlines() if line.startswith("#")
        }
        quoted: dict[str, list[str]] = {}
        for script in sorted(_SCRIPTS_DIR.glob("*.py")):
            for name in _RULE_HEADING_REFERENCE_PATTERN.findall(script.read_text(encoding="utf-8")):
                quoted.setdefault(name, []).append(script.name)
        assert quoted, "走査対象に規範文書の見出し引用が1件も無い"
        missing = {name: files for name, files in quoted.items() if name not in headings}
        assert not missing, f"01-agent.mdに実在しない見出しを引用している: {missing}"
