"""install.sh のテスト。

隔離 `$HOME` に現在のリポジトリを複製して install.sh を実行し、chezmoi による
デプロイが行われることを検証する。外部ネットワーク依存を避けるため:

- git clone 分岐は事前に `$FAKE_HOME/dotfiles` を作って回避
- chezmoi ダウンロード分岐はシステムの chezmoi バイナリを `$FAKE_HOME/.local/bin/`
  にコピーして回避
"""

import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


@pytest.mark.skipif(shutil.which("chezmoi") is None, reason="chezmoi未インストール")
def test_install_sh_deploys_rules(tmp_path: pathlib.Path):
    """install.sh が chezmoi でルールを ~/.claude/rules/ に配置する。"""
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    # 1. リポジトリを fake_home/dotfiles に複製 (git clone 分岐を回避)
    fake_dotfiles = fake_home / "dotfiles"
    _copy_repo(REPO_ROOT, fake_dotfiles)

    # 2. システムの chezmoi を fake_home/.local/bin に配置 (ダウンロード分岐を回避)
    chezmoi_bin = shutil.which("chezmoi")
    assert chezmoi_bin is not None
    local_bin = fake_home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    shutil.copy2(chezmoi_bin, local_bin / "chezmoi")

    # 3. install.sh を実行
    env = {
        "HOME": str(fake_home),
        "PATH": f"{local_bin}:/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(
        ["bash", str(INSTALL_SH)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    # 4. ルールファイルがデプロイされていること。
    # rules側の配布対象はagent.mdとstyles.mdの2ファイル。
    # その他の規約はagent-toolkitプラグインのスキルが担う。
    rules_dir = fake_home / ".claude" / "rules" / "agent-basics"
    assert (rules_dir / "agent.md").exists(), "agent.md が chezmoi でデプロイされていない"
    assert (rules_dir / "styles.md").exists(), "styles.md が chezmoi でデプロイされていない"


def _copy_repo(src: pathlib.Path, dst: pathlib.Path) -> None:
    """リポジトリを複製。.venv や巨大ディレクトリは除外して高速化。"""

    # .git は chezmoi init が内部で使うわけではないので不要
    # .venv / node_modules などテスト無関係な巨大ディレクトリは除外
    def _ignore(_dir: str, names: list[str]) -> list[str]:
        del _dir  # noqa
        return [n for n in names if n in {".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".git"}]

    shutil.copytree(src, dst, ignore=_ignore, symlinks=True)
