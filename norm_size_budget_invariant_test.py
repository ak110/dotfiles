"""エージェントへ届ける規範のMarkdownの合計サイズが、上限を超えないことを確かめる。

対象は`agent-toolkit/skills/`と`agent-basics/skills/`の追跡済みMarkdown（試験用の`fixtures/`を除く）で、
両プラグインにまたがるためリポジトリ直下に置く。
規範を足す前に引く原則（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）を文章だけで
守れず合計が増え続けたため、上限を超えたら次のセッションで整理できるよう、発見した担当に削減AWIの登録を案内する。
"""

import pathlib
import subprocess

_REPO_ROOT = pathlib.Path(__file__).resolve().parent
_ROOTS = ("agent-toolkit/skills", "agent-basics/skills")


_BUDGET_BYTES = 2_760_000


def _norm_markdown_bytes(root: pathlib.Path) -> int:
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
        "次のセッションで削減するため、発見した担当が削減AWIを登録する。"
        ".claude/skills/edit-dotfiles-norms/SKILL.mdの「規範を削除・縮小するときの消失確認」に従い、"
        "削減量と整理後の上限は、必要な規範を保ちながらdotfilesの価値を最大化するように決める。"
    )
