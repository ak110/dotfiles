"""atk (agent-toolkit `atk mq`) のadopt/reject/rm/edit・パストラバーサル検証のテスト。

adopt・reject・rm・editサブコマンドと、ファイル名引数の不正値拒否の単体テストを集約する。
既存サブコマンドの残テストは`atk_test.py`に、他サブコマンドの分割先は`_atk_mq_list_test.py`・
`_atk_mq_show_test.py`・`_atk_mq_process_loop_test.py`に分離する。
位置引数の重複除去（FB7）テストは`too-many-lines`回避のため`_atk_mq_dedup_test.py`へ分離する。
共通ヘルパーは`atk_test.py`から再利用する。
"""

import argparse
import contextlib
import datetime
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_common as common  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_frontmatter as frontmatter_parser  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_mutations as mutations  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_tbd as tbd  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_feedback_file,
)  # noqa: E402  # pylint: disable=wrong-import-position


def _write_tbd_entry(
    notes: pathlib.Path,
    filename: str,
    *,
    question: str = "変更前の質問",
    answer: str = "既存回答",
    frontmatter: str = "target_repo: github.com/example/foo\ntype: tbd\nquestion_type: free-form",
) -> pathlib.Path:
    """非対話edit用のTBDエントリを書き込む。"""
    path = notes / "inbox" / filename
    path.write_text(
        f"---\n{frontmatter}\n---\n\n"
        f"{tbd.QUESTION_HEADING}\n\n{question}\n\n"
        f"{tbd.ANSWER_HEADING}\n\n{tbd.ANSWER_MARKER}\n{answer}\n",
        encoding="utf-8",
    )
    return path


def _disable_transition_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """状態遷移テストからprivate-notesのgit操作を除外する。"""
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)


