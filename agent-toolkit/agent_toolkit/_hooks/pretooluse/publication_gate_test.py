import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config, config_repositories
from agent_toolkit._atk.wi import listing, snapshot
from agent_toolkit._common import session_state
from agent_toolkit._hooks import progress_self_check
from agent_toolkit._hooks.pretooluse import dispatch
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file


def _register_circuit(tmp_path, monkeypatch, owner):
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", owner)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", owner)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    with pytest.raises(SystemExit) as result:
        atk.main(
            [
                "wi",
                "list",
                "--skip-pull",
                "--count",
                "--configured-repos",
                "--snapshot-output",
                str(tmp_path / "baseline.json"),
            ],
            home=tmp_path,
        )
    assert result.value.code == 0


@pytest.mark.parametrize("first_configured", [False, True])
@pytest.mark.parametrize("handoff", [None, "target", "configured"])
@pytest.mark.parametrize("failure", [None, "snapshot", "source", "session_state", "directory"])
def test_single_lane_list_preserves_start_of_circuit(tmp_path, monkeypatch, capsys, first_configured, handoff, failure):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repo = "github.com/example/foo"
    repository = config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    session = "single-lane-start"
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", session)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    held = _write_awi_file(notes, "20261124-085010-001.md")
    original = held.read_text(encoding="utf-8")
    held.write_text(original.replace("state: inbox", "state: hold"), encoding="utf-8")

    outside = _write_awi_file(notes, "20261124-085010-003.md", target_repo="github.com/example/outside")

    def list_candidates(configured, output=None, *, success=True):
        target = "--configured-repos" if configured else f"--target-repo={repo}"
        options = ["--snapshot-output", str(output)] if output is not None else []
        with pytest.raises(SystemExit) as result:
            atk.main(["wi", "list", "--skip-pull", "--count", "--status=processable", target, *options], home=tmp_path)
        assert (result.value.code == 0) is success

    with monkeypatch.context() as first_list:

        def fail_write(*_args):
            raise OSError("テスト用の保存失敗")

        if failure == "directory":
            first_list.setattr(snapshot, "_claim_start", fail_write)
        elif failure == "snapshot":
            first_list.setattr(snapshot, "atomic_write", fail_write)
        elif failure == "source":
            write = snapshot.atomic_write

            def fail_source(path, data):
                if path.name == "source.json":
                    fail_write()
                write(path, data)

            first_list.setattr(snapshot, "atomic_write", fail_source)
        elif failure == "session_state":
            first_list.setattr(session_state, "_atomic_write", fail_write)
        list_candidates(first_configured)

    sources = snapshot.progress_sources(session)
    if failure not in {"directory", "snapshot", "source"}:
        (row,) = sources.values()
        assert snapshot.read(pathlib.Path(row["path"])) == {(repo, held.name), ("github.com/example/outside", outside.name)}
    else:
        assert "保存できない" in capsys.readouterr().err
    held.write_text(original, encoding="utf-8")
    new_name = "20261124-085010-002.md"
    new = _write_awi_file(notes, new_name)
    new.write_text(
        new.read_text(encoding="utf-8").replace("state: inbox", "state: inbox\nmerge_branch: test"),
        encoding="utf-8",
    )
    listing_copy = tmp_path / "for-delegate.json"
    list_candidates(not first_configured)
    if handoff is not None:
        broken = failure in {"directory", "snapshot", "source"}
        if broken:
            listing_copy.write_text("以前の出力", encoding="utf-8")
        list_candidates(handoff == "configured", listing_copy, success=not broken)
        if broken:
            assert listing_copy.read_text(encoding="utf-8") == "以前の出力"
        else:
            expected = {(repo, held.name)}
            if handoff == "target":
                expected.add(("github.com/example/outside", outside.name))
            assert snapshot.read(listing_copy) == expected
    assert snapshot.progress_sources(session) == sources
    payload = {"session_id": session, "cwd": str(tmp_path)}
    if failure in {"directory", "snapshot", "source"}:
        assert listing.progress_new_wis(session) is None
        return
    assert progress_self_check.claim_notice(payload, now=1.0) is None
    notice = progress_self_check.claim_notice(payload, now=1801.0)
    assert notice is not None and new_name in notice and held.name not in notice


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("legacy_file", ["valid", "missing", "changed"])
def test_circuit_resume_uses_legacy_start(tmp_path, monkeypatch, configured, explicit, legacy_file):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repo = "github.com/example/foo"
    repository = config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda: (repository,))
    session = "legacy-start"
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", session)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    old = _write_awi_file(notes, "20261124-085011-001.md")
    path = tmp_path / "legacy.json"
    digest = snapshot.save(path, ((old, repo, old.read_text(encoding="utf-8"), "inbox", "awi"),))
    row: dict[str, object] = {
        "repo": repo,
        "branch": None,
        "path": str(path),
        "notes": str(notes),
        "digest": digest,
        "unanswered": [],
    }
    legacy = {'["github.com/example/foo", null]': row}
    assert session_state.update_state(session, lambda state: {**state, "process_loop_progress_snapshots": legacy})
    if legacy_file == "changed":
        path.write_text("[]", encoding="utf-8")
    elif legacy_file == "missing":
        path.unlink()

    late = _write_awi_file(notes, "20261124-085011-002.md")
    target = "--configured-repos" if configured else f"--target-repo={repo}"
    output = tmp_path / "delegate.json"
    output.write_text("以前の出力", encoding="utf-8")
    args = ["--snapshot-output", str(output)] if explicit else []
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "list", "--skip-pull", "--count", "--status=processable", target, *args], home=tmp_path)
    valid = legacy_file == "valid"
    assert (result.value.code == 0) == (valid or not explicit)
    if valid and explicit:
        assert snapshot.read(output) == {(repo, old.name)}
    else:
        assert output.read_text(encoding="utf-8") == "以前の出力"

    assert snapshot.progress_sources(session) == legacy
    payload = {"session_id": session, "cwd": str(tmp_path)}
    assert progress_self_check.claim_notice(payload, now=1.0) is None
    notice = progress_self_check.claim_notice(payload, now=1801.0)
    assert notice is not None
    assert (late.name in notice) == valid
    assert ("新着WIは未確認" in notice) != valid


