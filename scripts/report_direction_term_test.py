"""報告の向きを示さない語をGit追跡ファイルから排除するリポジトリ全体の契約テスト。

ユーザーへ結果を届ける工程は「ユーザーへの報告」、ユーザーから寄せられた報告は「ユーザーからの報告」と書く。
「ユーザー」と「報告」を直結した語はどちらの向きにも読めるため使わない。
定義を書く`agent-toolkit/rules/01-agent.md`とその生成物だけが、禁止する語そのものを持つ。
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

pytestmark = pytest.mark.repo_invariant

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 本ファイル自身を検出しないよう、禁止する語を2つの部分文字列の連結で持つ。
# Unicodeエスケープは整形処理がリテラルへ戻すため使わない。
FORBIDDEN_TERM = "ユーザー" + "報告"

ALLOWED_PATHS = (
    "agent-toolkit/rules/01-agent.md",
    ".chezmoi-source/dot_codex/AGENTS.md",
    "scripts/report_direction_term_test.py",
)


def _find_violations(repo_root: pathlib.Path) -> list[str]:
    """Git追跡ファイルのうち禁止する語を含む行を`<パス>:<行番号>`で返す。"""
    pathspecs = ["."] + [f":(exclude){path}" for path in ALLOWED_PATHS]
    result = subprocess.run(
        ["git", "grep", "-n", "-I", "--no-color", "-F", "-e", FORBIDDEN_TERM, "--", *pathspecs],
        cwd=repo_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    # git grepは一致なしを終了コード1で表す。2以上は検索自体の失敗である。
    assert result.returncode in (0, 1), result.stderr
    return [":".join(line.split(":", 2)[:2]) for line in result.stdout.splitlines()]


def test_forbidden_term_is_detected_outside_allowed_paths(tmp_path: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text(f"前置き\n2026年の{FORBIDDEN_TERM}に由来する\n", encoding="utf-8")
    (tmp_path / "docs" / "clean.md").write_text("ユーザーへの報告とユーザーからの報告\n", encoding="utf-8")
    allowed = tmp_path / "agent-toolkit" / "rules" / "01-agent.md"
    allowed.parent.mkdir(parents=True)
    allowed.write_text(f"「{FORBIDDEN_TERM}」の語は使わない\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

    assert _find_violations(tmp_path) == ["docs/note.md:2"]


def test_tracked_files_do_not_use_direction_less_report_term() -> None:
    assert not _find_violations(REPO_ROOT)
