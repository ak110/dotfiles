"""定期再確認の標識と実行手順の一致を確かめる。"""

import pathlib
import re

from agent_toolkit._common import periodic_recheck
from agent_toolkit._hooks import user_prompt_submit

_MOD_DEFINITION = pathlib.Path(__file__).resolve().parents[3] / "hooks" / "periodic_recheck.ts"


def test_periodic_recheck_marker_matches_the_runtime_document() -> None:
    """フックの標識と`claude-code-runtime.md`の記述が同じリテラルを持つ。

    標識を2箇所が保持するため、片方だけの改訂で機械注入判定が成立しなくなる状態を検出する。
    """
    document = pathlib.Path(__file__).resolve().with_name("claude-code-runtime.md")
    assert f"`{user_prompt_submit.PERIODIC_RECHECK_MARKER}`" in document.read_text(encoding="utf-8")


def test_periodic_prompt_definition_starts_with_the_hook_marker() -> None:
    """`atk wait-schedule --format json`が出力する定期promptの1行目が、フックが機械注入ターンと判定する標識だけの行である。

    一致しないと、modと規範の手順が作成したtaskの発火がユーザー発話として扱われ、resume後の所有taskの確認も一致しなくなる。
    """
    assert periodic_recheck.PERIODIC_RECHECK_PROMPT.split("\n", 1)[0] == user_prompt_submit.PERIODIC_RECHECK_MARKER


def test_mod_marker_matches_the_hook_marker() -> None:
    """modが既存taskの保有と出力の形を判定する標識が、フックの標識と同じリテラルを持つ。

    片方だけを改訂すると、modは既存のtaskを保有に数えずに重複作成するか、正しい出力を装着失敗として扱う。
    """
    match = re.search(r"^export const PERIODIC_RECHECK_MARKER = '([^']*)';$", _MOD_DEFINITION.read_text(encoding="utf-8"), re.M)
    assert match is not None
    assert match.group(1) == user_prompt_submit.PERIODIC_RECHECK_MARKER
