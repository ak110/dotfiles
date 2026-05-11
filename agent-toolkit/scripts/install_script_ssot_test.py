"""install-claude.{sh,ps1}とagent-toolkit/rules/配下のSSOT整合性テスト。

ワンライナーインストーラーはGitHub Raw経由で個別ファイルを取得する都合上、
配布対象のファイル名を配列としてスクリプト内に保持する。
`agent-toolkit/rules/`配下のファイル追加・削除時には両スクリプトの手動同期が必要で、
本テストは3者の一致を検証して同期漏れを検知する。
"""

import pathlib
import re
from collections.abc import Callable

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_RULES_DIR = _REPO_ROOT / "agent-toolkit" / "rules"
_INSTALL_SH = _REPO_ROOT / "install-claude.sh"
_INSTALL_PS1 = _REPO_ROOT / "install-claude.ps1"


def _read_rules_dir() -> list[str]:
    return sorted(p.name for p in _RULES_DIR.glob("*.md"))


def _read_sh_files() -> list[str]:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(r"FILES=\(([^)]*)\)", text)
    if match is None:
        raise AssertionError("install-claude.sh のFILES=( ... )が見つからない")
    body = match.group(1)
    names: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        names.extend(line.split())
    return sorted(names)


def _read_ps1_files() -> list[str]:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    match = re.search(r"\$files\s*=\s*@\(([^)]*)\)", text)
    if match is None:
        raise AssertionError("install-claude.ps1 の$files = @( ... )が見つからない")
    body = match.group(1)
    return sorted(re.findall(r"'([^']+)'", body))


class TestInstallScriptSsot:
    """`agent-toolkit/rules/`配下のmdファイル一覧と`install-claude.{sh,ps1}`の配列の整合性を検証する。"""

    def test_rules_dir_exists(self):
        assert _RULES_DIR.is_dir(), f"agent-toolkit/rules/ が存在しない: {_RULES_DIR}"
        assert _read_rules_dir(), "agent-toolkit/rules/ にmdファイルが存在しない"

    @pytest.mark.parametrize(
        ("name", "reader"),
        [
            ("install-claude.sh", _read_sh_files),
            ("install-claude.ps1", _read_ps1_files),
        ],
    )
    def test_install_script_matches_rules_dir(self, name: str, reader: Callable[[], list[str]]):
        rules = _read_rules_dir()
        script_files = reader()
        assert script_files == rules, (
            f"{name} のファイル一覧が agent-toolkit/rules/ と不一致: script={script_files} rules={rules}"
        )

    def test_install_sh_and_ps1_match_each_other(self):
        sh_files = _read_sh_files()
        ps1_files = _read_ps1_files()
        assert sh_files == ps1_files, (
            f"install-claude.sh と install-claude.ps1 のファイル一覧が不一致: sh={sh_files} ps1={ps1_files}"
        )
