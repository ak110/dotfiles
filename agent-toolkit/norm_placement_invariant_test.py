"""エージェント向け文書をプラグインの`share/`へ置かないことを確かめる。

`share/`はスキルの標準の構成に無く、スキルだけを配置するホスト（OpenCodeなど）へ届かない。
ユーザーは、場面ごとに従う手順を標準のスキルへ置く方針を決めている（2026年9月25日、2026年11月16日に再指摘）。
置き場所は`.claude/skills/edit-dotfiles-norms/references/norm-layer-map.md`「層の一覧」から選ぶ。
"""

from pathlib import Path

_SHARE_DIR = Path(__file__).resolve().parent / "share"


def test_plugin_share_directory_has_no_markdown() -> None:
    """`agent-toolkit/share/`配下にMarkdownが無い（ディレクトリが無い場合も合格とする）。"""
    found = sorted(str(path.relative_to(_SHARE_DIR.parent)) for path in _SHARE_DIR.rglob("*.md")) if _SHARE_DIR.is_dir() else []
    assert not found, "エージェント向け文書は`share/`ではなく入口`atk-mode`の配下か責務の合うスキルへ置く: " + "、".join(found)
