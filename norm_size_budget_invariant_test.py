"""エージェントへ届ける規範のMarkdownの合計サイズが、記録した上限を超えないことを確かめる。

対象は`agent-toolkit/skills/`と`agent-basics/skills/`の追跡済みMarkdown（試験用の`fixtures/`を除く）で、
両プラグインにまたがるためリポジトリ直下に置く。
規範を足す前に引く原則（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）を文章だけで
守れず合計が増え続けたため、増やす時に差分とレビューへ表れるようにする（2026年11月22日のユーザー判断）。
上限を上げる時は`_BUDGET_BYTES`を書き換え、理由をcommit本文の`規範量:`の行に書く。
"""

import pathlib
import subprocess
import warnings

_REPO_ROOT = pathlib.Path(__file__).resolve().parent
_ROOTS = ("agent-toolkit/skills", "agent-basics/skills")
_BUDGET_BYTES = 2_681_044
# 上限との差がこの比率を超えたら、上限を下げるよう警告する（減らした分を次の追加で使い切らないため）。
_SLACK_RATIO = 0.05


def _norm_markdown_bytes(root: pathlib.Path) -> int:
    """対象の追跡済みMarkdownの合計バイト数を返す。"""
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", *(f"{prefix}/*.md" for prefix in _ROOTS)],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    paths = [path for path in listed.split("\0") if path and "/fixtures/" not in path]
    return sum((root / path).stat().st_size for path in paths if (root / path).is_file())


def test_norm_markdown_within_budget() -> None:
    total = _norm_markdown_bytes(_REPO_ROOT)
    assert total <= _BUDGET_BYTES, (
        f"規範のMarkdownの合計が{total}バイトで、上限{_BUDGET_BYTES}バイトを{total - _BUDGET_BYTES}バイト超えた。"
        "先に既存の記述の削除・統合・検査への置き換えで減らせないかを比べる。"
        f"それでも増やす時は{pathlib.Path(__file__).name}の_BUDGET_BYTESを{total}へ上げ、理由をcommit本文の`規範量:`の行に書く。"
    )
    if _BUDGET_BYTES - total > _BUDGET_BYTES * _SLACK_RATIO:
        warnings.warn(
            f"規範のMarkdownの合計（{total}バイト）が上限（{_BUDGET_BYTES}バイト）を大きく下回った。"
            f"{pathlib.Path(__file__).name}の_BUDGET_BYTESを{total}へ下げる。",
            stacklevel=1,
        )
