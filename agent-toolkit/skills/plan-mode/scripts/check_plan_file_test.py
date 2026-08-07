"""意味契約中心の計画検査を検証する。"""

import pathlib
import subprocess

import check_plan_file
import pytest


def _git(repo: pathlib.Path, *args: str) -> str:
    """テスト用リポジトリでgitを実行して標準出力を返す。"""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """既存対象と削除対象を持つGitリポジトリを作成する。"""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "existing.py").write_text("old\n", encoding="utf-8")
    (tmp_path / "old.py").write_text("remove\n", encoding="utf-8")
    _git(tmp_path, "add", "existing.py", "old.py")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def _plan(base: str) -> str:
    """新しい最小契約を満たす計画を返す。"""
    return f"""## 目的

成果を得る。

## 実装契約

### 計画メタ情報

- 対象リポジトリ: `/repo`
- ベースコミット: `{base}`
- 作業種別: 通常変更

### 対象ファイル一覧

- `existing.py`
- `new.py`（新設）
- `old.py`（削除）

共通変更説明だけで実装を特定する。

## 完了条件

検証が成功する。

## 進捗ログ

未着手。
"""


def _check(repo: pathlib.Path, base: str, content: str, *, compare_diff: bool = False) -> tuple[list[str], list[str]]:
    """計画を一時ファイルへ保存して検査する。"""
    path = repo / "plan.md"
    normalized_content = content.replace("- 対象リポジトリ: `/repo`", f"- 対象リポジトリ: `{repo.resolve()}`")
    path.write_text(normalized_content, encoding="utf-8")
    return check_plan_file.check(path, repo, base if compare_diff else None)


def test_accepts_free_form_contract_without_h1_table_or_code_block(repo: tuple[pathlib.Path, str]) -> None:
    """追加H2と自由記述を含む最小契約を受理する。"""
    work_dir, base = repo
    content = _plan(base).replace("## 完了条件", "## 任意の説明\n\n補足。\n\n## 完了条件")
    errors, warnings = _check(work_dir, base, content)
    assert not errors
    assert not warnings


@pytest.mark.parametrize("anchor", ["目的", "実装契約", "完了条件", "進捗ログ"])
def test_rejects_missing_anchor(repo: tuple[pathlib.Path, str], anchor: str) -> None:
    """各意味アンカーの欠落を拒否する。"""
    work_dir, base = repo
    content = _plan(base).replace(f"## {anchor}", f"## 欠落-{anchor}", 1)
    errors, _ = _check(work_dir, base, content)
    assert any("missing required H2" in error for error in errors)


def test_rejects_duplicate_anchor(repo: tuple[pathlib.Path, str]) -> None:
    """意味アンカーの重複を拒否する。"""
    work_dir, base = repo
    content = _plan(base) + "\n## 目的\n\n重複。\n"
    errors, _ = _check(work_dir, base, content)
    assert any("must be unique" in error for error in errors)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("", "対象ファイル一覧が空"),
        ("- `existing.py`\n- `existing.py`", "重複したパス"),
        ("- `/absolute.py`", "危険なパス"),
        ("- `../outside.py`", "危険なパス"),
        ("- `existing.py`\n- [ ] `hidden.py`", "契約形式と一致しない箇条書き"),
    ],
)
def test_rejects_invalid_target_list(repo: tuple[pathlib.Path, str], replacement: str, message: str) -> None:
    """空、重複、危険パスの対象一覧を拒否する。"""
    work_dir, base = repo
    original = "- `existing.py`\n- `new.py`（新設）\n- `old.py`（削除）"
    errors, _ = _check(work_dir, base, _plan(base).replace(original, replacement))
    assert any(message in error for error in errors)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("- `existing.py`（新設）", "新設対象が基準コミットに実在"),
        ("- `missing.py`", "既存対象が基準コミットに実在しない"),
        ("- `missing.py`（削除）", "削除対象が基準コミットに実在しない"),
    ],
)
def test_rejects_base_state_mismatch(repo: tuple[pathlib.Path, str], replacement: str, message: str) -> None:
    """基準コミット上の対象状態と注記の矛盾を拒否する。"""
    work_dir, base = repo
    content = _plan(base).replace("- `existing.py`", replacement, 1)
    errors, _ = _check(work_dir, base, content)
    assert any(message in error for error in errors)


def test_rejects_unknown_base_commit_even_when_all_targets_are_new(repo: tuple[pathlib.Path, str]) -> None:
    """新設対象だけでもリポジトリに存在しないベースコミットを拒否する。"""
    work_dir, base = repo
    content = (
        _plan(base)
        .replace(
            "- `existing.py`\n- `new.py`（新設）\n- `old.py`（削除）",
            "- `new.py`（新設）",
        )
        .replace(base, "f" * 40)
    )
    errors, _ = _check(work_dir, base, content)
    assert any("ベースコミットを解決できない" in error for error in errors)


