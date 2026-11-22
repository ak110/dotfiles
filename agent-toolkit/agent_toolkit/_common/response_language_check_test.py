"""agent-toolkit/agent_toolkit/_common/response_language_check.py のテスト。"""

import json
import pathlib

import pytest

from agent_toolkit._common.response_language_check import (
    CheckOutcome,
    check_text,
)

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent


def _make_mixed(japanese_count: int, english_word_count: int) -> str:
    japanese = "あ" * japanese_count
    words = " ".join(["word"] * english_word_count)
    if japanese and words:
        return japanese + " " + words
    return japanese or words


class TestCheckText:
    """check_text()の文字列入力に対する判定契約を検証する。"""

    @pytest.mark.parametrize("fenced", [False, True])
    def test_review_return_explanation_is_not_hidden_by_json(self, fenced: bool):
        text = json.dumps(
            [
                {
                    "retain": (
                        "reviewer/reviewee 기준의 소유 위치, review-table 8열 TSV, "
                        "file経由の入出力、対象成果物作成規範の読込順。"
                    )
                }
            ],
            ensure_ascii=False,
        )
        if fenced:
            text = "```json\n" + text + "\n```"
        assert check_text(text)[0] is CheckOutcome.WARN
        corrected = text.replace("기준의 소유 위치", "基準の所有位置").replace("8열", "8列")
        assert check_text(corrected)[0] is not CheckOutcome.WARN

    @pytest.mark.parametrize(
        "text",
        [
            "引用:「기준의 소유 위치」を日本語へ翻訳する。",
            "> 기준의 소유 위치\n入力を確認した。",
            "```text\n기준의 소유 위치\n```\n原文を保存した。",
            '{"input":"기준의 소유 위치", "analysis":"内部資料"}',
            "`기준의`は原文の識別子である。",
        ],
    )
    def test_foreign_material_is_preserved(self, text: str):
        assert check_text(text)[0] is not CheckOutcome.WARN

    def test_warns_for_discourse_marker(self):
        outcome, body = check_text("Now、調査を続ける。")
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_warns_below_japanese_word_ratio(self):
        outcome, body = check_text(_make_mixed(14, 35))
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_passes_japanese_text(self):
        outcome, body = check_text("あ" * 50)
        assert outcome is CheckOutcome.PASS
        assert body is None

    def test_skips_short_mixed_text(self):
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
        outcome, body = check_text(text)
        assert outcome is CheckOutcome.SKIP
        assert body is None

    @pytest.mark.parametrize(
        "identifier",
        [
            "/opt/project/agent-toolkit/hooks.py",
            "agent-toolkit/agent_toolkit/_hooks/stop_session.py",
            "C:\\Users\\aki\\dotfiles\\hook.py",
            "550e8400-e29b-41d4-a716-446655440000",
            "b4acde0123456789abcdef0123456789abcdef01",
            "pending_observation",
            "AUTO_RESUME_COMPLETED",
            "EXPLORE_FIRST_CONTENT",
            "Foo_Bar_Baz",
            "auto-resume-completed",
            "--no-fix --dry-run",
            "README.md CHANGELOG.md",
            "foo.bar.baz",
            "RESULT: PASS",
            "RESULT2: PASS3",
            "AUTO_RESUME_COMPLETED.",
        ],
    )
    def test_excludes_bare_machine_identifiers_from_word_ratio(self, identifier: str):
        outcome, body = check_text("確認: " + " ".join([identifier] * 8))
        assert outcome is not CheckOutcome.WARN
        assert body is None

    @pytest.mark.parametrize(
        "text",
        [
            "AUTO_RESUME_COMPLETED",
            "EXPLORE_FIRST_CONTENT",
            "Foo_Bar_Baz",
            "auto-resume-completed",
            "--no-fix --dry-run",
            "README.md CHANGELOG.md",
            "foo.bar.baz",
            "RESULT: PASS",
        ],
    )
    def test_fixed_return_values_are_not_english_prose(self, text: str):
        assert check_text(text)[0] is not CheckOutcome.WARN

    @pytest.mark.parametrize("text", ["The task completed successfully.", "Summary: done well"])
    def test_ordinary_english_prose_remains_detected(self, text: str):
        assert check_text(text)[0] is CheckOutcome.WARN

    def test_machine_identifier_exclusion_does_not_hide_english_prose(self):
        outcome, body = check_text("/opt/project/hook.py was updated and all validation checks completed successfully.")
        assert outcome is CheckOutcome.WARN
        assert body is not None

    @pytest.mark.parametrize(
        "text",
        [
            "status: checkpoint\ntype: review_round\nround: 2\nfindings_count: 3\nrequirement_spec_count: 0",
            "status: checkpoint\ntype: merge_request",
            (
                "統合完了\n"
                "merged_head: 0123456789abcdef0123456789abcdef01234567\n"
                'deferred_adopt_commits: [{"awi":"20260101-ccc.md","commit":"0123456789abcdef0123456789abcdef01234567"}]\n'
                "adopted: 20260101-aaa.md, 20260101-bbb.md\n"
                "rejected: なし"
            ),
            "condition_not_met",
            "status: completed",
            (
                "統合完了\n"
                "統合後のHEAD: 0123456789abcdef0123456789abcdef01234567\n"
                'adoptを延期したAWIとcommit: [{"awi":"20260101-ccc.md","commit":"0123456789abcdef0123456789abcdef01234567"}]\n'
                "adoptしたWI: 20260101-aaa.md, 20260101-bbb.md\n"
                "rejectしたWI: なし\n"
                '変更したエージェント向け文書: ["agent-toolkit/skills/atk-mode/roles/exec.subagent.md"]'
            ),
            "状態: completed\nレビューしたHEAD: abc1234\n未解決の指摘数: 0",
            "続行できない理由: 認可範囲の外にある既存不良が変更範囲の検証を妨げる",
        ],
    )
    def test_allows_specified_return_formats(self, text: str):
        outcome, body = check_text(text)
        assert outcome is not CheckOutcome.WARN
        assert body is None

    def test_warns_for_english_prose_after_machine_readable_lines(self):
        outcome, body = check_text("status: checkpoint\ntype: review_round\nI reviewed the plan and found three issues to fix.")
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_warns_for_capitalized_label_line(self):
        outcome, body = check_text("Summary: I fixed the hook and verified the tests.")
        assert outcome is CheckOutcome.WARN
        assert body is not None


class TestEnglishOnlyShortText:
    def test_warn_english_only_below_length_threshold(self):
        outcome, body = check_text("Done here.")
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_skip_english_single_word(self):
        outcome, body = check_text("Done.")
        assert outcome is CheckOutcome.SKIP
        assert body is None


class TestDiscourseMarker:
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
            "now、小文字表記でも検出する。",
        ],
    )
    def test_warn_when_plain_text_starts_with_marker(self, text: str):
        outcome, body = check_text(text)
        assert outcome is CheckOutcome.WARN
        assert body is not None

    def test_pass_product_name_form(self):
        outcome, body = check_text("Next.jsの構成を確認する。" + "あ" * 50)
        assert outcome is CheckOutcome.PASS
        assert body is None

    def test_pass_marker_inside_inline_code(self):
        outcome, body = check_text("`Now` は識別子である。" + "あ" * 50)
        assert outcome is CheckOutcome.PASS
        assert body is None

    def test_pass_marker_without_word_boundary(self):
        outcome, body = check_text("Nowここから確認する。" + "あ" * 50)
        assert outcome is CheckOutcome.PASS
        assert body is None
