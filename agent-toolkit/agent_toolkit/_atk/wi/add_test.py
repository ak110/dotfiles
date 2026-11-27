# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

# pylint: disable=protected-access,subprocess-run-check

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config
from agent_toolkit._atk.config_repositories import BranchSettings
from agent_toolkit._atk.wi import submit
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_add_support import _patch_add_editor, _patch_add_git
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes


@pytest.mark.parametrize("short", [False, True])
@pytest.mark.parametrize(
    "case", ["upstream", "advanced", "missing-upstream", "local", "other-repo", "other-branch", "other-line"]
)
def test_draft_base_is_checked_on_dry_run_and_add(tmp_path, capsys, monkeypatch, case, short):
    notes = git_repository.init_repository(_setup_notes(tmp_path), commit_message="notes")
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="base")
    older = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.run_git(repo, "remote", "add", "origin", "https://github.com/example/draft-base.git")
    isolated = tmp_path / "advanced"
    git_repository.run_git(repo, "worktree", "add", "--detach", str(isolated))
    (isolated / "upstream.txt").write_text("取得した上流\n", encoding="utf-8")
    latest = git_repository.commit_all(isolated, "upstream")
    if case != "local":
        git_repository.run_git(repo, "config", "branch.main.remote", "origin")
        git_repository.run_git(repo, "config", "branch.main.merge", "refs/heads/main")
        if case != "missing-upstream":
            git_repository.run_git(repo, "update-ref", "refs/remotes/origin/main", latest)
    base = older if case in {"local", "other-branch", "other-line"} else latest
    branch = "feature" if case in {"other-branch", "other-line"} else "main"
    if case in {"other-branch", "other-line"}:
        git_repository.run_git(repo, "branch", branch, older)
    if case == "other-line":
        monkeypatch.setattr(
            config,
            "repository_branch_settings",
            lambda: {"github.com/example/draft-base": BranchSettings(("main", "feature"), "main", {})},
        )
    body = tmp_path / "body.md"
    text = "# 調査した要求\n\n## 反映内容と反映先\n\n上流の記述を読んで本文を更新する。\n"
    if case == "other-repo":
        text = "---\ntarget_repo: github.com/example/another\n---\n\n" + text
    body.write_text(text, encoding="utf-8")
    args = [
        "wi",
        "add",
        "--target-repo",
        str(repo),
        "--body-file",
        str(body),
        "--source",
        "human",
        "--draft-base",
        branch if short else f"refs/heads/{branch}",
        base,
    ]

    def add(options):
        with pytest.raises(SystemExit) as status:
            atk.main([*args, *options], home=tmp_path, now=_FIXED_DT)
        return status.value.code

    if case in {"missing-upstream", "other-repo", "other-branch", "other-line"}:
        assert add(["--dry-run"]) != 0
        assert add([]) != 0
        expected = {
            "missing-upstream": "宛先refを解決できない",
            "other-repo": "別repoまたは別系統",
            "other-branch": "宛先worktreeの現在のブランチと一致しない",
            "other-line": "登録済みの宛先系統と一致しない",
        }[case]
        assert expected in capsys.readouterr().err
        assert not tuple(notes.rglob("*.md"))
        return

    assert add(["--dry-run"]) == 0
    assert not tuple(notes.rglob("*.md"))
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == older
    if case == "advanced":
        git_repository.run_git(repo, "update-ref", "refs/remotes/origin/main", older)
        assert add([]) == 1
        assert "起草の基点が取得済みの宛先refと一致しない" in capsys.readouterr().err
        assert not tuple(notes.rglob("*.md"))
    else:
        assert add([]) == 0
        (saved,) = notes.rglob("*.md")
        assert f"target_commit: {base}" in saved.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("branch", "candidate"),
    [("unknown", None), ("origin/main", "refs/heads/main"), ("feature/x", "refs/heads/feature/x")],
)
def test_draft_base_branch_diagnostic(tmp_path, capsys, branch, candidate):
    _setup_notes(tmp_path)
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="base")
    oid = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.run_git(repo, "remote", "add", "origin", "https://github.com/example/draft-base.git")
    git_repository.run_git(repo, "branch", "feature/x")
    body = tmp_path / "body.md"
    body.write_text("# 調査した要求\n\n## 反映内容と反映先\n\n上流の記述を読んで本文を更新する。\n", encoding="utf-8")

    with pytest.raises(SystemExit) as status:
        atk.main(
            ["wi", "add", str(repo), "--body-file", str(body), "--source", "human", "--draft-base", branch, oid, "--dry-run"],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert status.value.code == 1
    message = capsys.readouterr().err
    assert "refs/heads/<ブランチ>" in message
    assert "を含まないブランチ名" in message
    if candidate:
        assert f"{candidate} を指定する" in message


class TestAddEmptyBodyRejection:
    @pytest.mark.parametrize("body", ["", "-", "-\n-\n"])
    def test_empty_body_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        body: str,
    ) -> None:
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        _patch_add_git(monkeypatch, myrepo)
        body_path = tmp_path / "body.md"
        body_path.write_text(body, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "実質空" in captured.err
        assert "\n次の操作: 本文を記入して再投入する" in captured.err
        assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))

    def test_valid_body_with_content_still_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        _patch_add_git(monkeypatch, myrepo)
        body_path = tmp_path / "body.md"
        body_path.write_text("- 理由: 動作確認済みのため採用", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))

    def test_editor_confirmed_empty_body_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        _patch_add_git(monkeypatch, myrepo)
        _patch_add_editor(monkeypatch, "-\n")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1