def test_add_empty_feedback_keeps_detailed_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実質空フィードバックのCLI拒否案内に判定条件と対象先頭を含める。"""
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "add", "--target-repo", "github.com/example/foo", "-"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "空文字・空白のみ・箇条書きマーカー単独文字" in captured.err
    assert "該当メッセージの先頭: -" in captured.err


def test_flat_feedback_operations_are_public(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """平引数遷移が戻り値とファイル移動を一貫して反映する。"""
    notes = _setup_notes(tmp_path)
    _write_feedback_file(notes, "entry.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    filenames = mutations.transition_entries(
        notes,
        action="start-processing",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )
    assert filenames == ["entry.md"]
    assert (notes / "processing/entry.md").is_file()


def test_hold_and_unhold_reuse_standard_transition(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """holdとunholdは専用復旧状態を作成せず既存の状態遷移で往復する。"""
    notes = _setup_notes(tmp_path)
    _write_feedback_file(notes, "entry.md")
    _disable_transition_git(monkeypatch)

    mutations.transition_entries(notes, action="hold", filenames=["entry.md"], now=_FIXED_DT)
    assert (notes / "hold/entry.md").is_file()

    mutations.transition_entries(notes, action="unhold", filenames=["entry.md"], now=_FIXED_DT)
    assert (notes / "inbox/entry.md").is_file()


def test_transition_restores_missing_state_directories_before_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任意状態ディレクトリが不在でも全状態をgit addできる状態へ戻す。"""
    notes = _setup_notes(tmp_path)
    (notes / "planning").rmdir()
    _write_feedback_file(notes, "entry.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)

    def assert_state_paths_exist(_notes: pathlib.Path, _message: str, paths: list[str], **_kwargs: object) -> None:
        assert all((_notes / path).is_dir() for path in paths)

    monkeypatch.setattr(mutations, "_commit_and_push", assert_state_paths_exist)

    mutations.transition_entries(
        notes,
        action="start-processing",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )

    assert (notes / "processing/entry.md").is_file()


class TestCommitResolution:
    """採否結果へ記録するrevisionの解決境界を検証する。"""

    @pytest.mark.parametrize(
        ("action", "destination"),
        [("adopt", "adopted"), ("reject", "rejected")],
    )
    def test_url_target_cli_resolves_revision_from_current_worktree(
        self,
        action: str,
        destination: str,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """URL形の対象指定でも現在位置の対応作業ツリーでrevisionを完全OIDへ解決する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "feedback.md")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        monkeypatch.chdir(worktree)
        _disable_transition_git(monkeypatch)
        full_oid = "a" * 40

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(cmd, 0, str(worktree) + "\n", "")
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/foo.git\n", "")
            if "rev-parse" in cmd:
                return subprocess.CompletedProcess(cmd, 0, full_oid + "\n", "")
            raise AssertionError(cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    action,
                    "feedback.md",
                    "--commit=abcdef1",
                    "--target-repo=github.com/example/foo",
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0

        result = (notes / destination / "feedback.md").read_text(encoding="utf-8")
        assert f"- 対応commit: {full_oid}" in result

    def test_matching_worktree_records_full_oid(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """対応作業ツリーでは短縮revisionを完全OIDへ解決する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "feedback.md")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _disable_transition_git(monkeypatch)
        full_oid = "a" * 40

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/foo.git\n", "")
            if "rev-parse" in cmd:
                return subprocess.CompletedProcess(cmd, 0, full_oid + "\n", "")
            raise AssertionError(cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)
        mutations.transition_entries(
            notes,
            action="adopt",
            filenames=["feedback.md"],
            now=_FIXED_DT,
            commit="abcdef1",
            local_worktree=worktree,
        )
        assert f"- 対応commit: {full_oid}" in (notes / "adopted/feedback.md").read_text(encoding="utf-8")

    @pytest.mark.parametrize("revision", ["missing", "--not-an-option", "blob"])
    def test_invalid_revision_stops_before_mutation(
        self,
        revision: str,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """不在、オプション様、commit以外のrevisionは状態変更前に拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "feedback.md")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _disable_transition_git(monkeypatch)
        calls: list[str] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/foo.git\n", "")
            assert cmd[-2] == "--end-of-options"
            return subprocess.CompletedProcess(cmd, 1, "", "invalid")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(mutations, "_stamp_result", lambda *_args, **_kwargs: calls.append("stamp"))
        with pytest.raises(SystemExit) as exc_info:
            mutations.transition_entries(
                notes,
                action="reject",
                filenames=["feedback.md"],
                now=_FIXED_DT,
                commit=revision,
                local_worktree=worktree,
            )
        assert exc_info.value.code == 2
        assert not calls
        assert path.is_file()
        assert not (notes / "rejected/feedback.md").exists()

    def test_multiple_repositories_record_values_per_entry(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """一致する群だけを完全OID化し、他群は警告後に指定値を保つ。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "foo.md", target_repo="github.com/example/foo")
        _write_feedback_file(notes, "bar.md", target_repo="github.com/example/bar")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _disable_transition_git(monkeypatch)
        full_oid = "b" * 40

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/foo.git\n", "")
            return subprocess.CompletedProcess(cmd, 0, full_oid + "\n", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        mutations.transition_entries(
            notes,
            action="adopt",
            filenames=["foo.md", "bar.md"],
            now=_FIXED_DT,
            commit="abcdef1",
            local_worktree=worktree,
        )
        assert f"- 対応commit: {full_oid}" in (notes / "adopted/foo.md").read_text(encoding="utf-8")
        assert "- 対応commit: abcdef1" in (notes / "adopted/bar.md").read_text(encoding="utf-8")
        assert "github.com/example/bar" in capsys.readouterr().err


def test_remove_targets_explicit_state_and_keeps_legacy_priority(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態指定時は指定側を削除し、省略時はprocessing優先を維持する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    content = "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n本文\n"
    inbox = notes / "inbox/same.md"
    processing = notes / "processing/same.md"
    processing.parent.mkdir()
    inbox.write_text(content, encoding="utf-8")
    processing.write_text(content, encoding="utf-8")

    removed = mutations.transition_entries(
        notes,
        action="remove",
        filenames=["same.md"],
        now=_FIXED_DT,
        state="inbox",
        expected_content=content,
    )
    assert removed == ["same.md"]
    assert not inbox.exists()
    assert processing.read_text(encoding="utf-8") == content

    inbox.write_text(content, encoding="utf-8")
    removed = mutations.transition_entries(
        notes,
        action="remove",
        filenames=["same.md"],
        now=_FIXED_DT,
        force=True,
    )
    assert removed == ["same.md"]
    assert inbox.exists()
    assert not processing.exists()


def test_remove_rejects_changed_and_unreadable_expected_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """確認後の内容変更とUTF-8読取り不能を競合として削除しない。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    original = "---\ntype: feedback\n---\n\n確認時本文\n"

    changed = notes / "inbox/changed.md"
    changed.write_text(original.replace("確認時", "外部更新後"), encoding="utf-8")
    with pytest.raises(RuntimeError, match="編集中に他プロセスが対象を変更しました"):
        mutations.transition_entries(
            notes,
            action="remove",
            filenames=[changed.name],
            now=_FIXED_DT,
            state="inbox",
            expected_content=original,
        )
    assert changed.exists()

    pull_changed = notes / "inbox/pull-changed.md"
    pull_changed.write_text(original, encoding="utf-8")

    def update_during_pull(_path: pathlib.Path) -> None:
        pull_changed.write_text(original.replace("確認時", "pull後"), encoding="utf-8")

    monkeypatch.setattr(mutations, "_pull", update_during_pull)
    with pytest.raises(RuntimeError, match="編集中に他プロセスが対象を変更しました"):
        mutations.transition_entries(
            notes,
            action="remove",
            filenames=[pull_changed.name],
            now=_FIXED_DT,
            state="inbox",
            expected_content=original,
        )
    assert pull_changed.read_text(encoding="utf-8").endswith("pull後本文\n")

    unreadable = notes / "inbox/unreadable.md"
    unreadable.write_bytes(b"\xff")
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    with pytest.raises(RuntimeError, match="編集中に他プロセスが対象を変更しました"):
        mutations.transition_entries(
            notes,
            action="remove",
            filenames=[unreadable.name],
            now=_FIXED_DT,
            state="inbox",
            expected_content=original,
        )
    assert unreadable.exists()


def _write_convert_plan(tmp_path: pathlib.Path, target_commit: str) -> pathlib.Path:
    """変換テスト用の計画ファイルを作成する。"""
    plan = tmp_path / "plan.md"
    plan.write_text(
        f"# 計画\n\n## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{target_commit}`\n",
        encoding="utf-8",
    )
    return plan


def _write_planning_plan(
    tmp_path: pathlib.Path,
    target_commit: str,
    filenames: tuple[str, ...],
) -> pathlib.Path:
    """計画型変換テスト用にメタ情報と提示素材を持つ計画を作成する。"""
    plan = tmp_path / "plan.md"
    materials = "".join(f"- {filename}\n" for filename in filenames)
    plan.write_text(
        f"# 計画\n\n## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{target_commit}`\n\n## 提示素材\n\n{materials}",
        encoding="utf-8",
    )
    return plan


def _write_convert_feedback(
    notes: pathlib.Path,
    filename: str,
    *,
    entry_type: str = "feedback",
    state: str = "inbox",
    target_repo: str = "github.com/example/foo",
    target_commit: str = "a" * 40,
    schedule_mapping: str = "",
) -> pathlib.Path:
    """変換テスト用のエントリを書き込む。"""
    directory = notes / state
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(
        f"---\ntarget_repo: {target_repo}\ntype: {entry_type}\ntarget_commit: {target_commit}\n{schedule_mapping}---\n\n本文\n",
        encoding="utf-8",
    )
    return path


def _patch_planning_target_resolution(monkeypatch: pytest.MonkeyPatch, target_commit: str = "b" * 40) -> None:
    """planning変換テストの対象worktreeと計画ベースcommit解決を固定する。"""
    monkeypatch.setattr(mutations, "_local_worktree_repo_id", lambda _path: "github.com/example/foo")
    monkeypatch.setattr(mutations, "_resolve_plan_base_commit", lambda _plan, _worktree: target_commit)


def _disable_convert_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """変換テストでprivate-notesへのgit操作を無効化する。"""
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mutations, "_assert_conversion_worktree_clean", lambda _path: None)
    monkeypatch.setattr(mutations, "_assert_conversion_targets_tracked", lambda _path, _paths: None)
    monkeypatch.setattr(mutations, "_git_head", lambda _path: "a" * 40)


@pytest.mark.parametrize("state", ["inbox", "processing"])
def test_convert_to_plan_replaces_legacy_schedule_with_top_level_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """変換が状態を問わず計画・依存をトップレベルへ正規化する。"""
    notes = _setup_notes(tmp_path)
    schedule_mapping = (
        "queue_schedule:\n"
        "  body_sha256: stale\n"
        "  normalized_target_repo: github.com/example/foo\n"
        "  type: normal\n"
        "  dependency:\n"
        "    kind: none\n"
        "  target_files: []\n"
        "  carry_count: 2\n"
        "  carry_reasons: [limit-exceeded, conflict]\n"
        "  last_deferral_run_id: run-1\n"
        "  last_deferral_reason: conflict\n"
    )
    path = _write_convert_feedback(notes, "feedback.md", state=state, schedule_mapping=schedule_mapping)
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    details = mutations.convert_entry_to_plan(
        notes,
        filename="feedback.md",
        plan_file=str(plan),
        depends_on=("dependency.md", "dependency", "dependency.md"),
        target_repo="github.com/example/foo",
    )

    text = path.read_text(encoding="utf-8")
    parsed = frontmatter_parser.parse_frontmatter(text)
    assert parsed is not None
    data, _body = parsed
    assert data["plan_file"] == str(plan)
    assert data["depends_on"] == ["dependency.md"]
    assert "queue_schedule" not in data
    assert details["target_commit"] == "a" * 40
    assert details["depends_on"] == ["dependency.md"]


@pytest.mark.parametrize(
    ("dependency_args", "expected_dependencies"),
    [
        ((), ["predecessor.md"]),
        (("--depends-on", "replacement", "--depends-on", "replacement.md"), ["replacement.md"]),
    ],
)
def test_convert_to_plan_cli_distinguishes_omitted_and_explicit_dependencies(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency_args: tuple[str, ...],
    expected_dependencies: list[str],
) -> None:
    """変換CLIは依存の省略時に既存値を保持し、明示時だけ置換する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(
        notes,
        "feedback.md",
        schedule_mapping=(
            "queue_schedule:\n  dependency:\n    kind: external-user\n    condition: 回答後\n    tbd_filename: answer.md\n"
        ),
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: [predecessor.md]\n"),
        encoding="utf-8",
    )
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    with pytest.raises(SystemExit) as captured:
        atk.main(
            [
                "mq",
                "convert-to-plan",
                "feedback.md",
                "--plan-file",
                str(plan),
                "--target-repo",
                "github.com/example/foo",
                *dependency_args,
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert captured.value.code == 0
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["depends_on"] == expected_dependencies
    assert "queue_schedule" not in parsed[0]


@pytest.mark.parametrize("resolved_worktree", [pathlib.Path("/worktree"), None])
def test_convert_to_plan_cli_ignores_mismatched_plan_base(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    resolved_worktree: pathlib.Path | None,
) -> None:
    """計画作成時点の参照値と`target_commit`を比較せず変換を成立させる。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(notes, "feedback.md", target_commit="a" * 40)
    plan = _write_convert_plan(tmp_path, "b" * 40)
    _disable_convert_git(monkeypatch)
    resolved_targets: list[str | None] = []

    def resolve_target(value: str | None) -> tuple[str, pathlib.Path | None]:
        resolved_targets.append(value)
        return "github.com/example/foo", resolved_worktree

    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "resolve_add_target",
        resolve_target,
    )

    with pytest.raises(SystemExit) as captured:
        atk.main(
            [
                "mq",
                "convert-to-plan",
                "feedback.md",
                "--plan-file",
                str(plan),
                "--target-repo",
                "github.com/example/foo",
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert captured.value.code == 0
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["plan_file"] == str(plan)
    assert resolved_targets == ["github.com/example/foo"]


def test_convert_to_plan_migrates_legacy_entry_dependencies_when_omitted(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """依存指定を省略した変換は旧entries依存をトップレベルへ移行する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(
        notes,
        "feedback.md",
        schedule_mapping=(
            "queue_schedule:\n  dependency:\n    kind: entries\n    filenames: [predecessor.md, predecessor.md]\n"
        ),
    )
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    details = mutations.convert_entry_to_plan(notes, filename="feedback.md", plan_file=str(plan))

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["depends_on"] == ["predecessor.md"]
    assert "queue_schedule" not in parsed[0]
    assert details["depends_on"] == ["predecessor.md"]


def test_convert_to_plan_removes_legacy_none_dependency_when_omitted(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧none依存は依存なしの意味を保って旧scheduleだけを除去する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(
        notes,
        "feedback.md",
        schedule_mapping="queue_schedule:\n  dependency:\n    kind: none\n",
    )
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    mutations.convert_entry_to_plan(notes, filename="feedback.md", plan_file=str(plan))

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert "depends_on" not in parsed[0]
    assert "queue_schedule" not in parsed[0]


@pytest.mark.parametrize(
    "legacy_dependency",
    [
        "    kind: external-user\n    condition: 回答後\n    tbd_filename: answer.md\n",
        "    kind: entries\n    filenames: []\n",
    ],
)
def test_convert_to_plan_rejects_unrepresentable_legacy_dependency_when_omitted(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy_dependency: str,
) -> None:
    """トップレベルへ意味を保って移行できない旧依存は非破壊で拒否する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(
        notes,
        "feedback.md",
        schedule_mapping=f"queue_schedule:\n  dependency:\n{legacy_dependency}",
    )
    original = path.read_text(encoding="utf-8")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="旧形式の依存"):
        mutations.convert_entry_to_plan(notes, filename="feedback.md", plan_file=str(plan))

    assert path.read_text(encoding="utf-8") == original


def test_convert_to_plan_rejects_explicit_dependency_cycle(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変換時に依存を明示した場合は既存グラフへ閉路を形成しない。"""
    notes = _setup_notes(tmp_path)
    first = _write_convert_feedback(notes, "first.md")
    _write_convert_feedback(notes, "second.md")
    first.write_text(
        first.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: [second.md]\n"),
        encoding="utf-8",
    )
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="循環"):
        mutations.convert_entry_to_plan(
            notes,
            filename="second.md",
            plan_file=str(plan),
            depends_on=("first.md",),
        )


@pytest.mark.parametrize(
    ("plan_value", "expected"),
    [("relative.md", "絶対パス"), ("missing", "実在する通常ファイル")],
)
def test_convert_to_plan_rejects_invalid_plan(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    plan_value: str,
    expected: str,
) -> None:
    """相対パスと未存在の計画ファイルを拒否する。"""
    notes = _setup_notes(tmp_path)
    _write_convert_feedback(notes, "feedback.md")
    _disable_convert_git(monkeypatch)
    value = plan_value if plan_value == "relative.md" else str(tmp_path / plan_value)

    with pytest.raises(mutations.WebInputError, match=expected):
        mutations.convert_entry_to_plan(notes, filename="feedback.md", plan_file=value)


def test_convert_to_plan_rejects_tbd_repo_mismatch_and_self_dependency(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TBD、投入先不一致、自己依存をそれぞれ拒否する。"""
    notes = _setup_notes(tmp_path)
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)
    _write_convert_feedback(notes, "tbd.md", entry_type="tbd")
    with pytest.raises(mutations.WebInputError, match="フィードバックだけ"):
        mutations.convert_entry_to_plan(notes, filename="tbd.md", plan_file=str(plan))

    _write_convert_feedback(notes, "feedback.md")
    with pytest.raises(mutations.WebInputError, match="target_repoが一致しません"):
        mutations.convert_entry_to_plan(
            notes,
            filename="feedback.md",
            plan_file=str(plan),
            target_repo="github.com/example/other",
        )
    with pytest.raises(mutations.WebInputError, match="自分自身"):
        mutations.convert_entry_to_plan(
            notes,
            filename="feedback.md",
            plan_file=str(plan),
            depends_on=("feedback",),
        )


def test_set_dependencies_updates_normal_feedback_without_converting_plan(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通常フィードバックの型と本文を保ち、依存だけを正規化して更新する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(
        notes,
        "feedback.md",
        schedule_mapping="queue_schedule:\n  dependency:\n    kind: none\n",
    )
    _disable_convert_git(monkeypatch)

    details = mutations.set_entry_dependencies(
        notes,
        filename="feedback.md",
        depends_on=("dependency", "dependency.md"),
        target_repo="github.com/example/foo",
    )

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    data, body = parsed
    assert data["type"] == "feedback"
    assert "plan_file" not in data
    assert data["depends_on"] == ["dependency.md"]
    assert "queue_schedule" not in data
    assert "本文" in body
    assert details["depends_on"] == ["dependency.md"]


def test_set_dependencies_can_clear_dependencies(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """依存オプション省略時は既存の明示依存を解除する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(notes, "feedback.md")
    text = path.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: [old.md]\n")
    path.write_text(text, encoding="utf-8")
    _disable_convert_git(monkeypatch)

    mutations.set_entry_dependencies(notes, filename="feedback.md", depends_on=())

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert "depends_on" not in parsed[0]


@pytest.mark.parametrize(
    ("existing", "filename", "dependencies"),
    [
        (("first.md", "second.md"), "second.md", ("first.md",)),
        (("first.md", "second.md", "third.md"), "third.md", ("first.md",)),
    ],
)
def test_set_dependencies_rejects_mutual_and_existing_chain_cycles(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: tuple[str, ...],
    filename: str,
    dependencies: tuple[str, ...],
) -> None:
    """相互依存と既存長鎖へ閉路を形成する更新をlock内で拒否する。"""
    notes = _setup_notes(tmp_path)
    for name in existing:
        path = _write_convert_feedback(notes, name)
        if name == "first.md":
            path.write_text(
                path.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: [second.md]\n"),
                encoding="utf-8",
            )
        elif name == "second.md" and len(existing) == 3:
            path.write_text(
                path.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: [third.md]\n"),
                encoding="utf-8",
            )
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="循環"):
        mutations.set_entry_dependencies(notes, filename=filename, depends_on=dependencies)


def test_set_dependencies_uses_graph_refreshed_after_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pullで競合更新された依存を読んでから新しい閉路を拒否する。"""
    notes = _setup_notes(tmp_path)
    first = _write_convert_feedback(notes, "first.md")
    _write_convert_feedback(notes, "second.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)

    def pull_with_competing_update(_path: pathlib.Path) -> None:
        first.write_text(
            first.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: [second.md]\n"),
            encoding="utf-8",
        )

    monkeypatch.setattr(mutations, "_pull", pull_with_competing_update)

    with pytest.raises(mutations.WebInputError, match="循環"):
        mutations.set_entry_dependencies(notes, filename="second.md", depends_on=("first.md",))


def test_set_dependencies_cli_rejects_cycle(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """直接CLI呼び出しも循環拒否をユーザー向け終了状態へ変換する。"""
    notes = _setup_notes(tmp_path)
    first = _write_convert_feedback(notes, "first.md")
    _write_convert_feedback(notes, "second.md")
    first.write_text(
        first.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: [second.md]\n"),
        encoding="utf-8",
    )
    _disable_convert_git(monkeypatch)

    with pytest.raises(SystemExit) as captured:
        atk.main(
            ["mq", "set-dependencies", "second.md", "--depends-on", "first.md", "--target-repo", "github.com/example/foo"],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert captured.value.code == 1
    assert "循環する依存" in capsys.readouterr().err


def test_convert_to_plan_keeps_saved_change_when_push_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """commit後のpush失敗契約に従い、変換済みファイルを保持して例外を伝播する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(notes, "feedback.md")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)

    commit_calls: list[tuple[object, ...]] = []

    def fail_after_commit(*args: object, **_kwargs: object) -> None:
        commit_calls.append(args)
        raise subprocess.CalledProcessError(1, ["git", "push"])

    monkeypatch.setattr(mutations, "_commit_and_push", fail_after_commit)

    with pytest.raises(subprocess.CalledProcessError):
        mutations.convert_entry_to_plan(notes, filename="feedback.md", plan_file=str(plan))
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["plan_file"] == str(plan)
    assert commit_calls[0][2] == ("inbox/feedback.md",)

    push_calls: list[pathlib.Path] = []
    monkeypatch.setattr(
        mutations,
        "_commit_and_push",
        lambda *_args, **_kwargs: pytest.fail("再実行時に新規commitを作成してはならない"),
    )
    monkeypatch.setattr(mutations, "_push_pending_commits", push_calls.append)

    with pytest.raises(mutations.WebInputError, match="既に計画型"):
        mutations.convert_entry_to_plan(notes, filename="feedback.md", plan_file=str(plan))

    assert push_calls == [notes]


def test_convert_to_plan_pushes_pending_commit_before_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再実行時の未送信commitをff-only pullより先に同期する。"""
    notes = _setup_notes(tmp_path)
    _write_convert_feedback(notes, "feedback.md")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)
    events: list[str] = []
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: events.append("push"))
    monkeypatch.setattr(mutations, "_pull", lambda _path: events.append("pull"))
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)

    mutations.convert_entry_to_plan(notes, filename="feedback.md", plan_file=str(plan))

    assert events[:2] == ["push", "pull"]


def test_convert_multiple_entries_uses_one_commit_in_input_order(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """複数入力を指定順に更新し、1回のcommitへまとめる。"""
    notes = _setup_notes(tmp_path)
    paths = [_write_convert_feedback(notes, name) for name in ("second.md", "first.md")]
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))

    result = mutations.convert_entries_to_plan(
        notes,
        filenames=("second.md", "first.md"),
        plan_file=str(plan),
        skip_push=True,
    )

    assert commit_calls == [(notes, "chore: convert feedback items to plans", ("inbox/second.md", "inbox/first.md"))]
    entries = result["entries"]
    assert isinstance(entries, list)
    assert len(entries) == 2
    for path in paths:
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["plan_file"] == str(plan)


def test_convert_multiple_entries_rejects_duplicate_before_writing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重複入力を全体拒否し、対象本文を変更しない。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(notes, "feedback.md")
    original = path.read_text(encoding="utf-8")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="重複"):
        mutations.convert_entries_to_plan(
            notes,
            filenames=("feedback.md", "feedback"),
            plan_file=str(plan),
        )

    assert path.read_text(encoding="utf-8") == original


def test_convert_planning_entries_integrates_all_materials_into_oldest_inbox_item(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """planningの全feedbackを最古項目へ統合し、TBDと外部依存を保持する。"""
    notes = _setup_notes(tmp_path)
    feedback_names = ("20260827-000000-001.md", "20260827-000000-002.md")
    paths = [_write_convert_feedback(notes, name, state="planning") for name in feedback_names]
    tbd_name = "20260826-235959-001.md"
    _write_tbd_entry(notes, tbd_name)
    paths[0].write_text(
        paths[0]
        .read_text(encoding="utf-8")
        .replace(
            "type: feedback\n",
            f"type: feedback\ndepends_on: [{tbd_name}, external-a.md]\n",
        ),
        encoding="utf-8",
    )
    paths[1].write_text(
        paths[1]
        .read_text(encoding="utf-8")
        .replace(
            "type: feedback\n",
            "type: feedback\nqueue_schedule:\n  dependency:\n    kind: entries\n    filenames: [external-b.md]\n",
        ),
        encoding="utf-8",
    )
    plan = _write_planning_plan(tmp_path, "a" * 40, (*feedback_names, tbd_name))
    _disable_convert_git(monkeypatch)
    _patch_planning_target_resolution(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))

    result = mutations.convert_entries_to_plan(
        notes,
        filenames=(feedback_names[1], feedback_names[0]),
        plan_file=str(plan),
        message="統合した計画本文",
        target_repo="github.com/example/foo",
        local_worktree=tmp_path / "target-worktree",
        skip_push=True,
    )

    output_path = notes / "inbox" / feedback_names[0]
    assert not (notes / "planning" / feedback_names[0]).exists()
    assert not (notes / "planning" / feedback_names[1]).exists()
    assert output_path.is_file()
    assert (notes / "inbox" / tbd_name).is_file()
    parsed = frontmatter_parser.parse_frontmatter(output_path.read_text(encoding="utf-8"))
    assert parsed is not None
    data, body = parsed
    assert data["source"] == "plan"
    assert data["plan_file"] == str(plan)
    assert data["target_commit"] == "b" * 40
    assert data["depends_on"] == [tbd_name, "external-a.md", "external-b.md"]
    assert "統合した計画本文" in body
    entries = result["entries"]
    assert isinstance(entries, list) and len(entries) == 1
    assert result["planning"] is True
    assert len(commit_calls) == 1
    assert commit_calls[0][2] == (
        "planning/20260827-000000-001.md",
        "planning/20260827-000000-002.md",
        "inbox/20260827-000000-001.md",
    )


def test_convert_planning_entries_rejects_unrepresentable_legacy_dependency_before_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """planningの旧形式依存を移行できない場合は書込み前に拒否する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    path = _write_convert_feedback(notes, filename, state="planning")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            "type: feedback\nqueue_schedule:\n  dependency:\n    kind: external-user\n",
        ),
        encoding="utf-8",
    )
    original = path.read_text(encoding="utf-8")
    plan = _write_planning_plan(tmp_path, "a" * 40, (filename,))
    _disable_convert_git(monkeypatch)
    _patch_planning_target_resolution(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="旧形式の依存を計画実装型へ移行できません"):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(filename,),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    assert path.read_text(encoding="utf-8") == original
    assert not (notes / "inbox" / filename).exists()


@pytest.mark.parametrize(
    ("state", "message", "expected"),
    [
        ("planning", None, "--message"),
        ("inbox", "統合本文", "指定できません"),
    ],
)
def test_convert_to_plan_rejects_message_for_wrong_input_state_without_changes(
    state: str,
    message: str | None,
    expected: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態と本文入力の組合せを検証し、入力ファイルを変更しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    path = _write_convert_feedback(notes, filename, state=state)
    original = path.read_text(encoding="utf-8")
    material_names = (filename,) if state == "planning" else ()
    plan = (
        _write_planning_plan(tmp_path, "a" * 40, material_names) if material_names else _write_convert_plan(tmp_path, "a" * 40)
    )
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match=expected):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(filename,),
            plan_file=str(plan),
            message=message,
        )

    assert path.read_text(encoding="utf-8") == original
    assert not (notes / "inbox" / filename).exists() if state == "planning" else True


def test_convert_to_plan_rejects_mixed_states_before_writing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """inboxとplanningの入力混在を全体拒否する。"""
    notes = _setup_notes(tmp_path)
    first = _write_convert_feedback(notes, "20260827-000000-001.md")
    second = _write_convert_feedback(notes, "20260827-000000-002.md", state="planning")
    first_original = first.read_text(encoding="utf-8")
    second_original = second.read_text(encoding="utf-8")
    plan = _write_planning_plan(tmp_path, "a" * 40, (first.name, second.name))
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="異なる状態"):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(first.name, second.name),
            plan_file=str(plan),
            message="統合本文",
        )

    assert first.read_text(encoding="utf-8") == first_original
    assert second.read_text(encoding="utf-8") == second_original
    assert not (notes / "inbox" / second.name).exists()


