"""`check_agent_doc_tone.py`の検出用データが表記規則の定める語と一致することを検証する。"""

import pathlib

import check_agent_doc_tone


def test_denied_patterns_match_notation_rules_terms() -> None:
    """失敗させる語は、表記規則が説明に使わないと定める`対象語:`の行の語と一致する。

    規範だけに語を加えるとその語を使った説明がcommitで止まらず、コードだけに加えると規範に無い語でcommitが止まる。
    """
    notation_rules = (
        pathlib.Path(__file__).resolve().parents[1] / "agent-toolkit/skills/writing-standards/references/notation-rules.md"
    )
    lines = [line for line in notation_rules.read_text(encoding="utf-8").splitlines() if line.startswith("対象語: ")]
    assert len(lines) == 1, lines

    # 検出用データ自体を規範と比べることが本テストの目的であるため、非公開の定数を直接読む。
    denied = check_agent_doc_tone._DENIED_PATTERNS  # pylint: disable=protected-access
    assert set(lines[0].removeprefix("対象語: ").split("、")) == set(denied)
