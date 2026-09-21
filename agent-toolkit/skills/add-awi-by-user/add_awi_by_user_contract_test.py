"""`add-awi-by-user`の対象別状態を伝える契約の回帰テスト。"""

import pathlib


def test_mixed_target_results_require_an_opening_contrast() -> None:
    """同じ正常判定が複数あっても、変更対象との混在時は対照を要求する。"""
    skill = (pathlib.Path(__file__).parent / "SKILL.md").read_text(encoding="utf-8")

    assert "正常な対象と変更を要する対象が混在する場合" in skill
    assert "AWIが変更する範囲を最初の1文で対照する" in skill
    assert "全対象が同じ判定の場合と調査途中は対象外" in skill
    assert "全対象の判定が異なり" not in skill