class TestAddBodyFile:
    def test_body_file_content_becomes_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        repo = tmp_path / "myrepo"
        repo.mkdir()
        _patch_add_git(monkeypatch, repo)

        body = '### 見出し\n\n- `バッククォート` と $変数 と "引用符" を含む本文\n'
        body_path = tmp_path / "body.md"
        body_path.write_text(body, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
        assert body.strip() in content

    def test_body_file_repeats_for_multiple_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        repo = tmp_path / "myrepo"
        repo.mkdir()
        _patch_add_git(monkeypatch, repo)
        first = tmp_path / "first.md"
        second = tmp_path / "second.md"
        first.write_text("1件目の本文\n", encoding="utf-8")
        second.write_text("2件目の本文\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", "--body-file", str(first), "--body-file", str(second)],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        bodies = sorted(path.read_text(encoding="utf-8") for path in notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
        assert len(bodies) == 2
        assert any("1件目の本文" in body for body in bodies)
        assert any("2件目の本文" in body for body in bodies)

    def test_body_file_accepts_existing_file_path_as_content(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        repo = tmp_path / "myrepo"
        repo.mkdir()
        _patch_add_git(monkeypatch, repo)
        message_file = tmp_path / "message.txt"
        message_file.write_text("本文ファイルが参照する既存ファイル", encoding="utf-8")
        body_path = tmp_path / "body.md"
        body_path.write_text(str(message_file), encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
        assert f"\n{message_file}\n" in content

    def test_positional_message_is_rejected(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _setup_notes(tmp_path)
        body_path = tmp_path / "body.md"
        body_path.write_text("本文\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--body-file", str(body_path), "本文"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "余った位置引数:" in captured.err

    def test_body_file_missing_path_rejected(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """読み込みに失敗するパスを指定した場合は投入せず非ゼロ終了する。"""
        _setup_notes(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--body-file", str(tmp_path / "missing.md")], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "--body-file" in captured.err


@pytest.mark.parametrize(
    ("context", "title", "timing", "accepted"),
    [
        ("source", "不要な旧レーンを回収する", [], False),
        ("process-loop", "次回へ送る判断を是正する", [], False),
        ("frontmatter", "本文で巡回を明示する", [], False),
        ("source", "今回へ追加する", ["--process-wi-timing", "this-run"], True),
        ("source", "専用見出しなしで次回へ送る", ["--process-wi-timing", "next-run"], True),
        ("ordinary", "通常の投入にも今回を指定できる", ["--process-wi-timing", "this-run"], True),
        ("ordinary", "通常の投入", [], True),
    ],
)
def test_process_wi_timing_at_add_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    context: str,
    title: str,
    timing: list[str],
    accepted: bool,
) -> None:
    notes = _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    _patch_add_git(monkeypatch, myrepo)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    extra = []
    if context == "process-loop":
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    elif context == "source":
        extra = ["--source", "process-wi"]
    body = f"# {title}\n\n## 反映内容と反映先\n\n既存の処理結果を今の条件で確かめる。\n"
    if context == "frontmatter":
        body = f"---\nsource: process-wi\n---\n{body}"
    body_path = tmp_path / "timing.md"
    body_path.write_text(body, encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "add", str(myrepo), "--body-file", str(body_path), *extra, *timing], home=tmp_path, now=_FIXED_DT)

    captured = capsys.readouterr()
    saved = list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    assert exc_info.value.code == (0 if accepted else 1), captured.err
    assert bool(saved) == accepted
    if not accepted:
        assert "--process-wi-timing" in captured.err
    if accepted and "this-run" in timing:
        assert "pick-wi-check --merge" in captured.err
        assert "start-processing" in captured.err
        assert "担当へ追送" in captured.err


def test_batch_rejects_process_wi_timing(capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "add", "--batch", "--process-wi-timing", "this-run"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 2
    assert "--process-wi-timingは--batchと併用できません" in capsys.readouterr().err


def test_process_wi_dry_run_requires_the_same_timing_as_save(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = _setup_notes(tmp_path)
    body = tmp_path / "body.md"
    body.write_text("---\nsource: process-wi\n---\n# 見出し\n\n## 反映内容と反映先\n\n前提を確認する。\n", encoding="utf-8")
    messages = []
    for options in (["--dry-run"], []):
        with pytest.raises(SystemExit) as status:
            atk.main(
                ["wi", "add", "--target-repo", "github.com/example/repo", "--body-file", str(body), *options],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert status.value.code == 1
        messages.append(capsys.readouterr().err)
    assert messages[0] == messages[1]
    assert "--process-wi-timing" in messages[0]
    assert not tuple(notes.rglob("*.md"))


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "hold",
        "missing-run",
        "missing-receipt",
        "unaccepted-request",
        "wrong-repo",
        "completed",
        "failed",
        "loop",
        "flag",
    ],
)
def test_process_wi_submission_bindings_at_add_boundary(tmp_path, monkeypatch, capsys, case):
    notes = git_repository.init_repository(_setup_notes(tmp_path), commit_message="notes")
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    target = "github.com/example/repo"
    run_id = "a" * 32
    request_id = "receipt-1"
    if case != "missing-run":
        run_path = submit._run_path(run_id)
        run_path.parent.mkdir(parents=True)
        run_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "request_ids": [] if case == "unaccepted-request" else [request_id],
                    "target_repo": target,
                    "status": case if case in {"completed", "failed"} else "running",
                }
            ),
            encoding="utf-8",
        )
    if case != "missing-receipt":
        receipt_path = submit._receipt_path(request_id)
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "accepted_at": 1,
                    "request": {
                        "request_id": request_id,
                        "target_repo": "github.com/example/other" if case == "wrong-repo" else target,
                    },
                }
            ),
            encoding="utf-8",
        )
    body = tmp_path / "body.md"
    bindings = json.dumps({run_id: [request_id]})
    body.write_text(
        f"---\ntarget_repo: {target}\nsource: process-wi\nwi_submit_bindings: {bindings}\n---\n"
        "# 受付からの変更\n\n## 反映内容と反映先\n\n受付が確認した要件を保存する。\n",
        encoding="utf-8",
    )
    if case == "loop":
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    extra = ["--source", "process-wi"] if case == "flag" else ["--hold"] if case == "hold" else []
    for options in (["--dry-run"], []):
        with pytest.raises(SystemExit) as status:
            atk.main(
                ["wi", "add", "--target-repo", "github.com/example/fallback", "--body-file", str(body), *extra, *options],
                home=tmp_path,
                now=_FIXED_DT,
            )
        captured = capsys.readouterr()
        if case in {"valid", "hold"}:
            assert status.value.code == 0, captured.err
            assert "pick-wi-check --merge" not in captured.err
        else:
            assert status.value.code == 1
            assert "--process-wi-timing" in captured.err
        if options or case not in {"valid", "hold"}:
            assert not tuple(notes.rglob("*.md"))
        else:
            (saved,) = notes.rglob("*.md")
            assert "source: process-wi" in saved.read_text(encoding="utf-8")
