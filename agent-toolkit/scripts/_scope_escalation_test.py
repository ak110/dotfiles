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
from _scope_escalation import _STOP_FOCUS_CATEGORIES, _match_scope_escalation, has_inline_choice_offer
from _scope_escalation_test_helpers import load_scope_escalation_inputs

_INPUTS = load_scope_escalation_inputs()


class TestMatchScopeEscalation:
    """`_match_scope_escalation`のカテゴリ分類。"""

    @pytest.mark.parametrize(("text", "category"), _INPUTS)
    def test_fixture_inputs_match_expected_category(self, text: str, category: str):
        """隔離フィクスチャの各入力が期待カテゴリへ分類されることを確認する。"""
        assert _match_scope_escalation(text) == category

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "通常の進捗を報告する",
            "対象ファイル一覧を確認する",
            "テスト実行結果を報告する",
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
            assert _match_scope_escalation(phrase, categories=_STOP_FOCUS_CATEGORIES) == target
        # `_STOP_FOCUS_CATEGORIES`に含まれないカテゴリは検出されない。
        for cat, phrase in by_category.items():
            if cat in _STOP_FOCUS_CATEGORIES:
                continue
            assert _match_scope_escalation(phrase, categories=_STOP_FOCUS_CATEGORIES) is None

    def test_categories_none_matches_all(self):
        """`categories=None`（既定）は全カテゴリを照合対象とする。"""
        for text, category in _INPUTS:
            assert _match_scope_escalation(text, categories=None) == category

    def test_completion_difficulty_matches_single_session(self):
        """`本セッションのリソースでは完遂困難`は`single-session`を返す。"""
        assert _match_scope_escalation("本セッションのリソースでは完遂困難と判断する。") == "single-session"

    def test_scale_difficulty_matches_single_session(self):
        """`規模的に本セッションでは困難`は`single-session`を返す。"""
        assert _match_scope_escalation("規模的に本セッションでは困難のため一度に処理できない。") == "single-session"

    def test_general_completion_difficulty_not_matched(self):
        """`本|この|単一`セッション接頭を伴わない一般的な困難表現は`single-session`と判定しない。"""
        assert _match_scope_escalation("実装完遂は技術的に困難だが対応する。") is None

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