def test_convert_planning_entries_rejects_material_mismatch_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI入力と計画素材の集合が異なる場合は書込み前に拒否する。"""
    notes = _setup_notes(tmp_path)
    first = _write_convert_feedback(notes, "20260827-000000-001.md", state="planning")
    second = _write_convert_feedback(notes, "20260827-000000-002.md", state="planning")
    originals = {path.name: path.read_text(encoding="utf-8") for path in (first, second)}
    plan = _write_planning_plan(tmp_path, "a" * 40, (first.name,))
    _disable_convert_git(monkeypatch)
    _patch_planning_target_resolution(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="一致しません"):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(first.name, second.name),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    assert first.read_text(encoding="utf-8") == originals[first.name]
    assert second.read_text(encoding="utf-8") == originals[second.name]
    assert not (notes / "inbox" / first.name).exists()


def test_convert_planning_entries_rejects_destination_conflict_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """統合先inboxの同名競合時はplanningを変更しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_convert_feedback(notes, filename, state="planning")
    original = source.read_text(encoding="utf-8")
    conflict = notes / "inbox" / filename
    conflict.write_text("---\ntype: feedback\n---\n\n競合本文\n", encoding="utf-8")
    conflict_original = conflict.read_text(encoding="utf-8")
    plan = _write_planning_plan(tmp_path, "a" * 40, (filename,))
    _disable_convert_git(monkeypatch)
    _patch_planning_target_resolution(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="同名"):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(filename,),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    assert source.read_text(encoding="utf-8") == original
    assert conflict.read_text(encoding="utf-8") == conflict_original


def _initialize_private_notes_git(notes: pathlib.Path) -> str:
    """変換失敗時の作業ツリーとindex復元を検証するGit管理repoを作成する。"""
    for state in common.MQ_STATES:
        (notes / state).mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(notes), "init", "--initial-branch=main"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "-C", str(notes), "add", "-A"], check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(notes),
            "-c",
            "user.email=agent-toolkit@test.invalid",
            "-c",
            "user.name=agent-toolkit-test",
            "commit",
            "-m",
            "test: initialize private notes",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "-C", str(notes), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _disable_real_convert_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """実Git変換テストでremote同期だけを無効化する。"""
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)


