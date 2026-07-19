"""agent-toolkit/scripts/_scope_escalation.py のテスト。

`_match_scope_escalation`と`_SCOPE_ESCALATION_PHRASES`辞書のカテゴリ分類を、
隔離フィクスチャ`agent-toolkit/skills/agent-standards/references/_scope_escalation_test_inputs.txt`
から動的に読み込んだ最小マッチ入力で確認する。

フィクスチャは`pretooluse_test.py`・`stop_advisor_test.py`と共有する。
検出語そのものをテストコード本文へ転記しない
（`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節）。
"""

import typing

import pytest
from _scope_escalation import (
    _STOP_FOCUS_CATEGORIES,
    _apply_category_exclusions,
    _match_scope_escalation,
    has_inline_choice_offer,
    is_empty_completion_report,
)
from _scope_escalation_test_helpers import load_scope_escalation_inputs

_INPUTS = load_scope_escalation_inputs()


def _category_of(text: str, **kwargs: typing.Any) -> str | None:
    """`_match_scope_escalation`の戻り値タプルからカテゴリのみを取り出す。"""
    result = _match_scope_escalation(text, **kwargs)
    return result[0] if result is not None else None


class TestMatchScopeEscalation:
    """`_match_scope_escalation`のカテゴリ分類。"""

    @pytest.mark.parametrize(("text", "category"), _INPUTS)
    def test_fixture_inputs_match_expected_category(self, text: str, category: str):
        """隔離フィクスチャの各入力が期待カテゴリへ分類されることを確認する。"""
        assert _category_of(text) == category

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "通常の進捗を報告する",
            "対象ファイル一覧を確認する",
            "テスト実行結果を報告する",
            "Waiting for user input on the next question.",
            "background agent is running.",
            "バックグラウンドで処理する。その後、追跡中の状態を確認する。",
            "完了報告を提出してから確認する。",
            "gh run listで一覧を確認する。",
            "複雑度に応じてモデルを選択する。",
        ],
    )
    def test_non_matching_text_returns_none(self, text: str):
        """非該当テキストは`None`を返す。"""
        assert _match_scope_escalation(text) is None

    def test_stop_focus_categories_are_process_omission_only(self):
        """`_STOP_FOCUS_CATEGORIES`は`process-omission`単独で固定する。

        Stop経路で自由文脈テキストへ照合するため、他カテゴリの日常的な報告文言との
        誤検出を回避する意図の定数。`fabricated-metrics`はPreToolUse経路のみで検出する。
        将来の追加時は本テストとカテゴリ定義を同時に見直す。
        """
        assert frozenset({"process-omission"}) == _STOP_FOCUS_CATEGORIES

    def test_categories_filter_limits_matches(self):
        """`categories`引数で指定したカテゴリのみを照合対象とする。

        Stop経路で他カテゴリ（`priority-consult`・`next-cycle-defer`・`approach-confirm`等）が
        照合対象から除外されることを、各カテゴリの代表フレーズを渡して確認する。
        """
        by_category: dict[str, str] = {}
        for text, category in _INPUTS:
            by_category.setdefault(category, text)
        # `_STOP_FOCUS_CATEGORIES`のカテゴリは検出される。
        for target in _STOP_FOCUS_CATEGORIES:
            phrase = by_category.get(target)
            if phrase is None:
                continue
            assert _category_of(phrase, categories=_STOP_FOCUS_CATEGORIES) == target
        # `_STOP_FOCUS_CATEGORIES`に含まれないカテゴリは検出されない。
        for cat, phrase in by_category.items():
            if cat in _STOP_FOCUS_CATEGORIES:
                continue
            assert _match_scope_escalation(phrase, categories=_STOP_FOCUS_CATEGORIES) is None

    def test_categories_none_matches_all(self):
        """`categories=None`（既定）は全カテゴリを照合対象とする。"""
        for text, category in _INPUTS:
            assert _category_of(text, categories=None) == category

    def test_completion_difficulty_matches_single_session(self):
        """`本セッションのリソースでは完遂困難`は`single-session`を返す。"""
        assert _category_of("本セッションのリソースでは完遂困難と判断する。") == "single-session"

    def test_scale_difficulty_matches_single_session(self):
        """`規模的に本セッションでは困難`は`single-session`を返す。"""
        assert _category_of("規模的に本セッションでは困難のため一度に処理できない。") == "single-session"

    def test_general_completion_difficulty_not_matched(self):
        """`本|この|単一`セッション接頭を伴わない一般的な困難表現は`single-session`と判定しない。"""
        assert _match_scope_escalation("実装完遂は技術的に困難だが対応する。") is None

    def test_deferral_kettei_single_verb_captured(self):
        """単独動詞（決定）で完結する先送り文の捕捉確認（fb01反映）"""
        assert _category_of("実装時に対応方針を決定する。") == "plan-deferral-onset"

    def test_deferral_wc_l_kettei_captured(self):
        """実測値を根拠に添える先送り文の捕捉確認（fb01原理由の事例文）"""
        assert _category_of("実装時のwc -l実測値に基づき決定する。") == "plan-deferral-onset"

    def test_has_inline_choice_offer_detects_numbered_list(self):
        """`選択肢:`直後の番号付きリストは選択肢提示として検出する。"""
        text = "続行方針を選んでください。選択肢:\n1. 現行維持\n2. 次回持ち越し"
        assert has_inline_choice_offer(text) is True

    def test_has_inline_choice_offer_normal_text(self):
        """`選択肢:`を含まない通常の進捗記述は選択肢提示として検出しない。"""
        assert has_inline_choice_offer("処理を続行する。") is False

    def test_has_inline_choice_offer_fullwidth_number(self):
        """全角番号（`１`・`２`）で始まる選択肢提示も検出する。"""
        text = "選択肢:\n１. 現行維持\n２. 次回持ち越し"
        assert has_inline_choice_offer(text) is True

    @pytest.mark.parametrize("value", [None, 123, ["str"]])
    def test_non_string_input_returns_none(self, value: object):
        """非文字列入力は`None`を返す（fail-safe挙動）。"""
        # `_match_scope_escalation`は`isinstance(text, str)`で防御し、
        # 非文字列渡しでも例外を送出しない挙動を保証する。
        # 型チェッカー全種の引数型検査を回避するため`typing.cast`で`str`扱いに変換する。
        assert _match_scope_escalation(typing.cast("str", value)) is None

    def test_priority_consult_phrase_inside_zenkaku_kakko_not_matched(self):
        """全角鍵括弧内へ他ファイル節名を転記した文字列は検出されない。"""
        text = "計画ファイル本文の「スコープ相談節」を確認してから実装する。"
        assert _match_scope_escalation(text) is None

    def test_priority_consult_phrase_outside_zenkaku_kakko_matched(self):
        """全角鍵括弧の外側にpriority-consultパターン相当語彙がある場合は検出される。"""
        text = "優先順位について相談してから着手する。"
        assert _category_of(text) == "priority-consult"

    def test_priority_consult_phrase_inside_and_outside_kakko_matches_outside_only(self):
        """全角鍵括弧の内側と外側の両方に該当語彙がある場合、外側のみが検出対象となる。"""
        text = "計画ファイル本文の「スコープ相談節」を参照しつつ、優先順位について相談してから着手する。"
        assert _category_of(text) == "priority-consult"

    def test_match_scope_escalation_returns_tuple_on_hit(self):
        """検出時は`(category, matched_phrase)`のタプルを返す。"""
        result = _match_scope_escalation("優先順位について相談してから着手する。")
        assert result is not None
        category, matched = result
        assert category == "priority-consult"
        assert matched

    def test_priority_consult_phrase_inside_backtick_not_matched(self):
        """バッククォート囲み内へ他ファイル節名を転記した文字列は検出されない。"""
        text = "計画ファイル本文の`スコープ相談節`を確認してから実装する。"
        assert _match_scope_escalation(text) is None

    def test_priority_consult_phrase_inside_and_outside_backtick_matches_outside_only(self):
        """バッククォート囲みの内側と外側の両方に該当語彙がある場合、外側のみが検出対象となる。"""
        text = "計画ファイル本文の`スコープ相談節`を参照しつつ、優先順位について相談してから着手する。"
        assert _category_of(text) == "priority-consult"

    def test_priority_consult_phrase_inside_zenkaku_kakko_and_backtick_not_matched(self):
        """全角鍵括弧・バッククォート囲みの両方が同時に除去され、いずれの内部語彙も検出されない。"""
        text = "計画ファイル本文の「スコープ相談節」と`別のスコープ相談節`を確認してから実装する。"
        assert _match_scope_escalation(text) is None

    def test_async_wait_separated_form_notification_matched(self):
        """分離形「〜完了の通知を待つ」がasync-waitカテゴリで検出される（fb3反映）。"""
        assert _category_of("実行完了の通知を待つ。") == "async-wait"

    def test_async_wait_separated_form_bare_matched(self):
        """分離形「〜完了を待つ」がasync-waitカテゴリで検出される（fb3反映）。"""
        assert _category_of("処理完了を待つ。") == "async-wait"

    def test_async_wait_separated_form_negation_not_matched(self):
        """否定文「〜完了を待つ必要はない」は検出されない（fb3反映）。"""
        assert _match_scope_escalation("処理完了を待つ必要はない。") is None
        assert _match_scope_escalation("実行完了の通知を待つことはしない。") is None

    def test_async_wait_separated_form_inside_zenkaku_kakko_not_matched(self):
        """全角鍵括弧内へ引用転記された分離形は検出されない（fb3反映）。"""
        text = "codex指摘「実行完了の通知を待つ」というフレーズを引用する。"
        assert _match_scope_escalation(text) is None

    def test_async_wait_separated_form_inside_backtick_not_matched(self):
        """バッククォート囲み内へ引用転記された分離形は検出されない（fb3反映）。"""
        text = "指摘引用として`処理完了を待つ`を含める。"
        assert _match_scope_escalation(text) is None

    def test_async_wait_separated_form_completed_tense_not_matched(self):
        """完了時制「〜完了を待って〜した」は検出されない（fb3反映）。"""
        assert _match_scope_escalation("実行完了を待って処理を再開した。") is None
        assert _match_scope_escalation("処理完了を待って次工程へ進んだ。") is None


