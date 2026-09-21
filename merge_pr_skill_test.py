"""merge-prスキルの公開手順を静的に検証する。"""

from pathlib import Path

_SKILL_PATH = Path(__file__).resolve().parent / ".claude" / "skills" / "merge-pr" / "SKILL.md"


def test_develop_ci_wait_has_explicit_long_timeout() -> None:
    """develop CIの既定待機時間を過去の実行時間より長く固定する。"""
    skill = _SKILL_PATH.read_text(encoding="utf-8")

    wait_line = next(line for line in skill.splitlines() if "wait_ci.py --baseline" in line)
    assert "--timeout 1800" in wait_line


def test_local_develop_sync_resolves_the_linked_worktree() -> None:
    """現在cwdではなくdevelopを所有する一意なworktreeで同期する。"""
    skill = _SKILL_PATH.read_text(encoding="utf-8")

    assert "git worktree list --porcelain" in skill
    assert "`branch refs/heads/develop`を持つblock" in skill
    assert "0件又は複数件" in skill
    assert "git -C <develop worktreeの絶対パス> status --porcelain" in skill
    assert "中断状態が無いこと" in skill
    assert "作業ツリーがcleanであること" in skill
    assert "fast-forwardが成立すること" in skill
    assert "git -C <develop worktreeの絶対パス> merge --ff-only origin/master" in skill
    assert all(value in skill for value in ("省略した条件", "ローカル`develop`の短縮OID", "`origin/master`の短縮OID"))