def test_convert_planning_entries_restores_all_paths_after_write_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """書込み失敗時にplanningの全入力とindexを開始時へ戻す。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_convert_feedback(notes, filename, state="planning")
    original = source.read_text(encoding="utf-8")
    start_head = _initialize_private_notes_git(notes)
    plan = _write_planning_plan(tmp_path, "a" * 40, (filename,))
    _disable_real_convert_network(monkeypatch)
    _patch_planning_target_resolution(monkeypatch)

    def fail_after_write(path: pathlib.Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        raise OSError("テスト用書込み失敗")

    monkeypatch.setattr(mutations, "_atomic_write_text", fail_after_write)
    with pytest.raises(OSError, match="書込み失敗"):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(filename,),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    assert source.read_text(encoding="utf-8") == original
    assert not (notes / "inbox" / filename).exists()
    assert (
        subprocess.run(
            ["git", "-C", str(notes), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        == start_head
    )
    assert (
        subprocess.run(["git", "-C", str(notes), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
        == ""
    )


def test_convert_planning_entries_restores_all_paths_after_commit_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """commit失敗時にplanningの全入力とindexを開始時へ戻す。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_convert_feedback(notes, filename, state="planning")
    original = source.read_text(encoding="utf-8")
    start_head = _initialize_private_notes_git(notes)
    plan = _write_planning_plan(tmp_path, "a" * 40, (filename,))
    _disable_real_convert_network(monkeypatch)
    _patch_planning_target_resolution(monkeypatch)

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["git", "commit"])

    monkeypatch.setattr(mutations, "_commit_and_push", fail_commit)
    with pytest.raises(subprocess.CalledProcessError):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(filename,),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    assert source.read_text(encoding="utf-8") == original
    assert not (notes / "inbox" / filename).exists()
    assert (
        subprocess.run(
            ["git", "-C", str(notes), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        == start_head
    )
    assert (
        subprocess.run(["git", "-C", str(notes), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
        == ""
    )


def test_convert_planning_entries_keeps_clean_local_commit_after_push_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """push失敗時は変換済みのcleanなローカルcommitを保持する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    _write_convert_feedback(notes, filename, state="planning")
    start_head = _initialize_private_notes_git(notes)
    plan = _write_planning_plan(tmp_path, "a" * 40, (filename,))
    _disable_real_convert_network(monkeypatch)
    _patch_planning_target_resolution(monkeypatch)

    def commit_then_fail_push(
        private_notes: pathlib.Path,
        commit_message: str,
        relative_paths: tuple[str, ...],
        **_kwargs: object,
    ) -> None:
        subprocess.run(
            ["git", "-C", str(private_notes), "add", "--", *relative_paths],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(private_notes),
                "-c",
                "user.email=agent-toolkit@test.invalid",
                "-c",
                "user.name=agent-toolkit-test",
                "commit",
                "-m",
                commit_message,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        raise subprocess.CalledProcessError(1, ["git", "push"])

    monkeypatch.setattr(mutations, "_commit_and_push", commit_then_fail_push)
    with pytest.raises(subprocess.CalledProcessError):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(filename,),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    head = subprocess.run(
        ["git", "-C", str(notes), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert head != start_head
    assert (notes / "inbox" / filename).is_file()
    assert not (notes / "planning" / filename).exists()
    assert (
        subprocess.run(["git", "-C", str(notes), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
        == ""
    )


def test_cmd_convert_to_plan_displays_commit_for_single_planning_input(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """単一planning入力でも保存結果、commit及びpush結果を表示する。"""
    details: dict[str, object | None] = {
        "target_repo": "github.com/example/foo",
        "target_commit": "b" * 40,
        "plan_file": "/tmp/plan.md",
        "depends_on": [],
    }
    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "resolve_add_target",
        lambda _value: ("github.com/example/foo", tmp_path / "target-worktree"),
    )
    monkeypatch.setattr(
        mutations,
        "convert_entries_to_plan",
        lambda *_args, **_kwargs: {"entries": [details], "commit": "c" * 40, "planning": True},
    )
    args = argparse.Namespace(
        filename=["20260827-000000-001.md"],
        message="統合本文",
        plan_file="/tmp/plan.md",
        depends_on=None,
        target_repo="github.com/example/foo",
        skip_push=False,
    )

    mutations._cmd_convert_to_plan(args, tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

    output = capsys.readouterr().out
    assert "変換commit: " + "c" * 40 in output
    assert "push: 完了" in output


def test_cmd_convert_to_plan_displays_saved_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """変換コマンドが保存後に返された照合対象を表示する。"""
    details: dict[str, object | None] = {
        "target_repo": "github.com/example/foo",
        "target_commit": "a" * 40,
        "plan_file": "/tmp/plan.md",
        "depends_on": ["dependency.md"],
    }
    monkeypatch.setattr(
        mutations,
        "convert_entries_to_plan",
        lambda *_args, **_kwargs: {"entries": [details], "commit": "b" * 40},
    )
    args = argparse.Namespace(
        filename="feedback.md",
        plan_file="/tmp/plan.md",
        depends_on=["dependency.md"],
        target_repo="github.com/example/foo",
    )

    mutations._cmd_convert_to_plan(args, tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

    output = capsys.readouterr().out
    assert "target_repo: github.com/example/foo" in output
    assert f"target_commit: {'a' * 40}" in output
    assert "plan_file: /tmp/plan.md" in output
    assert "depends_on: dependency.md" in output
    assert "変換commit:" not in output
    assert "push: 完了" not in output


def test_return_to_inbox_moves_processing_to_inbox(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """return-to-inboxがprocessingからinboxへ戻す。"""
    notes = _setup_notes(tmp_path)
    _write_feedback_file(notes, "entry.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    mutations.transition_entries(notes, action="start-processing", filenames=["entry.md"], now=_FIXED_DT)
    filenames = mutations.transition_entries(
        notes,
        action="return-to-inbox",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )
    assert filenames == ["entry.md"]
    assert (notes / "inbox/entry.md").is_file()
    assert not (notes / "processing/entry.md").exists()


def test_cooldown_return_sets_one_utc_deadline_and_start_clears_it(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """複数フィードバックへ同じUTC期限を設定し、再開時に期限を除去する。"""
    notes = _setup_notes(tmp_path)
    first = _write_feedback_file(notes, "first.md")
    second = _write_feedback_file(notes, "second.md")
    _disable_transition_git(monkeypatch)
    now = datetime.datetime(2024, 1, 15, 10, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    mutations.transition_entries(notes, action="start-processing", filenames=["first.md", "second.md"], now=now)

    mutations.transition_entries(
        notes,
        action="return-to-inbox",
        filenames=["first.md", "second.md"],
        now=now,
        cooldown_days=3,
    )

    for path in (first, second):
        assert "cooldown_until: '2024-01-18T01:30:00+00:00'" in path.read_text(encoding="utf-8")
    mutations.transition_entries(notes, action="start-processing", filenames=["first.md"], now=now)
    assert "cooldown_until" not in (notes / "processing/first.md").read_text(encoding="utf-8")


def test_plain_return_clears_existing_cooldown(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """通常差し戻しでは既存期限を持ち越さない。"""
    notes = _setup_notes(tmp_path)
    path = _write_feedback_file(notes, "entry.md")
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ncooldown_until: old\n"),
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="start-processing", filenames=["entry.md"], now=_FIXED_DT)
    mutations.transition_entries(notes, action="return-to-inbox", filenames=["entry.md"], now=_FIXED_DT)
    assert "cooldown_until" not in path.read_text(encoding="utf-8")


def test_cooldown_return_rejects_tbd_mixture_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TBD混在入力を一括検証し、移動もfrontmatter更新も行わない。"""
    notes = _setup_notes(tmp_path)
    _write_feedback_file(notes, "feedback.md")
    _write_tbd_entry(notes, "tbd.md")
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="start-processing", filenames=["feedback.md", "tbd.md"], now=_FIXED_DT)
    feedback = notes / "processing/feedback.md"
    tbd_path = notes / "processing/tbd.md"
    original_feedback = feedback.read_text(encoding="utf-8")
    original_tbd = tbd_path.read_text(encoding="utf-8")

    with pytest.raises(mutations.WebInputError, match="フィードバック専用"):
        mutations.transition_entries(
            notes,
            action="return-to-inbox",
            filenames=["feedback.md", "tbd.md"],
            now=_FIXED_DT,
            cooldown_days=3,
        )

    assert feedback.read_text(encoding="utf-8") == original_feedback
    assert tbd_path.read_text(encoding="utf-8") == original_tbd


def test_cooldown_return_rejects_tbd_without_frontmatter_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TBD単独指定も移動とfrontmatter更新の前に拒否する。"""
    notes = _setup_notes(tmp_path)
    _write_tbd_entry(notes, "tbd.md")
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="start-processing", filenames=["tbd.md"], now=_FIXED_DT)
    path = notes / "processing/tbd.md"
    original = path.read_text(encoding="utf-8")

    with pytest.raises(mutations.WebInputError, match="フィードバック専用"):
        mutations.transition_entries(
            notes,
            action="return-to-inbox",
            filenames=["tbd.md"],
            now=_FIXED_DT,
            cooldown_days=3,
        )

    assert path.read_text(encoding="utf-8") == original
    assert not (notes / "inbox/tbd.md").exists()


def test_return_to_inbox_missing_file_reports_processing_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """return-to-inboxで未存在ファイルを指定するとprocessing側の状態名で案内する。"""
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "return-to-inbox", "nonexistent.md"], home=tmp_path)

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "processingに存在しません" in captured.err


class TestAdoptSingle:
    """adoptサブコマンド: 1件指定でinboxからadopted/へ移動しコミットを行う。"""

    def test_single_file_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のadopt実行でinboxから移動されadopted/に置かれコミットメッセージが正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "adopted" / "fb-001.md").exists()

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 entry (adopted)" in commit_cmd

    def test_adopt_bare_stem_from_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """拡張子.md省略入力がinbox側の実体を解決してadoptedへ移動する（fb 20260721-164301-001反映）。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "20260721-160220-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            # 拡張子.mdを省略した引数でadoptを呼ぶ
            atk.main(["mq", "adopt", "20260721-160220-001"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "20260721-160220-001.md").exists()
        assert (notes / "adopted" / "20260721-160220-001.md").exists()


class TestAdoptMultiple:
    """adoptサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_adopted_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """3件のadoptで全件がadopted/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        _write_feedback_file(notes, "fb-003.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "fb-002.md", "fb-003.md"], home=tmp_path)

        assert exc_info.value.code == 0
        inbox = notes / "inbox"
        assert not (inbox / "fb-001.md").exists()
        assert not (inbox / "fb-002.md").exists()
        assert not (inbox / "fb-003.md").exists()
        adopted = notes / "adopted"
        assert (adopted / "fb-001.md").exists()
        assert (adopted / "fb-002.md").exists()
        assert (adopted / "fb-003.md").exists()

        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 3 entries (adopted)" in commit_cmds[0]


class TestAdoptZeroArgs:
    """adoptサブコマンド: ファイル名引数0件でexit 2となる（nargs="+"のargparse制約）。"""

    def test_no_args_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ファイル名引数なしでargparseがexit 2を返すこと。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt"], home=tmp_path)

        assert exc_info.value.code == 2


class TestAdoptMissing:
    """adoptサブコマンド: 存在しないファイル指定でexit 2となる。"""

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processingのいずれにも存在しません" in captured.err


class TestAdoptStampWithNoteAndCommit:
    """adopt: --note・--commit指定時に`## 処理結果`節へ全項目が追記される。"""

    def test_stamp_written_with_all_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--note・--commit指定時、adopted/配下のファイル末尾に採否・処理日時・対応commit・メモが追記される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="元本文")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "adopt", "fb-001.md", "--note", "採用理由サマリー", "--commit", "abc1234"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        adopted_text = (notes / "adopted" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in adopted_text
        assert "- 採否: adopted" in adopted_text
        assert "- 処理日時: " in adopted_text
        assert "- 対応commit: abc1234" in adopted_text
        assert "- メモ: 採用理由サマリー" in adopted_text


class TestAdoptStampWithoutOptional:
    """adopt: --note・--commit省略時も必須項目のみ追記される。"""

    def test_stamp_written_with_required_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """引数省略時、`## 処理結果`節に採否・処理日時のみ追記され、対応commit・メモ行は含まれない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        adopted_text = (notes / "adopted" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in adopted_text
        assert "- 採否: adopted" in adopted_text
        assert "- 処理日時: " in adopted_text
        assert "- 対応commit: " not in adopted_text
        assert "- メモ: " not in adopted_text


class TestRejectDeletes:
    """rejectサブコマンド: ファイルをinboxからrejected/へ移動する。"""

    def test_single_file_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rejectでファイルがinboxから移動されrejected/に置かれコミット件名が正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "rejected" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 entry (rejected)" in commit_cmd


class TestSkipPush:
    """adopt・rejectの中間操作でpushを省略し、最後に滞留commitを送信する。"""

    def test_adopt_skip_push_commits_without_push_and_reports_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """adoptの--skip-pushはcommitだけを実行し未push状態を案内する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--skip-push"], home=tmp_path)

        assert exc_info.value.code == 0
        commands = [call["cmd"] for call in git_calls]
        assert any(command[:2] == ["git", "add"] for command in commands)
        assert any(command[:2] == ["git", "commit"] for command in commands)
        assert ["git", "push"] not in commands
        captured = capsys.readouterr()
        assert "未pushのcommit" in captured.err
        assert "atk mq commit" in captured.err

    def test_reject_skip_push_commits_without_push_and_reports_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """rejectの--skip-pushはcommitだけを実行し未push状態を案内する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md", "--skip-push"], home=tmp_path)

        assert exc_info.value.code == 0
        commands = [call["cmd"] for call in git_calls]
        assert any(command[:2] == ["git", "add"] for command in commands)
        assert any(command[:2] == ["git", "commit"] for command in commands)
        assert ["git", "push"] not in commands
        captured = capsys.readouterr()
        assert "未pushのcommit" in captured.err
        assert "atk mq commit" in captured.err

    def test_default_adopt_pushes_without_skip_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--skip-pushを指定しないadoptは従来どおりpushし注記を出力しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commands = [call["cmd"] for call in git_calls]
        assert ["git", "push"] in commands
        assert "--skip-push" not in capsys.readouterr().err

    def test_pending_commits_are_pushed_by_following_normal_transition(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """skip-push後の通常遷移が先行分を含む滞留commitをまとめてpushする。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as first_exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--skip-push"], home=tmp_path)
        assert first_exc_info.value.code == 0
        capsys.readouterr()

        with pytest.raises(SystemExit) as second_exc_info:
            atk.main(["mq", "reject", "fb-002.md"], home=tmp_path)
        assert second_exc_info.value.code == 0
        commands = [call["cmd"] for call in git_calls]
        assert commands.count(["git", "commit", "-m", "chore: process 1 entry (adopted)"]) == 1
        assert commands.count(["git", "commit", "-m", "chore: process 1 entry (rejected)"]) == 1
        assert commands.count(["git", "push"]) == 2
        assert "--skip-push" not in capsys.readouterr().err

    def test_commit_pushes_clean_ahead_repository(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """キューがcleanでもaheadの滞留commitを`atk mq commit`でpushする。"""
        notes = _setup_notes(tmp_path)
        for state in ("processing", "adopted", "rejected"):
            (notes / state).mkdir()
        (notes / "adopted" / "base.md").write_text("base\n", encoding="utf-8")
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=notes, capture_output=True, text=True, check=True)
        remote = tmp_path / "origin.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(remote)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote)],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "push", "--set-upstream", "origin", "main"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        (notes / "adopted" / "pending.md").write_text("pending\n", encoding="utf-8")
        subprocess.run(["git", "add", "adopted/pending.md"], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", "pending"], cwd=notes, capture_output=True, text=True, check=True)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "commit"], home=tmp_path)

        assert exc_info.value.code == 0
        local_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=notes, capture_output=True, text=True, check=True
        ).stdout.strip()
        remote_head = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert local_head == remote_head
        assert "差分なし。滞留commitをpushしました。" in capsys.readouterr().out

    def test_real_git_transitions_push_and_recover_after_remote_advances(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """通常遷移は即時pushし、remote進行後もskip-pushの滞留commitを回復する。"""
        first_home = tmp_path / "first"
        first_home.mkdir()
        notes = _setup_notes(first_home)
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
        for state in ("processing", "adopted", "rejected"):
            (notes / state).mkdir()
        for filename in ("default-adopt.md", "default-reject.md", "skip.md", "following.md"):
            _write_feedback_file(notes, filename)

        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        for key, value in (("user.name", "queue-test"), ("user.email", "queue-test@example.invalid")):
            subprocess.run(["git", "config", key, value], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "add", "."], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=notes, capture_output=True, text=True, check=True)

        remote = tmp_path / "origin.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(remote)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote)],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "push", "--set-upstream", "origin", "main"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )

        second_home = tmp_path / "second"
        second_home.mkdir()
        second_notes = second_home / "private-notes"
        subprocess.run(
            ["git", "clone", "--branch", "main", str(remote), str(second_notes)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        for key, value in (("user.name", "queue-test"), ("user.email", "queue-test@example.invalid")):
            subprocess.run(["git", "config", key, value], cwd=second_notes, capture_output=True, text=True, check=True)

        def local_head() -> str:
            return subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=notes, capture_output=True, text=True, check=True
            ).stdout.strip()

        def remote_head() -> str:
            return subprocess.run(
                ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

        with pytest.raises(SystemExit) as adopt_exit:
            atk.main(["mq", "adopt", "default-adopt.md"], home=first_home, now=_FIXED_DT)
        assert adopt_exit.value.code == 0
        assert local_head() == remote_head()

        with pytest.raises(SystemExit) as reject_exit:
            atk.main(["mq", "reject", "default-reject.md"], home=first_home, now=_FIXED_DT)
        assert reject_exit.value.code == 0
        assert local_head() == remote_head()

        subprocess.run(["git", "pull", "--ff-only"], cwd=second_notes, capture_output=True, text=True, check=True)
        (notes / "adopted" / "pending.md").write_text("pending\n", encoding="utf-8")
        subprocess.run(["git", "add", "adopted/pending.md"], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", "pending"], cwd=notes, capture_output=True, text=True, check=True)
        pending_head = local_head()
        remote_before_skip = remote_head()

        with pytest.raises(SystemExit) as skip_exit:
            atk.main(["mq", "adopt", "skip.md", "--skip-push"], home=first_home, now=_FIXED_DT)
        assert skip_exit.value.code == 0
        assert remote_head() == remote_before_skip
        assert local_head() != remote_head()
        assert (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", pending_head, "HEAD"],
                cwd=notes,
                capture_output=True,
                text=True,
                check=False,
            ).returncode
            == 0
        )
        assert subprocess.run(
            ["git", "log", "-2", "--format=%s"], cwd=notes, capture_output=True, text=True, check=True
        ).stdout.splitlines() == ["chore: process 1 entry (adopted)", "pending"]

        (second_notes / "remote-update.txt").write_text("remote update\n", encoding="utf-8")
        subprocess.run(["git", "add", "remote-update.txt"], cwd=second_notes, capture_output=True, text=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "remote update"],
            cwd=second_notes,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(["git", "push"], cwd=second_notes, capture_output=True, text=True, check=True)

        with pytest.raises(SystemExit) as following_exit:
            atk.main(["mq", "reject", "following.md"], home=first_home, now=_FIXED_DT)
        assert following_exit.value.code == 0
        assert local_head() == remote_head()
        assert (notes / "adopted/skip.md").is_file()
        assert (notes / "rejected/following.md").is_file()

    def test_skip_push_without_remote_does_not_report_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """remote未設定の管理リポジトリでは--skip-pushの注記を出力しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        (notes / common._LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--skip-push"], home=tmp_path)

        assert exc_info.value.code == 0
        assert ["git", "push"] not in [call["cmd"] for call in git_calls]
        assert "未pushのcommit" not in capsys.readouterr().err


class TestRejectStampWithNote:
    """reject: --note指定時に`## 処理結果`節へメモが追記される。"""

    def test_reject_stamp_note_written(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--note指定時、rejected/配下のファイル末尾に採否・処理日時・メモが追記される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md", "--note", "不採用理由"], home=tmp_path)

        assert exc_info.value.code == 0
        rejected_text = (notes / "rejected" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in rejected_text
        assert "- 採否: rejected" in rejected_text
        assert "- メモ: 不採用理由" in rejected_text


class TestRejectMultiple:
    """rejectサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_rejected_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のrejectで両方がrejected/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        inbox = notes / "inbox"
        assert not (inbox / "fb-001.md").exists()
        assert not (inbox / "fb-002.md").exists()
        rejected = notes / "rejected"
        assert (rejected / "fb-001.md").exists()
        assert (rejected / "fb-002.md").exists()
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 2 entries (rejected)" in commit_cmds[0]


class TestRejectIfInbox:
    """rejectのinbox状態前提を公開CLI経路で検証する。"""

    def test_rejects_inbox_entry_and_preserves_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """対象がinboxなら本文を保持して処理理由を記録する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "inbox.md", body="保持する本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "reject", "inbox.md", "--if-inbox", "--note=計画作成へ移管"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        content = (notes / "rejected/inbox.md").read_text(encoding="utf-8")
        assert "保持する本文" in content
        assert "- メモ: 計画作成へ移管" in content

    def test_processing_entry_stops_without_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """pull後にprocessingへ移った対象は終端しない。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        path = processing / "processing.md"
        path.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n処理中本文\n",
            encoding="utf-8",
        )
        original = path.read_text(encoding="utf-8")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "processing.md", "--if-inbox"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert path.read_text(encoding="utf-8") == original
        assert not (notes / "rejected/processing.md").exists()
        assert not any("commit" in call["cmd"] for call in git_calls)

    def test_mixed_states_stop_the_whole_batch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数対象の一部がprocessingならinbox対象も変更しない。"""
        notes = _setup_notes(tmp_path)
        inbox = _write_feedback_file(notes, "inbox.md", body="inbox本文")
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True, exist_ok=True)
        processing = processing_dir / "processing.md"
        processing.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\nprocessing本文\n",
            encoding="utf-8",
        )
        inbox_original = inbox.read_text(encoding="utf-8")
        processing_original = processing.read_text(encoding="utf-8")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "reject", "inbox.md", "processing.md", "--if-inbox"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 2
        assert inbox.read_text(encoding="utf-8") == inbox_original
        assert processing.read_text(encoding="utf-8") == processing_original
        assert not any((notes / "rejected").glob("*.md"))
        assert not any("commit" in call["cmd"] for call in git_calls)


class TestRejectZeroArgs:
    """rejectサブコマンド: ファイル名引数0件でexit 2となる（nargs="+"のargparse制約）。"""

    def test_no_args_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ファイル名引数なしでargparseがexit 2を返すこと。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject"], home=tmp_path)

        assert exc_info.value.code == 2


class TestRmSingle:
    """rmサブコマンド: 単純削除とコミット件名を検証する。"""

    def test_single_file_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rmで対象ファイルが削除されコミット件名が正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 entry" in commit_cmd

    def test_processing_file_removed_with_force(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing後（processing配下）のファイルは`--force`指定時のみrm対象として解決される。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nテスト本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "--force", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing_dir / "fb-001.md").exists()

    def test_processing_file_rejected_without_force(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--force`未指定時、processing配下のファイルは削除を拒否されexit 2する（フィードバック20260723-153526-001反映）。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nテスト本文\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (processing_dir / "fb-001.md").exists()
        captured = capsys.readouterr()
        assert "processing状態のファイルは既定で削除を保護します" in captured.err
        assert "fb-001.md" in captured.err

    def test_missing_file_reports_inbox_and_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox・processingいずれにも存在しない場合、両状態を明記したメッセージでexit 2する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-missing.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processingのいずれにも存在しません" in captured.err


class TestRmMultiple:
    """rmサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_removed_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のrmで両方削除と単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: remove 2 entries" in commit_cmds[0]


class TestEditNoEditor:
    """editサブコマンド: $EDITOR未設定でexit 1となる。"""

    def test_no_editor_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """$EDITORが未設定の場合はexit 1と案内が出力される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        monkeypatch.delenv("EDITOR", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "EDITOR" in captured.err


class TestEditWithChanges:
    """editサブコマンド: 編集後差分ありでcommit・push実行。"""

    def test_edit_with_changes_commits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """編集後にファイル差分があればコミット・pushが実行される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                # 新設計ではエディターへ渡されるのは対象ファイルのスナップショットを
                # 複製した一時ファイルのため、元ファイルではなくcmd[1]を書き換える。
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(editor_path.read_text(encoding="utf-8").replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: edit feedback item" in commit_cmd

    def test_processing_file_edited(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing後（processing配下）のファイルも編集対象として解決される。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(editor_path.read_text(encoding="utf-8").replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (processing_dir / "fb-001.md").read_text(encoding="utf-8").endswith("\n編集後\n")

    def test_editor_target_repo_change_invalidates_target_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """対話編集でtarget_repoを変更した場合は旧リポジトリのtarget_commitを削除する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "type: feedback\n",
                f"type: feedback\ntarget_commit: {'a' * 40}\n",
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("EDITOR", "fake-editor")

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(
                    editor_path.read_text(encoding="utf-8").replace(
                        "target_repo: github.com/example/foo",
                        "target_repo: github.com/example/new",
                    ),
                    encoding="utf-8",
                )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["target_repo"] == "github.com/example/new"
        assert "target_commit" not in parsed[0]

    def test_missing_file_reports_inbox_and_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox・processingいずれにも存在しない場合、両状態を明記したメッセージでexit 2する。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-missing.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processingのいずれにも存在しません" in captured.err


class TestEditNoChanges:
    """editサブコマンド: 差分なしでcommitせず終了。"""

    def test_edit_no_changes_skips_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集後にファイル差分がなければコミットされず案内のみ出力される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert commit_cmds == []
        captured = capsys.readouterr()
        assert "差分なし" in captured.out


class TestNoninteractiveEdit:
    """editサブコマンドのMESSAGE指定による非対話編集を検証する。"""

    def test_feedback_body_updates_without_editor_and_preserves_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """EDITOR未設定でも本文を更新し、未指定メタデータを保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前", source="session-review")
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8") == (
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\nsource: session-review\n---\n\n編集後\n"
        )

    def test_message_does_not_start_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """MESSAGE指定時はEDITORが設定済みでもエディターを起動しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "must-not-run")
        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[0] == "must-not-run":
                pytest.fail("MESSAGE指定時にEDITORが起動された")
            return _make_subprocess_fake(git_calls)(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0

    def test_target_repo_is_normalized_and_existing_frontmatter_lines_are_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """明示したtarget_repoを正規化し、他の意味的なキーを保持する。"""
        notes = _setup_notes(tmp_path)
        path = notes / "inbox" / "fb-001.md"
        path.write_text(
            "---\n# 保持するコメント\ntarget_repo: old.example/a/b\n\n"
            "target_repo: old.example/a/b\ntype: feedback\nsource: manual\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\ntarget_repo: https://github.com/Example/Repo.git\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0] == {
            "target_repo": "github.com/example/repo",
            "type": "feedback",
            "source": "manual",
        }
        assert parsed[1] == "\n編集後\n"

    @pytest.mark.parametrize(
        ("message", "exit_code", "error_fragment"),
        [
            ("---\ntype: tbd\n---\n\n本文", 2, "typeを変更"),
            ("---\nscope: item\n---\n\n本文", 1, "フィードバックでは指定できない"),
            (" \n-\n ", 1, "実質空"),
        ],
    )
    def test_feedback_rejects_invalid_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        message: str,
        exit_code: int,
        error_fragment: str,
    ) -> None:
        """種別変更・TBD専用キー・実質空本文を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == exit_code
        assert error_fragment in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_existing_file_path_is_rejected_without_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """実在ファイルパスだけのMESSAGEをtracebackなしで拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        message_file = tmp_path / "message.txt"
        message_file.write_text("編集後", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", str(message_file)], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ファイルパス" in captured.err
        assert "Traceback" not in captured.err
        assert path.read_text(encoding="utf-8").endswith("\n編集前\n")

    def test_empty_tbd_add_is_allowed_but_empty_edit_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """空のTBD質問はaddで許容し、既存質問を削除するeditでは拒否する。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as add_exit:
            atk.main(
                [
                    "mq",
                    "add",
                    "--target-repo",
                    "github.com/example/foo",
                    "--type=tbd",
                    "--question-type=free-form",
                    "",
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert add_exit.value.code == 0
        filename = f"{_FIXED_DT:%Y%m%d-%H%M%S}-001.md"
        assert (notes / "inbox" / filename).is_file()

        with pytest.raises(SystemExit) as edit_exit:
            atk.main(["mq", "edit", filename, ""], home=tmp_path)

        assert edit_exit.value.code == 1
        captured = capsys.readouterr()
        assert "質問本文は空にできません" in captured.err
        assert "Traceback" not in captured.err

    def test_processing_feedback_can_be_edited(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`processing`配下のフィードバックも非対話で編集する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir()
        path = processing / "fb-001.md"
        path.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8").endswith("\n編集後\n")

    def test_tbd_question_and_scope_update_preserves_answer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """TBDの質問とscopeだけを更新し、回答領域を保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_tbd_entry(
            notes,
            "tbd-001.md",
            frontmatter=("target_repo: github.com/example/foo\ntype: tbd\nscope: old\nquestion_type: choice\nchoices: A,B"),
        )
        original_answer = path.read_text(encoding="utf-8").split(tbd.ANSWER_HEADING, maxsplit=1)[1]
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\nscope: new\n---\n\n変更後の質問"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "tbd-001.md", message], home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert "scope: new" in content
        assert "変更後の質問" in content
        assert content.split(tbd.ANSWER_HEADING, maxsplit=1)[1] == original_answer

    @pytest.mark.parametrize(
        "message",
        [
            f"変更後\n\n{tbd.ANSWER_HEADING}\n",
            f"変更後\n\n{tbd.ANSWER_MARKER}\n",
            "---\nquestion_type: invalid\n---\n\n変更後",
            "---\nquestion_type: choice\nchoices:\n---\n\n変更後",
        ],
    )
    def test_tbd_rejects_invalid_question_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        message: str,
    ) -> None:
        """予約要素と不正な質問メタデータを拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_tbd_entry(notes, "tbd-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "tbd-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert path.read_text(encoding="utf-8") == original

    def test_tbd_uses_last_answer_marker_and_preserves_answer_region(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """回答マーカー重複時も終端側を基準に質問だけを更新する。"""
        notes = _setup_notes(tmp_path)
        path = _write_tbd_entry(
            notes,
            "tbd-001.md",
            question=f"前半\n\n{tbd.ANSWER_MARKER}\n\n後半",
            answer="保持する回答",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "tbd-001.md", "変更後の質問"], home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert content.count(tbd.ANSWER_MARKER) == 1
        assert content.endswith(f"{tbd.ANSWER_MARKER}\n保持する回答\n")

    def test_expected_content_conflict_keeps_message_unapplied(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """競合時は上書きせず、FILENAMEと未反映を案内する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        original_edit = mutations.edit_entry_content

        def conflict(
            private_notes: pathlib.Path,
            *,
            state: str,
            filename: str,
            content: str,
            target_repo: str | None = None,
            lock_timeout: float = -1,
            expected_content: str | None = None,
        ) -> bool:
            path.write_text(path.read_text(encoding="utf-8").replace("編集前", "競合側の変更"), encoding="utf-8")
            return original_edit(
                private_notes,
                state=state,
                filename=filename,
                content=content,
                target_repo=target_repo,
                lock_timeout=lock_timeout,
                expected_content=expected_content,
            )

        monkeypatch.setattr(mutations, "edit_entry_content", conflict)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.err
        assert "反映されていません" in captured.err
        assert path.read_text(encoding="utf-8").endswith("\n競合側の変更\n")

    def test_logically_identical_feedback_reports_no_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """論理本文が同一ならコミットせず差分なしを出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "本文"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "差分なし。" in capsys.readouterr().out
        assert not [call for call in git_calls if "commit" in call["cmd"]]

    def test_edit_rejects_explicit_target_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """非対話edit経路からtarget_commitを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\ntarget_commit: {'b' * 40}\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    @pytest.mark.parametrize("updated_commit", ["b" * 40, None])
    def test_edit_content_validator_rejects_target_commit_change_or_removal(
        self,
        updated_commit: str | None,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """共通保存境界が同一リポジトリのtarget_commit変更と削除を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            f"type: feedback\ntarget_commit: {'a' * 40}\n",
        )
        path.write_text(original, encoding="utf-8")
        replacement = "" if updated_commit is None else f"target_commit: {updated_commit}\n"
        updated = original.replace(f"target_commit: {'a' * 40}\n", replacement)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(mutations.WebInputError):
            mutations.edit_entry_content(
                notes,
                state="inbox",
                filename="fb-001.md",
                content=updated,
                lock_timeout=2.0,
            )

        assert path.read_text(encoding="utf-8") == original

    def test_edit_content_boundary_invalidates_target_commit_on_target_repo_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Web API共通保存境界はtarget_repo変更時に旧target_commitを削除する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            f"type: feedback\ntarget_commit: {'a' * 40}\n",
        )
        path.write_text(original, encoding="utf-8")
        updated = original.replace("target_repo: github.com/example/foo", "target_repo: github.com/example/new")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        assert mutations.edit_entry_content(
            notes,
            state="inbox",
            filename="fb-001.md",
            content=updated,
            lock_timeout=2.0,
        )

        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["target_repo"] == "github.com/example/new"
        assert "target_commit" not in parsed[0]

    def test_edit_rejects_explicit_plan_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """edit経路からplan_fileを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\nplan_file: /tmp/plan.md\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_edit_rejects_explicit_cooldown_until(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """通常edit経路から再処理抑制期限を注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\ncooldown_until: 2026-08-15T00:00:00+00:00\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    @pytest.mark.parametrize("updated_plan_file", ["/tmp/other.md", None])
    def test_edit_content_validator_rejects_plan_file_change_or_removal(
        self,
        updated_plan_file: str | None,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """共通保存境界が既存plan_fileの変更と削除を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            "type: feedback\nplan_file: /tmp/plan.md\n",
        )
        path.write_text(original, encoding="utf-8")
        replacement = "" if updated_plan_file is None else f"plan_file: {updated_plan_file}\n"
        updated = original.replace("plan_file: /tmp/plan.md\n", replacement)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(mutations.WebInputError):
            mutations.edit_entry_content(
                notes,
                state="inbox",
                filename="fb-001.md",
                content=updated,
                lock_timeout=2.0,
            )

        assert path.read_text(encoding="utf-8") == original

    def test_edit_preserves_plan_file_when_only_body_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """plan_fileを持つ項目も本文だけの編集を許容する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            "type: feedback\nplan_file: /tmp/plan.md\n",
        )
        path.write_text(original, encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["plan_file"] == "/tmp/plan.md"

    @pytest.mark.parametrize(
        ("reserved_key", "reserved_value"),
        [("repair_target", "broken.md"), ("repair_kind", "frontmatter")],
    )
    def test_edit_rejects_explicit_repair_metadata(
        self,
        reserved_key: str,
        reserved_value: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """edit経路から修復TBDの予約キーを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\n{reserved_key}: {reserved_value}\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_edit_raises_web_input_error_when_frontmatter_is_corrupt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """frontmatter全体が破損している場合は編集を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = notes / "inbox" / "fb-001.md"
        path.write_text("---\ntarget_repo: [broken\n---\n本文\n", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 1
        assert "frontmatterが破損" in capsys.readouterr().err


class TestAppendEdit:
    """`edit --append`のraw bytes保持・競合・TBD拒否を検証する。"""

    def test_append_preserves_lf_bytes_and_adds_utf8_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """LF本文の元bytesを変更せず、区切りとUTF-8本文を末尾へ追加する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="本文")
        original = path.read_bytes()
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "--append", "fb-001.md", "追記本文"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_bytes() == original + b"\n\n" + "追記本文".encode()

    def test_append_preserves_crlf_bytes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """CRLF本文の改行を変換せず、元bytesへ追記する。"""
        notes = _setup_notes(tmp_path)
        path = notes / "inbox" / "fb-001.md"
        path.write_bytes(b"---\r\ntarget_repo: github.com/example/foo\r\ntype: feedback\r\n---\r\n\r\n" + "本文\r\n".encode())
        original = path.read_bytes()
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "--append", "fb-001.md", "追記本文"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_bytes() == original + b"\n\n" + "追記本文".encode()

    @pytest.mark.parametrize("answer", ["", "既存回答"])
    def test_append_rejects_tbd_before_writing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        answer: str,
    ) -> None:
        """未回答・回答済みを問わずTBDへの追記を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_tbd_entry(notes, "tbd-001.md", answer=answer)
        original = path.read_bytes()
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "--append", "tbd-001.md", "追記本文"], home=tmp_path)

        assert exc_info.value.code == 1
        assert "TBDには追記できません" in capsys.readouterr().err
        assert path.read_bytes() == original

    def test_append_expected_bytes_conflict_keeps_message_unapplied(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """snapshot後の競合時は追記本文を反映しない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="本文")
        original_append = mutations.append_entry_content

        def conflict(
            private_notes: pathlib.Path,
            *,
            state: str,
            filename: str,
            content: bytes,
            target_repo: str | None = None,
            lock_timeout: float = -1,
            expected_content: bytes | None = None,
        ) -> bool:
            path.write_bytes(path.read_bytes() + "競合側の変更".encode())
            return original_append(
                private_notes,
                state=state,
                filename=filename,
                content=content,
                target_repo=target_repo,
                lock_timeout=lock_timeout,
                expected_content=expected_content,
            )

        monkeypatch.setattr(mutations, "append_entry_content", conflict)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "--append", "fb-001.md", "追記本文"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.err
        assert "反映されていません" in captured.err
        assert "追記本文".encode() not in path.read_bytes()


class TestEditNoArg:
    """editサブコマンド: 無引数時はinbox配下のファイル名順最大値（最終追加分）を対象とする。"""

    def test_edit_no_arg_selects_max_filename(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数ファイル存在時はファイル名順の最大値（最終追加分）が編集対象になる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "20240101-100000-001.md", body="旧")
        latest = _write_feedback_file(notes, "20240201-100000-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                # 新設計ではエディターへ渡されるのは対象ファイルのスナップショットを
                # 複製した一時ファイルのため、元ファイルではなくcmd[1]を書き換える。
                # 対象選択（最終追加分）の検証はcommit対象の相対パスで行う。
                editor_path = pathlib.Path(cmd[1])
                content = editor_path.read_text(encoding="utf-8")
                editor_path.write_text(content.replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: edit feedback item" in commit_cmd
        add_cmd = [c["cmd"] for c in git_calls if c["cmd"][:2] == ["git", "add"]][0]
        assert str(latest.relative_to(notes)) in add_cmd

    def test_edit_no_arg_exits_on_empty_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox空の場合はexit 2でstderr案内を出力する。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox" in captured.err


class TestStartProcessingSingle:
    """start-processingサブコマンド: 1件指定でinboxからprocessing/へ移動しコミットする。"""

    def test_single_file_moved_to_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のstart-processing実行でinboxから移動されprocessing/に置かれコミット件名が正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "start-processing", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "processing" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: start processing 1 entry" in commit_cmd


class TestStartPlanning:
    """start-planningサブコマンド: inboxの通常型フィードバックをplanning/へ移動する。"""

    def test_multiple_files_move_to_planning_in_sorted_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数項目をファイル名順で単一commitへまとめ、planning/へ移動する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "20260827-000000-002.md")
        _write_feedback_file(notes, "20260827-000000-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "start-planning",
                    "20260827-000000-002.md",
                    "20260827-000000-001.md",
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        assert sorted(path.name for path in (notes / "planning").iterdir()) == [
            "20260827-000000-001.md",
            "20260827-000000-002.md",
        ]
        assert not list((notes / "inbox").iterdir())
        commit_cmds = [call["cmd"] for call in git_calls if "commit" in call["cmd"]]
        assert commit_cmds == [["git", "commit", "-m", "chore: start planning 2 entries"]]
        assert "20260827-000000-001.md, 20260827-000000-002.md" in capsys.readouterr().out


def test_edit_entry_to_plan_reads_table_materials_and_moves_atomically(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計画の提示素材表から依存を統合し、本文・metadata・状態移動を一度に確定する。"""
    notes = _setup_notes(tmp_path)
    first = _write_feedback_file(notes, "20260827-000000-001.md")
    second = _write_feedback_file(notes, "20260827-000000-002.md")
    first_destination = notes / "planning" / first.name
    second_destination = notes / "planning" / second.name
    first.replace(first_destination)
    second.replace(second_destination)
    first = first_destination
    second = second_destination
    first.write_text(
        first.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: [external-a.md]\n"),
        encoding="utf-8",
    )
    second.write_text(
        second.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: [external-b.md]\n"),
        encoding="utf-8",
    )
    plan = tmp_path / "main-plan.md"
    plan.write_text(
        "## 提示素材\n\n"
        "| 素材ID | 種別 | キューID | 投入元 | 引用範囲 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| P-001 | フィードバック | 20260827-000000-001.md | test | 本文全文 |\n"
        "| P-002 | フィードバック | 20260827-000000-002.md | test | 本文全文 |\n\n"
        "| 要求ID | 素材参照 | 実装に必要な要件 | 採否 | 採用範囲 | 除外範囲 | 根拠 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| R-P-001-001 | P-001, P-002 | 統合する。 | 採用 | 統合 | 非該当 | 計画へ反映するため。 |\n",
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))

    details = mutations.edit_entry_to_plan(
        notes,
        filename="20260827-000000-001.md",
        content="---\nsummary: 統合本文\n---\n\n統合本文\n",
        plan_file=str(plan),
        target_commit="a" * 40,
        target_repo="github.com/example/foo",
    )

    output_path = notes / "inbox" / first.name
    assert not first.exists()
    assert not (notes / "planning" / first.name).exists()
    assert not (notes / "processing" / first.name).exists()
    assert second.exists()
    assert output_path.is_file()
    parsed = frontmatter_parser.parse_frontmatter(output_path.read_text(encoding="utf-8"))
    assert parsed is not None
    data, body = parsed
    assert data["source"] == "plan"
    assert data["plan_file"] == str(plan)
    assert data["target_commit"] == "a" * 40
    assert data["depends_on"] == ["external-a.md", "external-b.md"]
    assert "統合本文" in body
    assert details["plan_file"] == str(plan)
    assert len(commit_calls) == 1
    assert commit_calls[0][2] == [
        "planning/20260827-000000-001.md",
        "inbox/20260827-000000-001.md",
    ]


def test_edit_entry_to_plan_commit_failure_leaves_inbox_without_processing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変換commitの失敗後も計画型項目をprocessingへ公開しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_feedback_file(notes, filename)
    planning = notes / "planning" / filename
    source.replace(planning)
    plan = tmp_path / "main-plan.md"
    plan.write_text(f"## 提示素材\n\n- {filename}\n", encoding="utf-8")
    _disable_transition_git(monkeypatch)

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["git", "commit"])

    monkeypatch.setattr(mutations, "_commit_and_push", fail_commit)
    with pytest.raises(subprocess.CalledProcessError):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
        )

    assert not planning.exists()
    assert (notes / "inbox" / filename).is_file()
    assert not (notes / "processing" / filename).exists()


def test_edit_entry_to_plan_push_failure_leaves_local_inbox_commit_without_processing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変換commit後のpush失敗ではローカルinbox配置を保持し、processingへ移さない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_feedback_file(notes, filename)
    planning = notes / "planning" / filename
    source.replace(planning)
    plan = tmp_path / "main-plan.md"
    plan.write_text(f"## 提示素材\n\n- {filename}\n", encoding="utf-8")
    (notes / "processing").mkdir()
    (notes / "adopted").mkdir()
    (notes / "rejected").mkdir()
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=notes, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=notes, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "test: initialize private notes"],
        cwd=notes,
        check=True,
        capture_output=True,
        text=True,
    )
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=notes, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "push", "--set-upstream", "origin", "main"],
        cwd=notes,
        check=True,
        capture_output=True,
        text=True,
    )
    monkeypatch.setattr(common.platformdirs, "user_state_dir", lambda _name, **_kwargs: str(tmp_path / "state"))
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)

    def fail_push(_path: pathlib.Path) -> None:
        raise subprocess.CalledProcessError(1, ["git", "push"])

    monkeypatch.setattr(common, "_push_pending_commits", fail_push)
    before_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=notes, check=True, capture_output=True, text=True
    ).stdout.strip()

    with pytest.raises(subprocess.CalledProcessError):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
        )

    after_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=notes, check=True, capture_output=True, text=True
    ).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=notes, check=True, capture_output=True, text=True)
    upstream = subprocess.run(
        ["git", "merge-base", "--is-ancestor", after_head, "@{u}"],
        cwd=notes,
        capture_output=True,
        text=True,
        check=False,
    )
    assert after_head != before_head
    assert status.stdout == ""
    assert upstream.returncode != 0
    assert not planning.exists()
    assert (notes / "inbox" / filename).is_file()
    assert not (notes / "processing" / filename).exists()


def test_edit_entry_to_plan_rejects_inbox_name_conflict_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変換先inboxの同名競合時はplanningと競合先を変更しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_feedback_file(notes, filename)
    planning = notes / "planning" / filename
    source.replace(planning)
    original = planning.read_text(encoding="utf-8")
    conflict = notes / "inbox" / filename
    conflict.write_text("---\ntype: feedback\n---\n\n競合本文\n", encoding="utf-8")
    conflict_original = conflict.read_text(encoding="utf-8")
    plan = tmp_path / "main-plan.md"
    plan.write_text(f"## 提示素材\n\n- {filename}\n", encoding="utf-8")
    _disable_transition_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))

    with pytest.raises(mutations.WebInputError):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
        )

    assert planning.read_text(encoding="utf-8") == original
    assert conflict.read_text(encoding="utf-8") == conflict_original
    assert not (notes / "processing" / filename).exists()
    assert not commit_calls