class TestApplyCategoryExclusions:
    """`_apply_category_exclusions`のカテゴリ別除外動作を検証する。"""

    def test_priority_consult_removes_zenkaku_kakko_content(self):
        """`priority-consult`カテゴリでは全角鍵括弧内が除去される。"""
        text = "計画ファイル本文の「スコープ相談節」を確認する。"
        result = _apply_category_exclusions(text, "priority-consult")
        assert "スコープ相談節" not in result
        assert "計画ファイル本文の" in result

    def test_other_category_returns_text_unchanged(self):
        """`priority-consult`以外のカテゴリはtextをそのまま返す。"""
        text = "計画ファイル本文の「スコープ相談節」を確認する。"
        assert _apply_category_exclusions(text, "workload") == text

    def test_priority_consult_without_kakko_returns_unchanged(self):
        """全角鍵括弧を含まないtextは`priority-consult`でも無変換で返す。"""
        text = "優先順位について相談する。"
        assert _apply_category_exclusions(text, "priority-consult") == text

    def test_priority_consult_removes_backtick_content(self):
        """`priority-consult`カテゴリではバッククォート囲み内が除去される。"""
        text = "計画ファイル本文の`スコープ相談節`を確認する。"
        result = _apply_category_exclusions(text, "priority-consult")
        assert "スコープ相談節" not in result
        assert "計画ファイル本文の" in result

    def test_priority_consult_removes_both_kakko_and_backtick_content(self):
        """`priority-consult`カテゴリでは全角鍵括弧・バッククォート囲みの両方が同時に除去される。"""
        text = "計画ファイル本文の「スコープ相談節」と`別のスコープ相談節`を確認する。"
        result = _apply_category_exclusions(text, "priority-consult")
        assert "スコープ相談節" not in result
        assert "計画ファイル本文の" in result