@pytest.mark.parametrize("destination", ["develop", "maintenance", "feature/work"])
def test_new_wis_survive_branch_registration_during_the_same_conversation(tmp_path, monkeypatch, destination):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repo = "github.com/example/foo"
    configured = (config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git"),)
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: configured)
    old = _write_awi_file(notes, "20261124-085010-001.md")
    session = "register-while-resident"
    _register_circuit(tmp_path, monkeypatch, session)
    saved = snapshot.progress_sources(session)
    originals = {row["path"]: pathlib.Path(row["path"]).read_bytes() for row in saved.values()}
    configured = tuple(
        config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git", branch=branch, default_branch="develop")
        for branch in ("develop", "maintenance")
    )
    settings = config_repositories.BranchSettings(("develop", "maintenance"), "develop", {})
    monkeypatch.setattr(config, "repository_branch_settings", lambda: {repo: settings})
    names = {"develop": "20261124-085011-001.md", "maintenance": "20261124-085011-002.md"}
    for branch, name in names.items():
        item = _write_awi_file(notes, name)
        item.write_text(
            item.read_text(encoding="utf-8").replace("state: inbox", f"state: inbox\ntarget_branch: {branch}"),
            encoding="utf-8",
        )
    outside = _write_awi_file(notes, "20261124-085011-003.md", target_repo="github.com/example/outside")
    payload = {"session_id": session, "cwd": str(tmp_path)}
    assert progress_self_check.claim_notice(payload, now=1.0) is None
    notice = progress_self_check.claim_notice(payload, now=1801.0)
    assert notice is not None and all(notice.count(name) == 1 for name in names.values())
    assert old.name not in notice and outside.name not in notice
    rows = listing.progress_new_wis(session, targets=frozenset({(repo, destination)}))
    assert rows is not None
    expected = {names[destination]} if destination in names else set(names.values())
    assert {row.filename for row in rows} == expected
    assert snapshot.progress_sources(session) == saved
    assert {path: pathlib.Path(path).read_bytes() for path in originals} == originals


