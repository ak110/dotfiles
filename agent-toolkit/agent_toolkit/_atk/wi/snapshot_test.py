import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config
from agent_toolkit._atk.wi import listing, snapshot
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing import git_repository
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


@pytest.mark.parametrize("configured", [False, True])
def test_list_snapshot_precedes_filters(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], configured: bool
) -> None:
    notes = _setup_notes(tmp_path)
    for state in ("inbox", "processing", "hold", "adopted", "rejected"):
        path = _write_awi_file(notes, f"{state}.md", source="human")
        path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", f"state: {state}"), encoding="utf-8")
    _write_uwi_file(notes, "unanswered.md", answer="")
    _write_awi_file(notes, "20261110-123456-001.md")
    _write_awi_file(notes, "20261110-083456-001.md")
    _write_awi_file(notes, "other.md", target_repo="github.com/example/other")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    target = ["--configured-repos"] if configured else ["--target-repo=github.com/example/foo"]
    if configured:
        repository = config.ConfiguredRepository("github.com/example/foo", tmp_path, tmp_path / ".git")
        monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    baseline = tmp_path / "baseline.json"
    with pytest.raises(SystemExit) as result:
        atk.main(
            [
                "wi",
                "list",
                "--skip-pull",
                "--count",
                "--state=inbox",
                "--type=awi",
                "--source=human",
                *target,
                "--snapshot-output",
                str(baseline),
            ],
            home=tmp_path,
        )
    assert result.value.code == 0
    assert capsys.readouterr().out == "1\n"
    saved = baseline.read_bytes()
    records = json.loads(saved)
    names = {item["filename"] for item in records}
    assert names == {
        "inbox.md",
        "processing.md",
        "hold.md",
        "adopted.md",
        "rejected.md",
        "unanswered.md",
        "20261110-123456-001.md",
        "20261110-083456-001.md",
    } | (set() if configured else {"other.md"})
    assert all(set(item) == {"target_repo", "filename"} for item in records)
    for path in notes.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for state in ("processing", "hold", "adopted", "rejected"):
            text = text.replace(f"state: {state}", "state: inbox")
        path.write_text(text, encoding="utf-8")
    new_names = {"20261110-123456-002.md", "20261109-233456-001.md"}
    for name in new_names:
        _write_awi_file(notes, name, source="human")
    _write_uwi_file(notes, "new-uwi.md", answer="回答", source="human")
    _write_awi_file(notes, "new-agent.md", source="agent")
    for output in ("--jsonl", "--summary-only", "--count", "--no-jsonl"):
        with pytest.raises(SystemExit) as result:
            atk.main(
                [
                    "wi",
                    "list",
                    "--skip-pull",
                    output,
                    *target,
                    "--state=inbox",
                    "--type=awi",
                    "--source=human",
                    "--new-since",
                    str(baseline),
                ],
                home=tmp_path,
            )
        assert result.value.code == 0
        captured = capsys.readouterr()
        assert not captured.err
        if output == "--count":
            assert captured.out == "2\n"
        elif output == "--no-jsonl":
            assert all(name in captured.out for name in new_names)
            assert "adopted.md" not in captured.out
        else:
            assert {json.loads(line)["filename"] for line in captured.out.splitlines()} == new_names
        assert baseline.read_bytes() == saved


@pytest.mark.parametrize(
    "content",
    [
        None,
        "{",
        "{}",
        '[{"filename":"x.md"}]',
        '[{"target_repo":3,"filename":"x.md"}]',
        '[{"target_repo":"bad","filename":"x.md"}]',
        '[{"target_repo":"","filename":"../x.md"}]',
    ],
)
def test_list_rejects_invalid_baseline(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    baseline = tmp_path / "baseline.json"
    if content is not None:
        baseline.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "list", "--skip-pull", "--new-since", str(baseline)], home=tmp_path)
    assert result.value.code != 0
    captured = capsys.readouterr()
    assert "開始時集合" in captured.err and str(baseline) in captured.err
    assert not captured.out
    if content is not None:
        assert baseline.read_text(encoding="utf-8") == content
    else:
        assert not baseline.exists()


