"""install.sh のテスト。

隔離 `$HOME` に現在のリポジトリを複製して install.sh を実行し、chezmoi による
デプロイが行われることを検証する。外部ネットワーク依存を避けるため:

- git clone 分岐は事前に `$FAKE_HOME/dotfiles` を用意して回避
- chezmoi ダウンロード分岐はシステムの chezmoi バイナリを `$FAKE_HOME/.local/bin/`
  にコピーして回避
- Claude Code・Codex CLIとnpmの導入分岐は成功する代替実行ファイルを
  `$FAKE_HOME/.local/bin/`に配置して回避
"""

import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent
INSTALL_SH = REPO_ROOT / "install.sh"


@pytest.mark.skipif(shutil.which("chezmoi") is None, reason="chezmoi未インストール")
def test_install_sh_deploys_rules(tmp_path: pathlib.Path):
    """install.sh が chezmoi でルールを ~/.claude/rules/ に配置する。"""
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    # 1. リポジトリを fake_home/dotfiles に複製（git clone 分岐を回避）
    fake_dotfiles = fake_home / "dotfiles"
    _copy_repo(REPO_ROOT, fake_dotfiles)

    # 2. システムの chezmoi を fake_home/.local/bin に配置（ダウンロード分岐を回避）
    chezmoi_bin = shutil.which("chezmoi")
    assert chezmoi_bin is not None
    local_bin = fake_home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    shutil.copy2(chezmoi_bin, local_bin / "chezmoi")
    _write_fake_cli(local_bin / "claude")
    _write_fake_cli(local_bin / "codex")
    _write_fake_npm(local_bin / "npm")

    # 3. install.sh を実行
    env = {
        "HOME": str(fake_home),
        "PATH": f"{local_bin}:/usr/bin:/bin:/usr/local/bin",
        "LANG": "C.UTF-8",
    }
    subprocess.run(
        ["bash", str(INSTALL_SH)],
        env=env,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )

    # 4. ルールファイルがデプロイされていること。
    # rules側の配布対象は01-agent.md・02-collaboration.md・03-claude-code.md・
    # 04-styles.md・05-terminology.mdの5ファイル。
    # その他の規約はagent-toolkitプラグインのスキルが担う。
    # 代表として01-agent.mdの存在のみを検証する（5ファイル一致は install_script_ssot_test.py が担う）。
    rules_dir = fake_home / ".claude" / "rules" / "agent-toolkit"
    assert (rules_dir / "01-agent.md").exists(), "01-agent.md が chezmoi でデプロイされていない"


def _copy_repo(src: pathlib.Path, dst: pathlib.Path) -> None:
    """リポジトリを複製。.venv や巨大ディレクトリは除外して高速化。"""

    # .git は chezmoi init が内部で使うわけではないので不要。
    # .venv / node_modules などテスト無関係な巨大ディレクトリは除外する。
    def _ignore(_dir: str, names: list[str]) -> list[str]:
        del _dir  # noqa
        return [n for n in names if n in {".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".git"}]

    shutil.copytree(src, dst, ignore=_ignore, symlinks=True)


def _write_fake_cli(path: pathlib.Path) -> None:
    """外部取得を避けるため、常に成功するCLI代替実行ファイルを配置する。"""
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def _write_fake_npm(path: pathlib.Path) -> None:
    """Codexのglobal導入先を隔離HOMEへ向けるnpm代替実行ファイルを配置する。"""
    path.write_text(
        '#!/bin/sh\nif [ "$1" = "prefix" ]; then\n    printf "%s\\n" "$HOME/.local"\nfi\nexit 0\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