class TestIsEmptyCompletionReport:
    """`is_empty_completion_report`のサブエージェント完了報告判定。"""

    def test_returns_true_for_empty_text(self):
        """空文字列およびtrim後が空の入力は`True`を返す。"""
        assert is_empty_completion_report("") is True
        assert is_empty_completion_report("   \n\t  ") is True

    def test_returns_true_for_skill_invocation_only(self):
        """Skill呼び出し単独記述は`True`を返す。"""
        assert is_empty_completion_report("Skill(skill='foo')") is True
        assert is_empty_completion_report("Skill: agent-toolkit:writing-standards") is True

    def test_returns_false_for_short_normal_report(self):
        """10〜30字の正常な短文報告は`False`を返す。"""
        assert is_empty_completion_report("指摘なし") is False
        assert is_empty_completion_report("レビュー完了。指摘事項なし。") is False
        assert is_empty_completion_report("実装完了、テスト通過") is False

    def test_returns_false_for_normal_report(self):
        """100字以上の完了本文は`False`を返す。"""
        text = (
            "レビューを実施した。対象ファイルの実装内容は計画ファイル本文の設計要件と整合しており、"
            "重大な指摘は検出されなかった。日本語表現・型注釈・テストカバレッジの各観点でも問題なし。"
        )
        assert is_empty_completion_report(text) is False

    @pytest.mark.parametrize("value", [None, 123, ["Skill(skill='foo')"]])
    def test_returns_false_for_non_string(self, value: object):
        """非文字列入力は`False`を返す。"""
        assert is_empty_completion_report(value) is False

    def test_returns_true_for_skill_invocation_with_trailing_whitespace(self):
        """末尾に空白のみを持つSkill呼び出し単独記述は`True`を返す。"""
        assert is_empty_completion_report("Skill(skill='foo')   \n\n") is True
        assert is_empty_completion_report("  Skill: bar  ") is True

    def test_returns_false_for_skill_call_followed_by_body(self):
        """Skill呼び出し後に完了本文が続く正常報告は`False`を返す。"""
        text = "Skill(skill='foo')\n点検実施済。指摘事項なし。"
        assert is_empty_completion_report(text) is False