def test_rejects_target_repo_mismatched_with_worktree(repo: tuple[pathlib.Path, str]) -> None:
    """宣言リポジトリと作業ディレクトリのGitルートが異なる計画を拒否する。"""
    work_dir, base = repo
    content = _plan(base).replace("- 対象リポジトリ: `/repo`", "- 対象リポジトリ: `/other`")
    errors, _ = _check(work_dir, base, content)
    assert any("対象リポジトリが作業ディレクトリのGitルートと一致しない" in error for error in errors)


def test_accepts_relative_target_repo_matching_worktree(repo: tuple[pathlib.Path, str]) -> None:
    """相対表記の対象リポジトリを正規化してGitルートと照合する。"""
    work_dir, base = repo
    content = _plan(base).replace("- 対象リポジトリ: `/repo`", "- 対象リポジトリ: `.`")
    errors, _ = _check(work_dir, base, content)
    assert not errors


def test_rejects_unclosed_fence(repo: tuple[pathlib.Path, str]) -> None:
    """未閉鎖フェンスを拒否する。"""
    work_dir, base = repo
    errors, _ = _check(work_dir, base, _plan(base) + "\n```text\nopen\n")
    assert "閉じていないMarkdownフェンスがある" in errors


def test_rejects_missing_skill_and_agent_references(repo: tuple[pathlib.Path, str]) -> None:
    """実在しないスキルと専用agentの参照を拒否する。"""
    work_dir, base = repo
    content = _plan(base).replace("共通変更説明", "スキル`missing-skill`とAgentツールで`missing-agent`を使う。")
    errors, _ = _check(work_dir, base, content)
    assert any("実在しないスキル参照" in error for error in errors)
    assert any("実在しないサブエージェント参照" in error for error in errors)


def test_rejects_missing_skill_in_direct_invocation_form(repo: tuple[pathlib.Path, str]) -> None:
    """正規文書が使う直接起動表記でも不存在を検出する。"""
    work_dir, base = repo
    content = _plan(base).replace("共通変更説明", "`agent-toolkit:missing-skill`を起動する。")
    errors, _ = _check(work_dir, base, content)
    assert any("実在しないスキル参照: agent-toolkit:missing-skill" in error for error in errors)


def test_resolves_plugin_resources_outside_plugin_worktree(repo: tuple[pathlib.Path, str]) -> None:
    """利用先worktreeに複製されないplugin同梱resourceをplugin rootから解決する。"""
    work_dir, base = repo
    content = _plan(base).replace(
        "共通変更説明",
        "`agent-toolkit:plan-mode`を起動し、Agentツールで`agent-toolkit:plan-impl-executor`を起動する。",
    )
    errors, _ = _check(work_dir, base, content)
    assert not errors


def test_resolves_project_local_skill_from_worktree(repo: tuple[pathlib.Path, str]) -> None:
    """プロジェクトローカルskillは利用先worktreeから解決する。"""
    work_dir, base = repo
    skill = work_dir / ".claude" / "skills" / "local-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# local\n", encoding="utf-8")
    content = _plan(base).replace("共通変更説明", "スキル`local-skill`を起動する。")
    errors, _ = _check(work_dir, base, content)
    assert not errors


def test_base_commit_diff_mismatch_is_error(repo: tuple[pathlib.Path, str]) -> None:
    """実装後の対象一覧と実差分の不一致をエラーにする。"""
    work_dir, base = repo
    (work_dir / "existing.py").write_text("new\n", encoding="utf-8")
    _git(work_dir, "add", "existing.py")
    _git(work_dir, "commit", "-qm", "change")
    errors, warnings = _check(work_dir, base, _plan(base), compare_diff=True)
    assert any("対象ファイル一覧と実変更ファイルが一致しない" in error for error in errors)
    assert not warnings


def test_base_commit_diff_match_succeeds(repo: tuple[pathlib.Path, str]) -> None:
    """実装後の実差分が対象一覧と一致すれば成功する。"""
    work_dir, base = repo
    (work_dir / "existing.py").write_text("new\n", encoding="utf-8")
    (work_dir / "new.py").write_text("new\n", encoding="utf-8")
    (work_dir / "old.py").unlink()
    _git(work_dir, "add", "existing.py", "new.py", "old.py")
    _git(work_dir, "commit", "-qm", "change")
    errors, warnings = _check(work_dir, base, _plan(base), compare_diff=True)
    assert not errors
    assert not warnings
