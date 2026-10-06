"""定期再確認の標識と実行手順の一致を確かめる。"""

import pathlib
import re

from agent_toolkit._hooks import user_prompt_submit

_PROMPT_DEFINITION = pathlib.Path(__file__).resolve().parents[3] / "hooks" / "periodic_recheck_prompt.ts"


def test_periodic_recheck_marker_matches_the_runtime_document() -> None:
    """フックの標識と`claude-code-runtime.md`の記述が同じリテラルを持つ。

    標識を2箇所が保持するため、片方だけの改訂で機械注入判定が成立しなくなる状態を検出する。
    """
    document = pathlib.Path(__file__).resolve().with_name("claude-code-runtime.md")
    assert f"`{user_prompt_submit.PERIODIC_RECHECK_MARKER}`" in document.read_text(encoding="utf-8")


def test_periodic_recheck_prompt_definition_starts_with_the_hook_marker() -> None:
    """定期promptの定義元の1行目の標識が、フックが機械注入ターンと判定する標識と同じリテラルを持つ。

    片方だけを改訂すると、modが作成したtaskの発火がユーザー発話として扱われ、resume後の所有taskの確認も一致しなくなる。
    """
    match = re.search(
        r"^export const PERIODIC_RECHECK_MARKER = '([^']*)';$", _PROMPT_DEFINITION.read_text(encoding="utf-8"), re.M
    )
    assert match is not None
    assert match.group(1) == user_prompt_submit.PERIODIC_RECHECK_MARKER
