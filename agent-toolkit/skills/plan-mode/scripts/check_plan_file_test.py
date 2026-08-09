"""意味契約中心の計画検査を検証する。"""

import pathlib
import subprocess
import sys

import check_plan_file
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position


def _git(repo: pathlib.Path, *args: str) -> str:
    """テスト用リポジトリでgitを実行して標準出力を返す。"""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """各Git object typeの対象を持つGitリポジトリを作成する。"""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "existing.py").write_text("old\n", encoding="utf-8")
    (tmp_path / "old.py").write_text("remove\n", encoding="utf-8")
    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "nested.py").write_text("nested\n", encoding="utf-8")
    _git(tmp_path, "add", "existing.py", "old.py", "directory/nested.py")
    _git(tmp_path, "commit", "-qm", "files")
    blob = _git(tmp_path, "rev-parse", "HEAD:existing.py")
    _git(tmp_path, "update-index", "--add", "--cacheinfo", f"120000,{blob},linked.py")
    _git(tmp_path, "update-index", "--add", "--cacheinfo", f"160000,{'f' * 40},module")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def _rows_table(rows: tuple[str, ...], filler: str) -> str:
    """行名を固定した2列表を組み立てる。"""
    return "\n".join(["| 項目 | 内容 |", "| --- | --- |", *(f"| {row} | {filler} |" for row in rows)])


def _plan(base: str, *, bug: bool = False) -> str:
    """共有構造モジュールの定数から固定構造を満たす計画本文を組み立てる。"""
    work_type = "バグ対応" if bug else "通常変更"
    metadata = "\n".join(
        f"- {field}: {value}"
        for field, value in zip(
            _plan_format.PLAN_METADATA_FIELDS,
            ("`agent-toolkit:plan-mode`", "`/repo`", work_type, f"`{base}`"),
            strict=True,
        )
    )
    sections = [
        "# 計画の主題",
        "## 変更履歴",
        "| ID | 起点 | 指摘内容 | 採否・現在の結論 | 同期先 |\n| --- | --- | --- | --- | --- |\n"
        "| H-001 | 方針転換 | 初版の書式指定 | 固定構造で起草した。 | `目的` |",
        "## 目的",
        "### 概要",
        "成果を得る。",
        "### 計画メタ情報",
        metadata,
        "### 提示素材",
        "P-001:",
        "```text\n対象を更新してほしい。\n```",
        "### ユーザー合意済み事項",
        "| 合意事項 | 適用範囲 | 原文参照 |\n| --- | --- | --- |\n| 対象を更新する | 対象ファイルだけ | P-001 |",
    ]
    if bug:
        sections += [
            "## バグ調査結果",
            "### バグ調査結果: 対象の陳腐化",
            _rows_table(_plan_format.PLAN_BUG_TABLE_ROWS, "発生条件と実際値を含めて記述する。"),
        ]
    sections += [
        "## 対応方針",
        "### 実施内容",
        "| 実施内容 | ユーザー指示との関係 | 根拠 |\n| --- | --- | --- |\n"
        "| 対象を更新する | 指示どおり | P-001が更新を求めている。 |",
        "### 恒久化・リファクタリング内容",
        "#### 恒久化",
        "第1表の3処置を正本として参照する。"
        if bug
        else _rows_table(_plan_format.PLAN_PERMANENCE_TABLE_ROWS, "検討結果を記述する。"),
        "#### リファクタリング",
        _rows_table(_plan_format.PLAN_REFACTORING_TABLE_ROWS, "検討結果を記述する。"),
        "#### 類似見直し",
        _rows_table(_plan_format.PLAN_SIMILAR_REVIEW_TABLE_ROWS, "検討結果を記述する。"),
        "## 実装契約",
        "### 対象ファイル一覧",
        "- `existing.py`\n- `new.py`（新設）\n- `old.py`（削除）",
        "共通変更説明だけで実装を特定する。",
        "## 完了条件",
        "検証が成功する。",
        "## 進捗ログ",
        "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n| 2026-08-09 12:00 | 初版起草 | レビュー待ち。 |",
    ]
    return "\n\n".join(sections) + "\n"


def _check(repo: pathlib.Path, base: str, content: str, *, compare_diff: bool = False) -> tuple[list[str], list[str]]:
    """計画を一時ファイルへ保存して検査する。"""
    path = repo / "plan.md"
    normalized_content = content.replace("- 対象リポジトリ: `/repo`", f"- 対象リポジトリ: `{repo.resolve()}`")
    path.write_text(normalized_content, encoding="utf-8")
    return check_plan_file.check(path, repo, base if compare_diff else None)


def test_accepts_canonical_plan_with_free_implementer_region(repo: tuple[pathlib.Path, str]) -> None:
    """固定領域を満たし実装者向け領域を自由構成にした計画を受理する。"""
    work_dir, base = repo
    content = _plan(base).replace("## 完了条件", "## 任意の説明\n\n補足。\n\n## 完了条件")
    errors, warnings = _check(work_dir, base, content)
    assert not errors
    assert not warnings


