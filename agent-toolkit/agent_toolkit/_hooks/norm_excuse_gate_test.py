import pytest

from agent_toolkit._hooks import norm_excuse_gate


@pytest.mark.parametrize(
    "text",
    [
        "既存の条文で足りるため、再発防止のWIは作らない。",
        "残りのレビューでも採否を決める（agent-basics:review-standardsの既存の基準で足りるため、規範は足さない）。",
        "既存の規範で十分なので追加の対策はしない。",
        "既存の対策でカバーできる。",
        "規則にはもう書かれている。守れていなかっただけなので、再発防止は見送る。",
        "今回は再発防止策を見送る。既存の手順に明記されているためだ。",
        "現行の手順に定められているため、再発防止の対策は追加しない。",
        "今後気を付けます。",
    ],
)
def test_warns_about_excuses_without_a_retry_or_block(text: str) -> None:
    warning = norm_excuse_gate.warning_for_texts((text,))
    assert '<atk-auto source="norm_excuse_gate" kind="warn">' in warning
    assert "次の操作:" in warning


@pytest.mark.parametrize(
    "text",
    [
        "既存の対策が防げなかったため、検査を足す。",
        "既存の規範では足りないので構造を変える。",
        "既存の規範で十分ではないと分かった。",
        "既存の規範で足りていないため構造を変える。",
        "既存の対策が防げていない。",
        "既存の規範では防げず、検査を足す。",
        "既存の対策で被覆していないため、原因を調べる。",
        "既存の対策でカバーできないため、試験を足す。",
        "既存の規範で対応済みではありません。",
        "ユーザーは「既存の基準で足りる」という言い回しを止めるよう求めた。",
        "『今後気を付けます』だけでは不十分。",
        "今後気を付けますだけでは不足するため、試験を足す。",
        "今後気を付けます、だけでは不足するため、試験を足す。",
        "`既存の基準で足りる`という言い回しを検出する。",
        "> 既存の基準で足りる\n\nこの引用への対処を書く。",
        "```\n既存の基準で足りる\n```\n\n対処する。",
        "規範があるが防げなかったため、送達前の検査を追加する。別件は不具合ではなく、AWIは見送る。",
    ],
)
def test_ignores_negation_quotes_and_unrelated_work(text: str) -> None:
    assert not norm_excuse_gate.warning_for_texts((text,))