def test_edit_entry_to_plan_rejects_content_conflict_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """planning本文の内容競合時は変換先を作成しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_feedback_file(notes, filename)
    planning = notes / "planning" / filename
    source.replace(planning)
    original = planning.read_text(encoding="utf-8")
    plan = tmp_path / "main-plan.md"
    plan.write_text(f"## 提示素材\n\n- {filename}\n", encoding="utf-8")
    _disable_transition_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))

    with pytest.raises(RuntimeError, match="編集中に他プロセスが対象を変更しました"):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
            expected_content=original + "外部変更",
        )

    assert planning.read_text(encoding="utf-8") == original
    assert not (notes / "inbox" / filename).exists()
    assert not (notes / "processing" / filename).exists()
    assert not commit_calls


@pytest.mark.parametrize(
    ("tbd_name", "tbd_state"),
    [
        ("20260826-235959-001.md", "processing"),
        ("20260827-000001-001.md", "inbox"),
    ],
)
def test_edit_entry_to_plan_allows_active_tbd_materials_outside_feedback_set(
    tbd_name: str,
    tbd_state: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前後にあるactive TBDを検証し、最古選択と依存統合から除外する。"""
    notes = _setup_notes(tmp_path)
    feedback_names = ("20260827-000000-001.md", "20260827-000000-002.md")
    for index, name in enumerate(feedback_names):
        source = _write_feedback_file(notes, name)
        source.replace(notes / "planning" / name)
        path = notes / "planning" / name
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "type: feedback\n",
                f"type: feedback\ndepends_on: [{tbd_name}, external-{index}.md]\n",
            ),
            encoding="utf-8",
        )
    tbd_path = _write_tbd_entry(notes, tbd_name)
    if tbd_state == "processing":
        (notes / "processing").mkdir(exist_ok=True)
        tbd_path.replace(notes / "processing" / tbd_name)
    plan = tmp_path / "main-plan.md"
    plan.write_text(
        "## 提示素材\n\n" + "".join(f"- {name}\n" for name in (*feedback_names, tbd_name)),
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)

    mutations.edit_entry_to_plan(
        notes,
        filename=feedback_names[0],
        content="統合本文",
        plan_file=str(plan),
        target_commit="a" * 40,
        target_repo="github.com/example/foo",
    )

    output = notes / "inbox" / feedback_names[0]
    parsed = frontmatter_parser.parse_frontmatter(output.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["depends_on"] == [tbd_name, "external-0.md", "external-1.md"]
    assert (notes / tbd_state / tbd_name).is_file()
    assert not (notes / "processing" / feedback_names[0]).exists()


@pytest.mark.parametrize("case", ["invalid_type", "missing_requirement_table", "duplicate_queue_id"])
def test_edit_entry_to_plan_rejects_invalid_structured_materials_before_changes(
    case: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正規パーサーが拒否する旧表形式をキュー項目の保存前に拒否する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_feedback_file(notes, filename)
    planning = notes / "planning" / filename
    source.replace(planning)
    material_type = "不正種別" if case == "invalid_type" else "フィードバック"
    second_row = f"| P-002 | フィードバック | {filename} | test | 本文全文 |\n" if case == "duplicate_queue_id" else ""
    requirement_table = (
        ""
        if case == "missing_requirement_table"
        else (
            "\n| 要求ID | 素材参照 | 実装に必要な要件 | 採否 | 採用範囲 | 除外範囲 | 根拠 |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            f"| R-P-001-001 | {'P-001, P-002' if second_row else 'P-001'} | 統合する。 | "
            "採用 | 統合 | 非該当 | 計画へ反映するため。 |\n"
        )
    )
    plan = tmp_path / "main-plan.md"
    plan.write_text(
        "## 提示素材\n\n"
        "| 素材ID | 種別 | キューID | 投入元 | 引用範囲 |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"| P-001 | {material_type} | {filename} | test | 本文全文 |\n"
        f"{second_row}{requirement_table}",
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))
    original = planning.read_text(encoding="utf-8")

    with pytest.raises(mutations.WebInputError, match="提示素材"):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
        )

    assert planning.read_text(encoding="utf-8") == original
    assert not (notes / "processing" / filename).exists()
    assert not commit_calls


