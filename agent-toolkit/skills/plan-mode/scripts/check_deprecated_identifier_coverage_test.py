"""agent-toolkit/skills/plan-mode/scripts/check_deprecated_identifier_coverage.py のテスト。

計画ファイルの`#### 廃止・改名対象一覧`H4に列挙した識別子について、
リポジトリ横断grepのヒットファイル集合と`### 対象ファイル一覧`の差集合検出を検証する。
廃止・改名対象一覧が空の場合・全ヒットが対象ファイル一覧内の場合・
一部ヒットが対象ファイル一覧から漏れている場合の3ケースを網羅する。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

_SCRIPT = pathlib.Path(__file__).resolve().parent / "check_deprecated_identifier_coverage.py"


def _run(plan_path: pathlib.Path, *, repo_root: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """スクリプトを別プロセスで起動し結果を返す。

    `repo_root`をリポジトリルート探索の起点（`cwd`）とする。テスト用一時ディレクトリを
    疑似リポジトリルートとして扱い、`.git`を持たない前提で実行する。計画ファイル自体は
    `repo_root`の外側に配置し、計画本文中の識別子宣言行（`` - `<identifier>` ``）が
    grep対象として自己ヒットしないようにする。
    """
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(plan_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo_root,
    )


def test_no_deprecated_targets_exit_zero(tmp_path: pathlib.Path) -> None:
    """`#### 廃止・改名対象一覧`が空（識別子列挙なし）の場合はexit 0となる。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "kept_file.py").write_text("old_identifier = 1\n", encoding="utf-8")
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(
        """## 変更内容

### 対象ファイル一覧

- [ ] `kept_file.py` （現行1行, 見込み1行）

#### 廃止・改名対象一覧

なし（本計画は廃止・改名を含まない）。
""",
        encoding="utf-8",
    )

    result = _run(plan_path, repo_root=repo_root)

    assert result.returncode == 0


def test_all_hits_within_target_file_list_exit_zero(tmp_path: pathlib.Path) -> None:
    """廃止識別子のヒットファイル全てが`### 対象ファイル一覧`に含まれる場合はexit 0となる。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "kept_file.py").write_text("old_identifier = 1\n", encoding="utf-8")
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(
        """## 変更内容

### 対象ファイル一覧

- [ ] `kept_file.py` （現行1行, 見込み1行）

#### 廃止・改名対象一覧

- `old_identifier`
""",
        encoding="utf-8",
    )

    result = _run(plan_path, repo_root=repo_root)

    assert result.returncode == 0


def test_hits_in_excluded_dirs_are_ignored(tmp_path: pathlib.Path) -> None:
    """`.venv`・`node_modules`配下のヒットはgrep除外により違反として報告されない。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".venv" / "lib").mkdir(parents=True)
    (repo_root / ".venv" / "lib" / "dummy.py").write_text("old_identifier = 1\n", encoding="utf-8")
    (repo_root / "node_modules" / "pkg").mkdir(parents=True)
    (repo_root / "node_modules" / "pkg" / "dummy.py").write_text("old_identifier = 2\n", encoding="utf-8")
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(
        """## 変更内容

### 対象ファイル一覧

#### 廃止・改名対象一覧

- `old_identifier`
""",
        encoding="utf-8",
    )

    result = _run(plan_path, repo_root=repo_root)

    assert result.returncode == 0


def test_partial_hits_missing_from_target_file_list_exit_one(tmp_path: pathlib.Path) -> None:
    """廃止識別子のヒットファイルの一部が`### 対象ファイル一覧`から漏れている場合はexit 1となる。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "kept_file.py").write_text("old_identifier = 1\n", encoding="utf-8")
    (repo_root / "missed_file.py").write_text("old_identifier = 2\n", encoding="utf-8")
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(
        """## 変更内容

### 対象ファイル一覧

- [ ] `kept_file.py` （現行1行, 見込み1行）

#### 廃止・改名対象一覧

- `old_identifier`
""",
        encoding="utf-8",
    )

    result = _run(plan_path, repo_root=repo_root)

    assert result.returncode == 1
    assert "missed_file.py" in result.stderr
    assert "old_identifier" in result.stderr
