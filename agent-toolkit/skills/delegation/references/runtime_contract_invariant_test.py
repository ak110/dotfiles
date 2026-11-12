"""定期再確認の標識と実行手順の一致を確かめる。"""

import pathlib
import re

from agent_toolkit._hooks import user_prompt_submit

_MOD_DEFINITION = pathlib.Path(__file__).resolve().parents[3] / "hooks" / "periodic_recheck.ts"


def test_mod_marker_matches_the_hook_marker() -> None:
    """modが既存taskの保有と出力の形を判定する標識が、フックの標識と同じリテラルを持つ。

    片方だけを改訂すると、modは既存のtaskを保有に数えずに重複作成するか、正しい出力を装着失敗として扱う。
    """
    match = re.search(r"^export const PERIODIC_RECHECK_MARKER = '([^']*)';$", _MOD_DEFINITION.read_text(encoding="utf-8"), re.M)
    assert match is not None
    assert match.group(1) == user_prompt_submit.PERIODIC_RECHECK_MARKER