def test_removed_branch_is_unobservable_and_cannot_be_acknowledged_as_no_new_wis(tmp_path, monkeypatch):
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repo = "github.com/example/foo"
    configured = (config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git", branch="develop"),)
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: configured)
    settings = config_repositories.BranchSettings(("develop",), "develop", {})
    monkeypatch.setattr(config, "repository_branch_settings", lambda: {repo: settings})
    session = "removed-baseline-branch"
    _register_circuit(tmp_path, monkeypatch, session)
    configured = (config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git", branch="maintenance"),)
    payload = {"session_id": session, "cwd": str(tmp_path)}
    assert progress_self_check.claim_notice(payload, now=1.0) is None
    notice = progress_self_check.claim_notice(payload, now=1801.0)
    assert notice is not None and "新着WIは未確認" in notice
    assert all(value in notice for value in (repo, "develop", "maintenance", "atk config repos list", "手動照合"))


@pytest.mark.parametrize(
    "command",
    [
        "git push origin develop",
        "git push origin HEAD:refs/heads/develop",
        "git -C /repo push origin develop",
        "timeout 30 git push origin develop",
        "cd /repo && git push origin develop",
        "gh pr create --base master --head develop",
    ],
)
@pytest.mark.parametrize("baseline", [False, True])
def test_publication_does_not_query_or_wait_for_new_work(tmp_path, monkeypatch, capsys, command, baseline):
    session = "publication-with-new-work"
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", session)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if baseline:
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        repository = config.ConfiguredRepository("github.com/example/foo", tmp_path, tmp_path / ".git")
        monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
        _register_circuit(tmp_path, monkeypatch, session)
        _write_awi_file(tmp_path / "private-notes", "20261124-085011-004.md")
        merge = _write_awi_file(tmp_path / "private-notes", "20261124-085011-005.md", source="implement")
        merge.write_text(
            merge.read_text(encoding="utf-8").replace("source: implement", "source: implement\nmerge_branch: feature/test"),
            encoding="utf-8",
        )

    def unexpected_query(*_args, **_kwargs):
        pytest.fail("公開操作で新着WIを再取得した")

    monkeypatch.setattr(listing, "progress_new_wis", unexpected_query)
    payload = {"session_id": session, "cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": command}}
    capsys.readouterr()
    for _ in range(2):
        assert dispatch.main(json.dumps(payload)) == 0
        output = capsys.readouterr().out
        response = json.loads(output) if output else {}
        assert response.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_own_processing_wis_leave_progress_notice(tmp_path, monkeypatch):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repo = "github.com/example/foo"
    repository = config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    session = "processing-new-wis"
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", session)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)

    def run(*args: str) -> None:
        with pytest.raises(SystemExit) as result:
            atk.main(["wi", *args], home=tmp_path)
        assert result.value.code == 0

    run("list", "--skip-pull", "--count", "--configured-repos", "--snapshot-output", str(tmp_path / "baseline.json"))
    names = tuple(f"20261124-085010-00{number}.md" for number in range(1, 4))
    for name in names:
        _write_awi_file(notes, name)
    run("start-processing", *names[:2], f"--target-repo={repo}")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", session)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "direct-delegate")
    run("start-processing", names[2], f"--target-repo={repo}")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)

    payload = {"session_id": session, "cwd": str(tmp_path)}

    assert listing.progress_new_wis(session) == ()

    outside = "20261124-085011-001.md"
    _write_awi_file(notes, outside)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "other-session")
    run("start-processing", outside, f"--target-repo={repo}")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    run("return-to-inbox", names[2], f"--target-repo={repo}")

    assert progress_self_check.claim_notice(payload, now=1.0) is None
    notice = progress_self_check.claim_notice(payload, now=1801.0)
    assert notice is not None and outside in notice and names[2] in notice
    assert all(name not in notice for name in names[:2])


def test_new_merge_stays_pending_until_adoption(tmp_path, monkeypatch):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repo = "github.com/example/foo"
    repository = config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    session = "pending-merge"
    _register_circuit(tmp_path, monkeypatch, session)
    item = _write_awi_file(notes, "20261124-085011-004.md", source="implement")
    raw = item.read_text(encoding="utf-8").replace("source: implement\n", "source: implement\nmerge_branch: feature/test\n")
    item.write_text(raw, encoding="utf-8")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["wi", "start-processing", item.name, f"--target-repo={repo}"], home=tmp_path)
    assert listing.progress_new_wis(session) == ()
    tracked = listing.progress_new_wis(session, track_merges=True)
    assert tracked is not None and tracked[0].merge_pending == "processing"
    pending, _ = progress_self_check._new_wi_text(session, session)  # pylint: disable=protected-access
    assert item.name in pending and "取り込み結果待ち" in pending

    adopted = item.read_text(encoding="utf-8").replace("state: processing", "state: adopted")
    item.write_text(adopted, encoding="utf-8")
    assert listing.progress_new_wis(session, track_merges=True) == ()