def test_accepts_canonical_bug_plan(repo: tuple[pathlib.Path, str]) -> None:
    """固定14行の調査表を持つバグ対応計画を受理する。"""
    work_dir, base = repo
    errors, warnings = _check(work_dir, base, _plan(base, bug=True))
    assert not errors
    assert not warnings


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("# 計画の主題\n\n", ""), "ATX H1が1件必要"),
        (("## 変更履歴", "## 前書き\n\n補足。\n\n## 変更履歴"), "人間向け固定領域のH2"),
        (("## 対応方針\n", "## 対応方法\n"), "固定H2`## 対応方針`は1件必要"),
        (
            (
                "| 2026-08-09 12:00 | 初版起草 | レビュー待ち。 |\n",
                "| 2026-08-09 12:00 | 初版起草 | レビュー待ち。 |\n\n## 後書き\n\n補足。\n",
            ),
            "最後のH2にする",
        ),
        (("| 日時 | 完了した工程 | 結果・特記事項 |", "| 日時 | 工程 | 結果 |"), "`## 進捗ログ`は"),
        (("### 計画メタ情報", "### メタ情報"), "`### 計画メタ情報`は1件必要"),
        (("- 起動経路: `agent-toolkit:plan-mode`\n", ""), "この順序で1行ずつ置く"),
        (("### 対象ファイル一覧", "### 変更対象"), "`### 対象ファイル一覧`は1件必要"),
        (("| 対象を更新する | 指示どおり |", "| 対象を更新する | 対応追加 |"), "`ユーザー指示との関係`は"),
        (
            ("| 対象を更新する | 対象ファイルだけ | P-001 |", "| 対象を更新する | 対象ファイルだけ | P-900 |"),
            "原文参照が提示素材に無い",
        ),
        (("| 母集団 | 検討結果を記述する。 |", "| 対象 | 検討結果を記述する。 |"), "3行表を置く"),
    ],
)
def test_rejects_fixed_region_violations(repo: tuple[pathlib.Path, str], mutation: tuple[str, str], message: str) -> None:
    """固定領域の欠落、順序違反、追加H2、表違反を個別に拒否する。"""
    work_dir, base = repo
    content = _plan(base).replace(*mutation, 1)
    errors, _ = _check(work_dir, base, content)
    assert any(message in error for error in errors), errors


def test_rejects_duplicate_fixed_h2(repo: tuple[pathlib.Path, str]) -> None:
    """固定H2の重複を拒否する。"""
    work_dir, base = repo
    content = _plan(base) + "\n## 目的\n\n重複。\n"
    errors, _ = _check(work_dir, base, content)
    assert any("固定H2`## 目的`は1件必要" in error for error in errors)


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


@pytest.mark.parametrize(
    ("path", "object_type", "is_allowed"),
    [
        ("existing.py", "blob", True),
        ("linked.py", "blob", True),
        ("module", "commit", True),
        ("directory", "tree", False),
    ],
)
def test_target_git_object_type_boundary(
    repo: tuple[pathlib.Path, str], path: str, object_type: str, *, is_allowed: bool
) -> None:
    """fileとsymlinkのblobおよびgitlinkのcommitを受理し、directoryのtreeを拒否する。"""
    work_dir, base = repo
    original = "- `existing.py`\n- `new.py`（新設）\n- `old.py`（削除）"
    errors, _ = _check(work_dir, base, _plan(base).replace(original, f"- `{path}`"))
    message = f"既存対象が基準コミット上のファイルまたはgitlinkではない: {path} (object type={object_type})"
    if is_allowed:
        assert not errors
    else:
        assert message in errors


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


def test_rejects_missing_agent_reference(repo: tuple[pathlib.Path, str]) -> None:
    """実在しない専用agentの参照を拒否する。"""
    work_dir, base = repo
    content = _plan(base).replace("共通変更説明", "Agentツールで`missing-agent`を使う。")
    errors, _ = _check(work_dir, base, content)
    assert any("実在しないサブエージェント参照" in error for error in errors)


@pytest.mark.parametrize(
    ("invocation", "missing_reference"),
    [
        ("スキル`missing-prefix`を起動する。", "missing-prefix"),
        ("スキル `missing-prefix-space`を起動する。", "missing-prefix-space"),
        ("Skillツールで`missing-tool`を起動する。", "missing-tool"),
        ("Skillツールで `missing-tool-space`を起動する。", "missing-tool-space"),
        ("`missing-suffix`スキルを起動する。", "missing-suffix"),
        ("`missing-suffix-space` スキルを起動する。", "missing-suffix-space"),
        ("`agent-toolkit:plan-mode`を起動する。", None),
        ("`agent-toolkit:missing` を起動する。", "agent-toolkit:missing"),
        ("スキル`other:plan-mode`を起動する。", "other:plan-mode"),
        ("スキル `other:plan-mode`を起動する。", "other:plan-mode"),
        ("`uv`を起動する。", None),
        ("`uv` を起動する。", None),
    ],
)
def test_skill_reference_classification(repo: tuple[pathlib.Path, str], invocation: str, missing_reference: str | None) -> None:
    """明示標識、namespace、通常CLIの分類境界を検証する。"""
    work_dir, base = repo
    content = _plan(base).replace("共通変更説明", invocation)
    errors, _ = _check(work_dir, base, content)
    if missing_reference is None:
        assert not errors
    else:
        assert any(f"実在しないスキル参照: {missing_reference}" in error for error in errors)


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
    assert any(f"対象ファイル一覧と{base}..HEADのコミット済み差分が一致しない" in error for error in errors)
    assert any("未コミットの作業ツリー差分は照合対象外" in error for error in errors)
    assert not warnings


def test_base_commit_ignores_uncommitted_changes(repo: tuple[pathlib.Path, str]) -> None:
    """未コミットの作業ツリー差分は照合対象に含めない。"""
    work_dir, base = repo
    (work_dir / "existing.py").write_text("new\n", encoding="utf-8")
    errors, warnings = _check(work_dir, base, _plan(base), compare_diff=True)
    assert any("対象ファイル一覧と" in error for error in errors)
    assert all("existing.py']" not in error.rsplit("実差分=", maxsplit=1)[-1] for error in errors)
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