def test_edit_plan_cli_uses_plan_base_commit_instead_of_current_head(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計画後にHEADが進んでも計画メタ情報のベースコミットを保存する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_feedback_file(notes, filename)
    source.replace(notes / "planning" / filename)
    plan_base = "a" * 40
    current_head = "b" * 40
    plan = tmp_path / "main-plan.md"
    plan.write_text(
        f"# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- ベースコミット: `{plan_base}`\n\n## 提示素材\n\n- {filename}\n",
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)
    worktree = tmp_path / "target-worktree"
    worktree.mkdir()
    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "resolve_add_target",
        lambda _value: ("github.com/example/foo", worktree),
    )
    monkeypatch.setattr(mutations, "_local_worktree_repo_id", lambda _path: "github.com/example/foo")
    monkeypatch.setattr(mutations._add, "resolve_head_commit", lambda _path: current_head)  # pylint: disable=protected-access
    resolved: list[tuple[pathlib.Path, str]] = []

    def resolve_commit(path: pathlib.Path, revision: str) -> str:
        resolved.append((path, revision))
        return revision

    monkeypatch.setattr(mutations, "_resolve_commit", resolve_commit)

    with pytest.raises(SystemExit) as captured:
        atk.main(
            [
                "mq",
                "edit",
                filename,
                "統合本文",
                "--plan-file",
                str(plan),
                "--target-repo",
                "github.com/example/foo",
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert captured.value.code == 0
    output = notes / "inbox" / filename
    parsed = frontmatter_parser.parse_frontmatter(output.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["target_commit"] == plan_base
    assert parsed[0]["target_commit"] != current_head
    assert resolved == [(worktree, plan_base)]
    assert not (notes / "processing" / filename).exists()


@pytest.mark.parametrize(
    ("metadata", "commit_resolves", "expected_exit"),
    [
        ("", True, 1),
        (f"- ベースコミット: `{'a' * 40}`\n- ベースコミット: `{'b' * 40}`\n", True, 1),
        ("- ベースコミット: `abc123`\n", True, 1),
        (f"- ベースコミット: `{'a' * 40}`\n", False, 2),
    ],
)
def test_edit_plan_cli_rejects_invalid_or_unresolvable_plan_base(
    metadata: str,
    commit_resolves: bool,
    expected_exit: int,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計画baseの欠落・曖昧・短縮・解決不能を現在HEADへ置換しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_feedback_file(notes, filename)
    source.replace(notes / "planning" / filename)
    plan = tmp_path / "main-plan.md"
    plan.write_text(
        f"# 計画\n\n## 概要\n\n### 計画メタ情報\n\n{metadata}\n## 提示素材\n\n- {filename}\n",
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)
    worktree = tmp_path / "target-worktree"
    worktree.mkdir()
    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "resolve_add_target",
        lambda _value: ("github.com/example/foo", worktree),
    )
    monkeypatch.setattr(mutations, "_local_worktree_repo_id", lambda _path: "github.com/example/foo")

    def resolve_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0 if commit_resolves else 1, "a" * 40 if commit_resolves else "", "")

    monkeypatch.setattr(mutations.subprocess, "run", resolve_run)

    with pytest.raises(SystemExit) as captured:
        atk.main(
            [
                "mq",
                "edit",
                filename,
                "統合本文",
                "--plan-file",
                str(plan),
                "--target-repo",
                "github.com/example/foo",
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert captured.value.code == expected_exit
    assert (notes / "planning" / filename).is_file()
    assert not (notes / "processing" / filename).exists()


@pytest.mark.parametrize("case", ["empty", "outside", "not_oldest", "mixed_state"])
def test_edit_entry_to_plan_rejects_invalid_material_set_without_changes(
    case: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提示素材の欠落、対象外、非最古及び状態混在は変換前に一括拒否する。"""
    notes = _setup_notes(tmp_path)
    (notes / "processing").mkdir()
    names = ("20260827-000000-001.md", "20260827-000000-002.md")
    for name in names:
        source = _write_feedback_file(notes, name)
        source.replace(notes / "planning" / name)
    materials = names
    filename = names[0]
    if case == "empty":
        materials = ()
    elif case == "outside":
        materials = (names[1],)
    elif case == "not_oldest":
        filename = names[1]
    elif case == "mixed_state":
        (notes / "planning" / names[1]).replace(notes / "processing" / names[1])
    plan = tmp_path / "main-plan.md"
    rows = "".join(f"- {name}\n" for name in materials)
    plan.write_text(f"## 提示素材\n\n{rows}", encoding="utf-8")
    _disable_transition_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))
    before = {
        path.relative_to(notes): path.read_text(encoding="utf-8")
        for state in ("planning", "processing")
        for path in (notes / state).iterdir()
    }

    with pytest.raises(mutations.WebInputError):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
        )

    after = {
        path.relative_to(notes): path.read_text(encoding="utf-8")
        for state in ("planning", "processing")
        for path in (notes / state).iterdir()
    }
    assert after == before
    assert not commit_calls


class TestStartProcessingMultiple:
    """start-processingサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_moved_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のstart-processingで両方がprocessing/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "start-processing", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        processing = notes / "processing"
        assert (processing / "fb-001.md").exists()
        assert (processing / "fb-002.md").exists()
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: start processing 2 entries" in commit_cmds[0]

    def test_mixed_target_repo_rejects_entire_batch_before_moving(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """集合内の1件でも`target_repo`が異なる場合は一括移動を開始しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "start-processing",
                    "fb-001.md",
                    "fb-002.md",
                    "--target-repo",
                    "github.com/example/foo",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "inbox" / "fb-002.md").exists()
        assert not (notes / "processing" / "fb-001.md").exists()
        assert not (notes / "processing" / "fb-002.md").exists()

    def test_missing_member_rejects_entire_batch_before_moving(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """集合内の1件でもinboxに存在しない場合は適合項目も移動しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "start-processing", "fb-001.md", "missing.md", "--target-repo", "github.com/example/foo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()
        assert not (notes / "processing" / "fb-001.md").exists()

    @pytest.mark.parametrize(
        ("invalid_frontmatter", "target_args"),
        [
            (
                "type: [\ntarget_repo: github.com/example/foo",
                ["--target-repo", "github.com/example/foo"],
            ),
            (
                "target_repo: github.com/example/foo",
                ["--target-repo", "github.com/example/foo"],
            ),
            ("type: feedback", []),
            (
                "type: unknown\ntarget_repo: github.com/example/foo",
                ["--target-repo", "github.com/example/foo"],
            ),
        ],
        ids=["malformed", "missing-type", "missing-target-repo", "invalid-type"],
    )
    def test_invalid_frontmatter_rejects_entire_batch_before_moving(
        self,
        invalid_frontmatter: str,
        target_args: list[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """集合内の1件で必須frontmatterが不正な場合は適合項目も移動しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        (notes / "inbox" / "fb-002.md").write_text(
            f"---\n{invalid_frontmatter}\n---\n\n本文\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "start-processing",
                    "fb-001.md",
                    "fb-002.md",
                    *target_args,
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "inbox" / "fb-002.md").exists()
        assert not (notes / "processing" / "fb-001.md").exists()
        assert not (notes / "processing" / "fb-002.md").exists()


class TestStartProcessingFailureBoundaries:
    """一括移動後のcommit・push失敗と限定復旧の境界を検証する。"""

    def test_commit_failure_leaves_only_requested_moves_and_commit_recovers_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """commit前失敗後は指定移動だけを`atk mq commit`で1回復旧できる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
        monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
        monkeypatch.setattr(mutations, "_pull", lambda _path: None)
        transition_calls: list[str] = []

        def fail_commit(*_args: object, **_kwargs: object) -> None:
            transition_calls.append("transition")
            raise subprocess.CalledProcessError(1, ["git", "commit"])

        monkeypatch.setattr(mutations, "_commit_and_push", fail_commit)
        with pytest.raises(subprocess.CalledProcessError):
            mutations.transition_entries(
                notes,
                action="start-processing",
                filenames=["fb-001.md", "fb-002.md"],
                target_repo="github.com/example/foo",
                now=_FIXED_DT,
            )

        assert transition_calls == ["transition"]
        assert sorted(path.name for path in (notes / "processing").iterdir()) == ["fb-001.md", "fb-002.md"]
        assert not list((notes / "inbox").iterdir())

        status_outputs = [" M processing/fb-001.md\n M processing/fb-002.md\n", ""]
        recovery_calls: list[tuple[object, ...]] = []
        push_calls: list[pathlib.Path] = []

        def fake_status(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            assert cmd[:3] == ["git", "status", "--porcelain"]
            return subprocess.CompletedProcess(cmd, 0, status_outputs.pop(0), "")

        def recover_commit(*args: object, **_kwargs: object) -> None:
            recovery_calls.append(args)

        monkeypatch.setattr(subprocess, "run", fake_status)
        monkeypatch.setattr(mutations, "_commit_and_push", recover_commit)
        monkeypatch.setattr(mutations, "_push_pending_commits", push_calls.append)
        assert mutations.commit_entries(notes) is True
        assert mutations.commit_entries(notes) is False
        assert len(recovery_calls) == 1
        assert push_calls == [notes, notes, notes]

    def test_push_failure_after_transition_commit_is_not_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """遷移commit後のpush失敗はcleanな未pushcommitを残し、完了扱いしない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        for state in ("processing", "adopted", "rejected"):
            (notes / state).mkdir()
        subprocess.run(["git", "init", "--initial-branch=main"], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "add", "."], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=notes, capture_output=True, text=True, check=True)
        remote = tmp_path / "origin.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(remote)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(
            ["git", "push", "--set-upstream", "origin", "main"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        monkeypatch.setattr(mutations, "_pull", lambda _path: None)
        before_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=notes, capture_output=True, text=True, check=True
        ).stdout.strip()
        calls: list[str] = []

        def fail_push(_path: pathlib.Path) -> None:
            calls.append("push")
            raise subprocess.CalledProcessError(1, ["git", "push"])

        monkeypatch.setattr(common, "_push_pending_commits", fail_push)
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            mutations.transition_entries(
                notes,
                action="start-processing",
                filenames=["fb-001.md", "fb-002.md"],
                target_repo="github.com/example/foo",
                now=_FIXED_DT,
            )

        assert exc_info.value.cmd == ["git", "push"]
        assert calls == ["push"]
        assert sorted(path.name for path in (notes / "processing").iterdir()) == ["fb-001.md", "fb-002.md"]
        assert not list((notes / "inbox").iterdir())
        after_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=notes, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert after_head != before_head
        status = subprocess.run(["git", "status", "--porcelain"], cwd=notes, capture_output=True, text=True, check=True)
        assert status.stdout == ""
        upstream_check = subprocess.run(
            ["git", "merge-base", "--is-ancestor", after_head, "@{u}"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=False,
        )
        assert upstream_check.returncode != 0


class TestStartProcessingMissing:
    """start-processingサブコマンド: 存在しないファイル指定でexit 2となる。"""

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "start-processing", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inboxに存在しません" in captured.err


class TestAdoptFromProcessing:
    """adopt: processing配下のファイルもadopted/へ移動できる。"""

    def test_adopt_from_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processing/配下のファイルがadopt対象に含まれadopted/へ移動する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        (processing / "fb-p.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-p.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing / "fb-p.md").exists()
        assert (notes / "adopted" / "fb-p.md").exists()


class TestRejectFromProcessing:
    """reject: processing配下のファイルもrejected/へ移動できる。"""

    def test_reject_from_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processing/配下のファイルがreject対象に含まれrejected/へ移動する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        (processing / "fb-p.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-p.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing / "fb-p.md").exists()
        assert (notes / "rejected" / "fb-p.md").exists()


class TestProcessingPrecedence:
    """同名ファイルがinbox・processing双方に存在する場合processingを優先する。"""

    def test_adopt_prefers_processing_when_both_exist(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """同名ファイルがinbox・processing双方に存在する場合、processing側が移動元として選ばれる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-dup.md")
        inbox_path = notes / "inbox" / "fb-dup.md"
        inbox_path.write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\ninbox本文\n",
            encoding="utf-8",
        )
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        processing_path = processing / "fb-dup.md"
        processing_path.write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\nprocessing本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-dup.md"], home=tmp_path)

        assert exc_info.value.code == 0
        # processing側が移動元として選ばれるため、inbox側は残存しprocessing側は消える。
        assert inbox_path.exists()
        assert not processing_path.exists()
        adopted_path = notes / "adopted" / "fb-dup.md"
        assert adopted_path.exists()
        # 実際に移動されたのはprocessing側の内容であることを確認する。
        assert "processing本文" in adopted_path.read_text(encoding="utf-8")


class TestTargetRepoVerification:
    """mutation系サブコマンド: `--target-repo`指定時のfrontmatter一致検証を検証する。

    既定のfrontmatter`target_repo`は`github.com/example/foo`（`_write_feedback_file`既定値）。
    """

    @pytest.mark.parametrize(
        "command",
        [
            ["mq", "edit", "fb-001.md", "編集後"],
            ["mq", "adopt", "fb-001.md"],
            ["mq", "reject", "fb-001.md"],
            ["mq", "start-processing", "fb-001.md"],
            ["mq", "rm", "fb-001.md"],
        ],
        ids=["edit", "adopt", "reject", "start-processing", "remove"],
    )
    @pytest.mark.parametrize("target_repo_kind", ["legacy-path", "canonical-url"])
    def test_active_entry_with_legacy_target_repo_accepts_equivalent_target(
        self,
        command: list[str],
        target_repo_kind: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """旧パス形の保存値は対応するパス形・URL形のどちらとも一致する。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "target-repo"
        local_repo.mkdir()
        _write_feedback_file(notes, "fb-001.md", target_repo=str(local_repo), body="編集前")
        target_repo = {
            "legacy-path": str(local_repo),
            "canonical-url": "github.com/example/repo",
        }[target_repo_kind]
        git_calls: list[_GitCall] = []
        fallback = _make_subprocess_fake(git_calls)

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/repo.git\n", "")
            return fallback(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main([*command, "--target-repo", target_repo], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

    @pytest.mark.parametrize("target_repo_kind", ["legacy-path", "canonical-url"])
    def test_processing_entry_with_legacy_target_repo_accepts_equivalent_target_on_return(
        self,
        target_repo_kind: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """return-to-inboxも旧パス形の保存値をパス形・URL形で検証できる。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "target-repo"
        local_repo.mkdir()
        path = _write_feedback_file(notes, "fb-001.md", target_repo=str(local_repo))
        processing = notes / "processing"
        processing.mkdir()
        path.replace(processing / path.name)
        target_repo = {
            "legacy-path": str(local_repo),
            "canonical-url": "github.com/example/repo",
        }[target_repo_kind]
        fallback = _make_subprocess_fake([])

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/repo.git\n", "")
            return fallback(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "return-to-inbox", "fb-001.md", "--target-repo", target_repo],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0

    @pytest.mark.parametrize(
        "command",
        [
            ["mq", "edit", "fb-001.md", "編集後"],
            ["mq", "adopt", "fb-001.md"],
            ["mq", "reject", "fb-001.md"],
            ["mq", "start-processing", "fb-001.md"],
            ["mq", "rm", "fb-001.md"],
        ],
        ids=["edit", "adopt", "reject", "start-processing", "remove"],
    )
    def test_active_entry_with_legacy_target_repo_rejects_different_target(
        self,
        command: list[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """旧パス形の保存値は別リポジトリのURL形と一致しない。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "target-repo"
        local_repo.mkdir()
        _write_feedback_file(notes, "fb-001.md", target_repo=str(local_repo), body="編集前")
        fallback = _make_subprocess_fake([])

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/repo.git\n", "")
            return fallback(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main([*command, "--target-repo", "github.com/example/other"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert "target_repo不一致" in capsys.readouterr().err

    def test_processing_entry_with_legacy_target_repo_rejects_different_target_on_return(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """return-to-inboxも旧パス形の保存値を別リポジトリとは判定しない。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "target-repo"
        local_repo.mkdir()
        path = _write_feedback_file(notes, "fb-001.md", target_repo=str(local_repo))
        processing = notes / "processing"
        processing.mkdir()
        path.replace(processing / path.name)
        fallback = _make_subprocess_fake([])

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/repo.git\n", "")
            return fallback(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "return-to-inbox", "fb-001.md", "--target-repo", "github.com/example/other"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 2
        assert "target_repo不一致" in capsys.readouterr().err

    def test_unresolvable_saved_target_repo_exits_2_without_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """解決不能な保存値は不一致としてTraceback無しのexit 2で拒否する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo=str(tmp_path / "missing-repo"))
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "adopt", "fb-001.md", "--target-repo", "github.com/example/repo"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "target_repo不一致" in captured.err
        assert "Traceback" not in captured.err

    @pytest.mark.parametrize(
        "command",
        [
            ["mq", "adopt", "fb-dup.md"],
            ["mq", "reject", "fb-dup.md"],
            ["mq", "rm", "--force", "fb-dup.md"],
        ],
        ids=["adopt", "reject", "remove"],
    )
    def test_resolved_processing_entry_is_verified_when_same_name_exists_in_inbox(
        self,
        command: list[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同名併存時は実際の操作対象であるprocessing側を検証する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-dup.md", target_repo="github.com/example/foo")
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        processing_path = processing / "fb-dup.md"
        processing_path.write_text(
            "---\ntarget_repo: github.com/example/other\ntype: feedback\n---\n\nprocessing本文\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main([*command, "--target-repo", "github.com/example/foo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "target_repo不一致" in capsys.readouterr().err
        assert processing_path.exists()

    def test_edit_verifies_resolved_processing_entry_when_same_name_exists_in_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """editの事前検査も解決済みprocessing実体へ適用する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-dup.md", target_repo="github.com/example/foo")
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        processing_path = processing / "fb-dup.md"
        processing_path.write_text(
            "---\ntarget_repo: github.com/example/other\ntype: feedback\n---\n\nprocessing本文\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("EDITOR", "fake-editor")
        editor_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "edit", "fb-dup.md", "--target-repo", "github.com/example/foo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert "target_repo不一致" in capsys.readouterr().err
        assert not editor_calls

    def test_adopt_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """adopt: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "target_repo不一致" in captured.err
        assert (notes / "inbox" / "fb-001.md").exists()
        assert not (notes / "adopted" / "fb-001.md").exists()

    def test_adopt_match_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """adopt: `--target-repo`一致時は通常通りadopted/へ移動する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--target-repo", "github.com/example/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (notes / "adopted" / "fb-001.md").exists()

    def test_reject_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """reject: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_rm_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rm: `--target-repo`不一致時にexit 2でファイルは削除されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_start_processing_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "start-processing", "fb-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_edit_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """edit: `--target-repo`不一致時にexit 2でエディターは起動されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")
        editor_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert not editor_calls

    def test_unspecified_target_repo_is_noop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--target-repo`未指定時は検証されず既存挙動のまま処理が進む。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (notes / "adopted" / "fb-001.md").exists()

    def test_adopt_bare_stem_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """adopt: 拡張子.md省略入力でも`--target-repo`不一致時に検証が回避されない（回帰確認）。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001", "--target-repo", "github.com/other/repo"], home=tmp_path)
        assert exc_info.value.code == 2
        assert not (notes / "adopted" / "fb-001.md").exists()


class TestPathTraversalRejection:
    """パストラバーサル系の不正引数は早期に拒否されること。"""

    @pytest.mark.parametrize(
        "bad",
        [
            "../escape.md",
            "subdir/file.md",
            "/abs/path.md",
            "..\\windows.md",
            "..",
            ".",
            "",
        ],
    )
    def test_rejects_bad_filenames(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        bad: str,
    ) -> None:
        """不正なファイル名引数はexit 2でstderr案内を出力する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", bad], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err or "基準ディレクトリ外" in captured.err
