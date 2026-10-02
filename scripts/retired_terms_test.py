"""撤去した名称が、読み取り互換を定める許容箇所の外のGit追跡ファイルへ現れないことを検査する。

撤去を規範本文の置換だけで伝えると、過去のセッション記録、終端済みのWIおよび旧形式の計画に残る旧名を
読んだ書き手が、同じ語を現行の規範へ書き戻す。書き戻しをこのテストの失敗として検出する。

名称を撤去するときは`_RETIRED_TERMS`へ撤去語、正式名および許容箇所を1件加える。
許容箇所は現行の読み取り互換を定める箇所（旧名の定数、その互換を説明する文、旧形式の基準文書、
旧形式を読む互換テスト）に限る。互換を定めない箇所を許容すると、その箇所への書き戻しを検出できない。
走査対象は`git grep`が扱うGit追跡ファイルの作業ツリー上の内容とし、隠しディレクトリも含める。
"""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess

import pytest

pytestmark = pytest.mark.repo_invariant

_ROOT = pathlib.Path(__file__).resolve().parents[1]


@dataclasses.dataclass(frozen=True)
class _AllowedLocation:
    """撤去語が残ってよい箇所。"""

    path_pattern: str
    """リポジトリ相対パスへ`PurePosixPath.match`で照合するパターン。"""
    line_substring: str | None = None
    """指定した場合は、この文字列を含む行だけを許容する。"""

    def allows(self, path: str, line: str) -> bool:
        """指定の行を許容するかを返す。"""
        if not pathlib.PurePosixPath(path).match(self.path_pattern):
            return False
        return self.line_substring is None or self.line_substring in line


@dataclasses.dataclass(frozen=True)
class _RetiredTerm:
    """撤去した名称と、その正式名および許容箇所。"""

    term: str
    replacement: str
    allowed: tuple[_AllowedLocation, ...]


_RETIRED_TERMS = (
    _RetiredTerm(
        term="近接検証",
        replacement="変更範囲の検証",
        allowed=(
            # 旧形式の計画を読むための構造定数（`PLAN_LEGACY_*`）
            _AllowedLocation("agent-toolkit/agent_toolkit/_plan/structure/constants.py"),
            # 既存計画の旧名を読み取り互換として受理することを説明する文
            _AllowedLocation("agent-toolkit/skills/plan-mode/references/plan-file-standards.md", "読み取り互換"),
            # 旧形式の計画の基準文書
            _AllowedLocation("agent-toolkit/skills/plan-mode/references/legacy-plan-file-standards.md"),
            # 旧形式の計画を読む互換のテスト
            _AllowedLocation("*_test.py"),
        ),
    ),
    # 計画実装型AWIと計画化の手順は後継なし（2026年9月13日に廃止）。
    *(
        _RetiredTerm(
            term=term,
            replacement="なし（2026年9月13日に廃止）",
            allowed=(
                # 日付の付いた過去の障害記録
                _AllowedLocation("docs/development/incidents-*.md"),
                # 廃止を決めた利用者の方針記録
                _AllowedLocation("docs/development/concepts-principles.md"),
                # 廃止を記す方針記録の行
                _AllowedLocation("docs/development/concepts-workflows.md", "2026年9月13日の利用者指示で廃止した"),
                # 撤去の不在を確かめるテスト
                _AllowedLocation("*_test.py"),
            ),
        )
        for term in ("計画実装型", "convert-to-plan")
    ),
)


def _find_violations(repo_root: pathlib.Path, terms: tuple[_RetiredTerm, ...] = _RETIRED_TERMS) -> list[str]:
    """許容箇所の外に現れた撤去語を`<パス>:<行番号>`付きのメッセージで返す。"""
    violations: list[str] = []
    for retired in terms:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "grep", "-n", "-I", "-F", "-z", "--no-color", "--full-name", "-e", retired.term],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        # git grepは一致が無い場合に終了コード1を返す。
        if proc.returncode == 1:
            continue
        assert proc.returncode == 0, proc.stderr
        for record in proc.stdout.splitlines():
            path, line_number, line = record.split("\0", 2)
            if any(location.allows(path, line) for location in retired.allowed):
                continue
            violations.append(
                f"{path}:{line_number}: 撤去した名称「{retired.term}」が残っている。正式名「{retired.replacement}」へ置き換える"
            )
    return violations


def _init_repo(root: pathlib.Path, files: dict[str, str]) -> None:
    """一時ディレクトリへファイルを置き、Gitの追跡対象へ加える。"""
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True, text=True, encoding="utf-8")
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "--", *files], check=True, capture_output=True, text=True, encoding="utf-8")


def test_retired_terms_absent_outside_allowed_locations() -> None:
    """リポジトリのGit追跡ファイルに、許容箇所の外の撤去語が無い。"""
    violations = _find_violations(_ROOT)
    assert not violations, "\n".join(violations)


def test_retired_term_outside_allowed_location_is_reported(tmp_path: pathlib.Path) -> None:
    """許容箇所の外と、行条件を満たさない許容ファイルの行を、パス・行番号・正式名つきで報告する。"""
    _init_repo(
        tmp_path,
        {
            ".claude/skills/example/SKILL.md": "# 例\n\nレーンの近接検証へテストを含める。\n",
            "agent-toolkit/skills/plan-mode/references/plan-file-standards.md": "近接検証の1行表を使う。\n",
        },
    )

    violations = _find_violations(tmp_path)

    assert violations == [
        ".claude/skills/example/SKILL.md:3: 撤去した名称「近接検証」が残っている。正式名「変更範囲の検証」へ置き換える",
        "agent-toolkit/skills/plan-mode/references/plan-file-standards.md:1: "
        "撤去した名称「近接検証」が残っている。正式名「変更範囲の検証」へ置き換える",
    ]


def test_retired_term_in_allowed_locations_is_accepted(tmp_path: pathlib.Path) -> None:
    """読み取り互換を定める許容箇所だけに撤去語が残る場合は違反としない。"""
    _init_repo(
        tmp_path,
        {
            "agent-toolkit/agent_toolkit/_plan/structure/constants.py": 'LEGACY = ("近接検証",)\n',
            "agent-toolkit/skills/plan-mode/references/plan-file-standards.md": (
                "旧名「近接検証」の表は読み取り互換で受理する。\n"
            ),
            "agent-toolkit/skills/plan-mode/references/legacy-plan-file-standards.md": "- `近接検証`列\n",
            "agent-toolkit/agent_toolkit/_plan/structure/parsing_test.py": 'LEGACY = "近接検証"\n',
        },
    )

    assert not _find_violations(tmp_path)