def test_list_snapshot_normalizes_repo_and_preserves_broken_entries(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    baseline = tmp_path / "baseline.json"
    command = ["wi", "list", "--skip-pull", "--target-repo=all", "--count"]
    with pytest.raises(SystemExit) as result:
        atk.main([*command, "--snapshot-output", str(baseline)], home=tmp_path)
    assert result.value.code == 0
    assert not json.loads(baseline.read_text(encoding="utf-8"))
    capsys.readouterr()
    item = _write_awi_file(notes, "url.md", target_repo="https://GitHub.com/Example/Foo.git")
    broken = _write_awi_file(notes, "broken.md")
    broken.write_text("---\n[broken\n", encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        atk.main([*command, "--snapshot-output", str(baseline)], home=tmp_path)
    assert result.value.code == 0
    assert {tuple(sorted(record.items())) for record in json.loads(baseline.read_text(encoding="utf-8"))} == {
        (("filename", "url.md"), ("target_repo", "github.com/example/foo")),
        (("filename", "broken.md"), ("target_repo", "")),
    }
    capsys.readouterr()
    item.write_text(
        item.read_text(encoding="utf-8").replace("https://GitHub.com/Example/Foo.git", "github.com/example/foo"),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as result:
        atk.main([*command, "--new-since", str(baseline)], home=tmp_path)
    assert result.value.code == 0
    assert capsys.readouterr().out == "0\n"


@pytest.mark.parametrize("configured", [False, True])
def test_process_loop_baseline_finds_new_merge_work_item(tmp_path, monkeypatch, capsys, configured):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repository = config.ConfiguredRepository("github.com/example/foo", tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "snapshot-loop")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "snapshot-loop")
    before = "20261123-140000-001.md"
    new = "merge-lane.md"
    outside = "20261123-145959-002.md"
    _write_awi_file(notes, before, source="implement")
    target = "--configured-repos" if configured else "--target-repo=github.com/example/foo"
    baseline = tmp_path / "snapshot.json"
    with pytest.raises(SystemExit) as invocation:
        atk.main(
            ["wi", "list", "--skip-pull", "--count", target, "--snapshot-output", str(baseline)],
            home=tmp_path,
        )
    assert invocation.value.code == 0
    capsys.readouterr()
    original = baseline.read_bytes()
    path = _write_awi_file(notes, new, source="implement")
    path.write_text(
        path.read_text(encoding="utf-8").replace("state: inbox", "state: inbox\nmerge_branch: worktree-done"),
        encoding="utf-8",
    )
    _write_awi_file(notes, outside, target_repo="github.com/example/other", source="implement")

    later_baseline = tmp_path / "later-snapshot.json"
    with pytest.raises(SystemExit) as invocation:
        atk.main(["wi", "list", "--skip-pull", "--count", target, "--snapshot-output", str(later_baseline)], home=tmp_path)
    assert invocation.value.code == 0
    capsys.readouterr()
    assert listing.progress_new_wis("snapshot-loop") == (listing.NewWorkItem(new, "implement", True),)
    assert baseline.read_bytes() == original
    assert later_baseline.read_bytes() == original
    (row,) = snapshot.progress_sources("snapshot-loop").values()
    pathlib.Path(row["path"]).write_text("[]", encoding="utf-8")
    with pytest.raises(ActionableError, match="開始時の保存後に内容が変化"):
        listing.progress_new_wis("snapshot-loop")
    with pytest.raises(SystemExit) as rejected:
        atk.main(
            ["wi", "list", "--skip-pull", "--count", target, "--snapshot-output", str(later_baseline)],
            home=tmp_path,
        )
    assert rejected.value.code != 0
    assert later_baseline.read_bytes() == original


def test_process_loop_progress_reads_new_merge_work_item_from_upstream(tmp_path, monkeypatch, capsys):
    notes = _setup_notes(tmp_path)
    server = git_repository.init_bare_repository(tmp_path / "origin.git")
    git_repository.init_repository(notes, initial_branch="main")
    before = "20261123-140000-001.md"
    _write_awi_file(notes, before, source="implement")
    git_repository.commit_all(notes, "begin")
    git_repository.run_git(notes, "remote", "add", "origin", str(server))
    git_repository.run_git(notes, "push", "-u", "origin", "main")
    repository = config.ConfiguredRepository("github.com/example/foo", tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "snapshot-remote")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "snapshot-remote")
    baseline = tmp_path / "snapshot.json"
    with pytest.raises(SystemExit) as invocation:
        atk.main(
            ["wi", "list", "--skip-pull", "--count", "--configured-repos", "--snapshot-output", str(baseline)],
            home=tmp_path,
        )
    assert invocation.value.code == 0
    capsys.readouterr()
    other = tmp_path / "publisher"
    git_repository.run_git(tmp_path, "clone", "-b", "main", str(server), str(other))
    new = "20261123-145959-001.md"
    path = _write_awi_file(other, new, source="implement")
    text = path.read_text(encoding="utf-8").replace("state: inbox", "state: inbox\nmerge_branch: worktree-done")
    path.write_text(text, encoding="utf-8")
    git_repository.commit_all(other, "publish")
    git_repository.run_git(other, "push")
    assert not tuple(notes.rglob(new))
    assert listing.progress_new_wis("snapshot-remote") == (listing.NewWorkItem(new, "implement", True),)


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("save_failed", [False, True])
def test_first_circuit_list_contract(tmp_path, monkeypatch, configured, explicit, save_failed):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repo = "github.com/example/foo"
    repository = config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git", "main")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda: (repository,))
    session = "first-list-contract"
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", session)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    expected = set()
    for branch, number in (("main", "001"), ("other", "002")):
        item = _write_awi_file(notes, f"20261124-085015-{number}.md")
        text = item.read_text(encoding="utf-8").replace("state: inbox", f"state: inbox\ntarget_branch: {branch}")
        item.write_text(text, encoding="utf-8")
        if not configured or branch == "main":
            expected.add((repo, item.name))
    output = tmp_path / "first.json"
    output.write_text("以前の出力", encoding="utf-8")
    args = ["--snapshot-output", str(output)] if explicit else []
    scope = "--configured-repos" if configured else f"--target-repo={repo}"

    def reject_write(*_args):
        raise OSError("テスト用の保存失敗")

    with monkeypatch.context() as first:
        if save_failed:
            first.setattr(snapshot, "atomic_write", reject_write)
        with pytest.raises(SystemExit) as result:
            atk.main(["wi", "list", "--skip-pull", "--count", "--status=processable", scope, *args], home=tmp_path)
    assert (result.value.code == 0) == (not explicit or not save_failed)
    if explicit and not save_failed:
        assert snapshot.read(output) == expected
    else:
        assert output.read_text(encoding="utf-8") == "以前の出力"
    assert bool(snapshot.progress_sources(session)) != save_failed
