"""エージェントへ届ける規範のMarkdownの合計サイズが、上限を超えないことを確かめる。

対象は`agent-toolkit/skills/`と`agent-basics/skills/`の追跡済みMarkdown（試験用の`fixtures/`を除く）で、
両プラグインにまたがるためリポジトリ直下に置く。
規範を足す前に引く原則（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）を文章だけで
守れず合計が増え続けたため、上限と整理後の目標の2段階で検査する（2026年11月22日のユーザー判断）。
上限を超えたら、その変更の中で削って収めず、合計を整理後の目標以下へ下げる専用の整理を行う。
整理は`.claude/skills/edit-dotfiles-norms/SKILL.md`「規範を削除・縮小するときの消失確認」に従う。
上限を上げるのは、ユーザーが自分から明示的に上げてよいと指示した時に限る。
エージェントは上限の引き上げを提案も確認もしない。
"""

import pathlib
import subprocess

_REPO_ROOT = pathlib.Path(__file__).resolve().parent
_ROOTS = ("agent-toolkit/skills", "agent-basics/skills")
_BUDGET_BYTES = 2_681_044
# 整理後の目標の上限に対する比率。整理の後に、目標と上限の差だけ足せる余裕を残す。
_TARGET_RATIO = 0.85


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
    target = int(_BUDGET_BYTES * _TARGET_RATIO)
    assert total <= _BUDGET_BYTES, (
        f"規範のMarkdownの合計が{total}バイトで、上限{_BUDGET_BYTES}バイトを{total - _BUDGET_BYTES}バイト超えた。"
        "この変更の中で削って収めない。"
        f"合計を整理後の目標{target}バイト以下へ下げる専用の整理を、"
        ".claude/skills/edit-dotfiles-norms/SKILL.mdの「規範を削除・縮小するときの消失確認」に従って行い、その後にこの変更を入れる。"
        f"{pathlib.Path(__file__).name}の_BUDGET_BYTESを上げるのは、ユーザーが自分から明示的に指示した時に限り、"
        "エージェントは引き上げを提案も確認もしない。"
    )
