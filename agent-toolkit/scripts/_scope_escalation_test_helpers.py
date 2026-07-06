"""scope-escalation検出テストの共通フィクスチャ読み込みヘルパー。

`_scope_escalation_test.py`・`pretooluse_test.py`・`stop_advisor_test.py`が
隔離フィクスチャ`agent-toolkit/skills/agent-standards/references/_scope_escalation_test_inputs.txt`
を読み込む処理をSSOTで集約する。

検出語そのものをテストコード本文へ転記しないため、隔離フィクスチャから動的に読み込む
（`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節）。
"""

import pathlib

SCOPE_ESCALATION_INPUTS_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "skills"
    / "agent-standards"
    / "references"
    / "_scope_escalation_test_inputs.txt"
)


def load_scope_escalation_inputs() -> list[tuple[str, str]]:
    r"""隔離フィクスチャからテスト入力を読み込む。

    フォーマットは`<expected-category>\\t<minimal-matching-text>`のタブ区切り。
    空行と`#`先頭行はスキップする。
    フィクスチャ不在時は空リストを返す（モジュールコレクション失敗を避けるため）。
    返却値の各要素は`(text, category)`で、pytest parametrizeに渡す並びに整合する。
    """
    if not SCOPE_ESCALATION_INPUTS_PATH.exists():
        return []
    inputs: list[tuple[str, str]] = []
    for raw in SCOPE_ESCALATION_INPUTS_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" not in stripped:
            continue
        category, text = stripped.split("\t", 1)
        inputs.append((text.strip(), category.strip()))
    return inputs
