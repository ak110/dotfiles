"""`check_plan_meta.py`の単体テスト。

`## 背景`配下`### 計画メタ情報`H3の欠落、および配下`- 起動経路:`・`- 対象リポジトリ:`
各行の欠落について、それぞれpositive/negativeケースを検証する。
"""

from __future__ import annotations

import pathlib
import subprocess

_SCRIPT = pathlib.Path(__file__).with_name("check_plan_meta.py")

_VALID_META = """## 背景

### 計画メタ情報

- 起動経路: plan-and-add-feedback経由
- 対象リポジトリ: `~/dotfiles`

### 経緯

動機の要約。

## 対応方針
"""


def _run(tmp_path: pathlib.Path, content: str) -> subprocess.CompletedProcess[str]:
    """スクリプトを別プロセスで起動し結果を返す。"""
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(content, encoding="utf-8")
    return subprocess.run(
        ["python3", str(_SCRIPT), str(plan_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_meta_passes(tmp_path: pathlib.Path) -> None:
    """`### 計画メタ情報`と必須2項目が揃っていれば違反なし。"""
    result = _run(tmp_path, f"# タイトル\n\n{_VALID_META}")
    assert result.returncode == 0
    assert result.stderr == ""


def test_missing_background_section(tmp_path: pathlib.Path) -> None:
    """`## 背景`自体が存在しない場合は違反。"""
    content = "# タイトル\n\n## 対応方針\n"
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "plan-meta-missing" in result.stderr


def test_missing_meta_subsection(tmp_path: pathlib.Path) -> None:
    """`## 背景`はあるが`### 計画メタ情報`H3が無い場合は違反。"""
    content = "# タイトル\n\n## 背景\n\n### 経緯\n\n動機の要約。\n\n## 対応方針\n"
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "plan-meta-missing" in result.stderr


def test_missing_launch_route(tmp_path: pathlib.Path) -> None:
    """`- 起動経路:`行が無い場合は違反。"""
    content = "# タイトル\n\n## 背景\n\n### 計画メタ情報\n\n- 対象リポジトリ: `~/dotfiles`\n\n## 対応方針\n"
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "起動経路" in result.stderr


def test_missing_target_repo(tmp_path: pathlib.Path) -> None:
    """`- 対象リポジトリ:`行が無い場合は違反。"""
    content = "# タイトル\n\n## 背景\n\n### 計画メタ情報\n\n- 起動経路: process-feedbacks経由\n\n## 対応方針\n"
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "対象リポジトリ" in result.stderr


def test_meta_subsection_scoped_to_background(tmp_path: pathlib.Path) -> None:
    """`### 計画メタ情報`が`## 背景`外に存在しても検出しない（欠落扱いとして報告する）。"""
    content = (
        "# タイトル\n\n## 背景\n\n### 経緯\n\n動機の要約。\n\n"
        "## 対応方針\n\n### 計画メタ情報\n\n"
        "- 起動経路: process-feedbacks経由\n- 対象リポジトリ: `~/dotfiles`\n"
    )
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "plan-meta-missing" in result.stderr
