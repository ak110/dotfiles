"""chezmoiテンプレートのホスト別の展開結果が、役割データ`.chezmoi-source/.chezmoidata.toml`の定義と一致することを検証する。

テンプレートの配置先`.chezmoi-source/`は配布元であり、テストを置くと配布対象に混ざるため、包含するリポジトリ直下へ置く。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
SOURCE = REPO_ROOT / ".chezmoi-source"

pytestmark = pytest.mark.skipif(shutil.which("chezmoi") is None, reason="chezmoi未インストール")

# 役割を持つホスト名と、大文字・ドメイン付き・役割を持たない名前を含める。
# chezmoiの`.chezmoi.hostname`は短縮名だが、テンプレートは小文字化だけで比べるため、ドメイン付きの名前は一致しない。
_HOSTNAMES = ["euryale", "EURYALE.example.com", "euryale-container", "stheno", "Stheno", "other"]


def _execute(template: Path, hostname: str) -> str:
    completed = subprocess.run(
        [
            "chezmoi",
            "--source",
            str(SOURCE),
            "--working-tree",
            str(REPO_ROOT),
            "execute-template",
            "--override-data",
            json.dumps({"chezmoi": {"hostname": hostname}}),
            "--file",
            str(template),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


@pytest.mark.parametrize("hostname", _HOSTNAMES)
def test_myprojects_selects_section_by_role(hostname: str) -> None:
    """Windowsアプリの環境（stheno）と全プロジェクトの環境（euryale・euryale-container）で本文を切り替える。"""
    text = _execute(SOURCE / "dot_claude" / "rules" / "myprojects.md.tmpl", hostname)
    assert ("D:\\VC\\_Develop\\gv" in text) is (hostname.lower() == "stheno")
    assert ("## プロジェクト間の同期" in text) is (hostname.lower() in {"euryale", "euryale-container"})


@pytest.mark.parametrize("hostname", _HOSTNAMES)
def test_ssh_peer_hosts_rule_only_for_peer_hosts(hostname: str) -> None:
    """stheno・euryaleだけに本文を出し、それぞれ相手のホストへの接続先を示す。他では出力が空になりファイルが作られない。"""
    text = _execute(SOURCE / "dot_claude" / "rules" / "ssh-peer-hosts.local.md.tmpl", hostname)
    short = hostname.lower()
    assert ("ssh-unelevated stheno" in text) is (short == "euryale")
    if short == "stheno":
        assert "ssh euryale" in text
        assert "ssh stheno" not in text
    elif short == "euryale":
        assert "ssh stheno" in text
        assert "ssh euryale" not in text
    else:
        assert text.strip() == ""


def test_ssh_peer_hosts_rule_target_is_local_md() -> None:
    """配布先が`~/.claude/rules/`直下の`*.local.md`になり、Codexの読込指示とagents_serverの連結の対象に入る。"""
    completed = subprocess.run(
        [
            "chezmoi",
            "--source",
            str(SOURCE),
            "--working-tree",
            str(REPO_ROOT),
            "target-path",
            str(SOURCE / "dot_claude" / "rules" / "ssh-peer-hosts.local.md.tmpl"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    target = Path(completed.stdout.strip())
    assert target.parent.name == "rules"
    assert target.parent.parent.name == ".claude"
    assert target.name == "ssh-peer-hosts.local.md"
