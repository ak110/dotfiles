import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config, config_repositories
from agent_toolkit._atk.wi import listing, snapshot
from agent_toolkit._atk.wi.formatters import NewWorkItem
from agent_toolkit._common import session_state
from agent_toolkit._hooks import notice as hook_notice
from agent_toolkit._hooks import progress_self_check, work_contract
from agent_toolkit._hooks.pretooluse import publication_gate
from agent_toolkit._testing.output_policy_support import assert_output_policy
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


@pytest.fixture(autouse=True)
def _publication_origin(tmp_path, monkeypatch):
    git_run = publication_gate.git_command.run
    resolve = publication_gate.resolve_repo_identifier

    def run(args, cwd, **kwargs):
        if cwd is not None and pathlib.Path(cwd) == tmp_path:
            if args == ["rev-parse", "--symbolic-full-name", "@{push}"]:
                return subprocess.CompletedProcess(args, 0, "refs/remotes/origin/develop\n", "")
            if args[:4] == ["remote", "get-url", "--push", "--all"]:
                repo = "foo" if args[4] == "origin" else args[4]
                return subprocess.CompletedProcess(args, 0, f"https://github.com/example/{repo}.git\n", "")
        return git_run(args, cwd, **kwargs)

    def repository(value):
        return "github.com/example/foo" if value == str(tmp_path) else resolve(value)

    monkeypatch.setattr(publication_gate.git_command, "run", run)
    monkeypatch.setattr(publication_gate, "resolve_repo_identifier", repository)


def test_publication_blocks_on_uwis_answered_after_the_baseline(tmp_path, monkeypatch):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repository = config.ConfiguredRepository("github.com/example/foo", tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "snapshot-answered")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "snapshot-answered")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    names = ("20261124-061940-001.md", "20261124-061940-002.md")
    for name in names:
        _write_uwi_file(notes, name, answer="")
    with pytest.raises(SystemExit) as invocation:
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
    assert invocation.value.code == 0
    payload = {"session_id": "snapshot-answered", "cwd": str(tmp_path)}
    for name in names:
        _write_uwi_file(notes, name, answer="回答済み")

    def gate(command: str) -> str | None:
        denied = publication_gate.check(payload, "snapshot-answered", "Bash", {"command": command})
        return denied[0] if denied else None

    for command in ("git push origin develop", "gh pr create --base master --head develop"):
        denied = gate(command)
        assert denied is not None and all(name in denied for name in names)
    assert gate("git push --dry-run origin develop") is None
    assert gate("gh pr create --base master --head develop") is None
    _write_uwi_file(notes, "20261124-061940-003.md", answer="")
    _write_uwi_file(notes, "20261124-061940-003.md", answer="回答済み")
    changed = gate("gh pr create --base master --head develop")
    assert changed is not None and "20261124-061940-003.md" in changed
    _write_uwi_file(notes, "20261124-061940-003.md", answer="")
    contract = tmp_path / "work-contract.md"
    monkeypatch.setattr(work_contract, "record_path", lambda _sid: contract)
    contract.write_text(f"- 新着WIの判定: {names[0]} | 次回: 別ホストで観測する\n", encoding="utf-8")
    pending = gate("git push")
    assert pending is not None and names[0] in pending and names[1] in pending
    _write_uwi_file(notes, names[1], answer="")
    remaining = gate("git push")
    assert remaining is not None and names[0] in remaining and names[1] not in remaining
    _write_uwi_file(notes, names[0], answer="")
    assert gate("git push") is None
    _write_uwi_file(notes, names[1], answer="回答済み")
    recurred = gate("git push")
    assert recurred is not None and names[1] in recurred
    _write_uwi_file(notes, names[0], answer="回答済み")
    contract.write_text("", encoding="utf-8")
    both = gate("git push")
    assert both is not None and names[0] in both and names[1] in both
    contract.write_text(f"- 新着WIの判定: {names[0]} | 次回: 別ホストで観測する\n", encoding="utf-8")
    assert gate("git push") is None


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


@pytest.mark.parametrize("oid", ["a" * 40, "b" * 64])
@pytest.mark.parametrize(
    "new_items", [None, [NewWorkItem("20261124-085011-004.md", "implement", True, merge_pending="processing")]]
)
def test_pinned_publication_does_not_wait_for_new_selection(tmp_path, monkeypatch, oid, new_items):
    owner = "pipelined-publication"
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", owner)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(publication_gate, "progress_new_wis", lambda *_args, **_kwargs: new_items)
    payload = {"session_id": owner, "cwd": str(tmp_path)}
    assert publication_gate.check(payload, owner, "Bash", {"command": f"git push origin {oid}:refs/heads/develop"}) is None
    assert publication_gate.check(payload, owner, "Bash", {"command": "git push origin HEAD:refs/heads/develop"})


def test_publication_uses_registered_baseline_after_session_temp_changes(tmp_path, monkeypatch):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repo = "github.com/example/foo"
    repository = config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    owner = "registered-publication"
    _register_circuit(tmp_path, monkeypatch, owner)
    registered = snapshot.progress_sources(owner)
    assert registered
    with monkeypatch.context() as moved:
        moved.setattr(snapshot.managed_temp, "find_session_temp", lambda _session_id: tmp_path / "new")
        assert snapshot.progress_sources(owner) == registered
        assert not snapshot.progress_sources("different-circuit")
        late = _write_awi_file(notes, "20261124-085011-004.md")
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", owner)
        for session, payload in (
            (owner, {"session_id": owner, "cwd": str(tmp_path)}),
            ("codex-delegate", {"session_id": "codex-delegate", "agent_id": "codex-delegate", "cwd": str(tmp_path)}),
        ):
            denied = publication_gate.check(payload, session, "Bash", {"command": "git push"})
            assert denied is not None and late.name in denied[0]


@pytest.mark.parametrize(
    "push",
    ["git push origin develop", f"git push --force-with-lease=refs/heads/develop:{'a' * 40} origin HEAD:refs/heads/develop"],
)
def test_publication_requires_recovery_of_the_same_circuit_baseline(tmp_path, monkeypatch, push):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repository = config.ConfiguredRepository("github.com/example/foo", tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    owner = "active-publication"
    _register_circuit(tmp_path, monkeypatch, "previous-publication")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", owner)
    command = {"command": push}
    payload = {"session_id": owner, "cwd": str(tmp_path)}
    for _ in range(2):
        denied = publication_gate.check(payload, owner, "Bash", command)
        assert denied is not None and "開始時集合がない" in denied[0]
        assert "再発行すると実行" not in denied[1]

    _register_circuit(tmp_path, monkeypatch, owner)
    monkeypatch.setattr(snapshot.managed_temp, "find_session_temp", lambda _session_id: tmp_path / "compacted")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", owner)
    child = "publication-worker"
    delegated = {"session_id": child, "agent_id": child, "cwd": str(tmp_path)}
    assert publication_gate.check(delegated, child, "Bash", command) is None
    late = _write_awi_file(notes, "20261124-085011-004.md")
    initial = publication_gate.check(delegated, child, "Bash", command)
    assert initial is not None and late.name in initial[0]

    (source,) = snapshot.progress_sources(owner).values()
    path = pathlib.Path(source["path"])
    original = path.read_bytes()
    path.write_bytes(b"invalid")
    for _ in range(2):
        denied = publication_gate.check(delegated, child, "Bash", command)
        assert denied is not None and "新着WIを取得できない" in denied[0]
        assert "再発行すると実行" not in denied[1]
    path.write_bytes(original)
    recovered = publication_gate.check(delegated, child, "Bash", command)
    assert recovered is not None and late.name in recovered[0]
    assert publication_gate.check(delegated, child, "Bash", command) is None


@pytest.mark.parametrize(
    "push",
    [
        "git push --force-with-lease origin HEAD:develop",
        "git push --force-with-lease=develop:abcd origin HEAD:develop",
        f"git push --force-with-lease=refs/heads/other:{'a' * 40} origin HEAD:develop",
        f"git push --force-with-lease=refs/heads/develop:{'a' * 40}",
    ],
)
def test_publication_does_not_accept_ambiguous_lease(tmp_path, monkeypatch, push):
    owner = "ambiguous-publication"
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", owner)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    payload = {"session_id": owner, "cwd": str(tmp_path)}
    denied = publication_gate.check(payload, owner, "Bash", {"command": push})
    assert denied is not None and "公開対象を確定できない" in denied[0]
    assert "再発行すると実行" not in denied[1]


def test_own_processing_wis_leave_progress_notice_and_publication_gate(tmp_path, monkeypatch):
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

    def gate(command: str):
        return publication_gate.check(payload, session, "Bash", {"command": command})

    assert gate("git push origin develop") is None
    assert gate("gh pr create --base master --head develop") is None

    outside = "20261124-085011-001.md"
    _write_awi_file(notes, outside)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "other-session")
    run("start-processing", outside, f"--target-repo={repo}")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    run("return-to-inbox", names[2], f"--target-repo={repo}")

    pending = gate("git push origin develop")
    assert pending is not None and outside in pending[0] and names[2] in pending[0]
    assert all(name not in pending[0] for name in names[:2])
    assert progress_self_check.claim_notice(payload, now=1.0) is None
    try:
        notice = progress_self_check.claim_notice(payload, now=1801.0)
        assert notice is not None and outside in notice and names[2] in notice
        assert all(name not in notice for name in names[:2])
    finally:
        progress_self_check.release_reservations()


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
    payload = {"session_id": session, "cwd": str(tmp_path)}
    publish = {"command": "git push origin develop"}

    for _ in range(2):
        denied = publication_gate.check(payload, session, "Bash", publish)
        assert denied is not None and "同じ操作の再発行では解除できません" in denied[1]
    with pytest.raises(SystemExit, match="0"):
        atk.main(["wi", "start-processing", item.name, f"--target-repo={repo}"], home=tmp_path)
    assert listing.progress_new_wis(session) == ()
    tracked = listing.progress_new_wis(session, track_merges=True)
    assert tracked is not None and tracked[0].merge_pending == "processing"
    pending, _ = progress_self_check._new_wi_text(session, session)  # pylint: disable=protected-access
    assert item.name in pending and "取り込み結果待ち" in pending
    assert publication_gate.check(payload, session, "Bash", publish) is not None

    adopted = item.read_text(encoding="utf-8").replace("state: processing", "state: adopted")
    item.write_text(adopted, encoding="utf-8")
    assert publication_gate.check(payload, session, "Bash", publish) is None


@pytest.mark.parametrize("transport", ["git", "gh", "mcp"])
def test_new_merge_does_not_change_the_frozen_publication_unit(tmp_path, monkeypatch, transport):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repo = "github.com/example/foo"
    repository = config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    session = "existing-publication"
    _register_circuit(tmp_path, monkeypatch, session)
    run = publication_gate.git_command.run
    head = "a" * 40
    remote_head = "a" * 40

    def git(args, cwd, **kwargs):
        if args == ["rev-parse", "--verify", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(args, 0, head + "\n", "")
        if args == ["ls-remote", "--refs", "--exit-code", "origin", "refs/heads/work"]:
            if remote_head is None:
                return subprocess.CompletedProcess(args, 2, "", "")
            return subprocess.CompletedProcess(args, 0, remote_head + "\trefs/heads/work\n", "")
        return run(args, cwd, **kwargs)

    monkeypatch.setattr(publication_gate.git_command, "run", git)
    payload = {"session_id": session, "cwd": str(tmp_path)}
    tool, request = {
        "git": ("Bash", {"command": "git push origin HEAD:refs/heads/develop"}),
        "gh": ("Bash", {"command": "gh pr create --repo example/foo --base develop --head work"}),
        "mcp": ("mcp__github__create_pull_request", {"owner": "example", "repo": "foo", "base": "develop", "head": "work"}),
    }[transport]
    assert publication_gate.check(payload, session, tool, request) is None
    item = _write_awi_file(notes, "20261124-085011-004.md", source="implement")
    raw = item.read_text(encoding="utf-8").replace("source: implement\n", "source: implement\nmerge_branch: feature/test\n")
    item.write_text(raw, encoding="utf-8")
    assert publication_gate.check(payload, session, tool, request) is None
    if transport == "git":
        head = "b" * 40
    else:
        remote_head = "b" * 40
    assert publication_gate.check(payload, session, tool, request) is not None
    if transport != "git":
        remote_head = None
        assert publication_gate.check(payload, session, tool, request) is not None


@pytest.mark.parametrize("transport", ["mcp", "gh", "git"])
def test_explicit_publication_scope_requires_its_own_baseline(tmp_path, monkeypatch, transport):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repos = ("github.com/example/foo", "github.com/example/bar")
    configured = tuple(config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git") for repo in repos)
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: configured)
    session = "publication-scope"
    _register_circuit(tmp_path, monkeypatch, session)
    late = _write_awi_file(notes, "20261124-085011-004.md", target_repo=repos[1])
    payload = {"session_id": session, "cwd": str(tmp_path)}

    def check(repo):
        if transport == "git":
            return publication_gate.check(payload, session, "Bash", {"command": f"git push {repo} main"})
        if transport == "gh":
            return publication_gate.check(
                payload, session, "Bash", {"command": f"gh pr create --repo example/{repo} --base main --head work"}
            )
        return publication_gate.check(
            payload, session, "mcp__github__create_pull_request", {"owner": "example", "repo": repo, "base": "main"}
        )

    assert check("foo") is None
    denied = check("bar")
    assert denied is not None and late.name in denied[0]
    denied = check("unknown")
    assert denied is not None and "開始時集合がない" in denied[0]
    uncertain = publication_gate.check(payload, session, "Bash", {"command": "git push origin --all"})
    assert uncertain is not None and "比較を行っていない" in uncertain[0]
    unspecified_base = publication_gate.check(payload, session, "Bash", {"command": "gh pr create --repo example/foo"})
    assert unspecified_base is not None and "比較を行っていない" in unspecified_base[0]


def test_pr_base_needs_its_own_branch_baseline(tmp_path, monkeypatch):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repo = "github.com/example/foo"
    configured = tuple(
        config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git", branch=branch, default_branch="develop")
        for branch in ("develop", "maintenance")
    )
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: configured)
    branches = config_repositories.BranchSettings(("develop", "maintenance"), "develop", {})
    monkeypatch.setattr(config, "repository_branch_settings", lambda: {repo: branches})
    session = "pr-base-scope"
    _register_circuit(tmp_path, monkeypatch, session)
    late = _write_awi_file(notes, "20261124-085011-005.md", target_repo=repo)
    late.write_text(
        late.read_text(encoding="utf-8").replace("state: inbox", "state: inbox\ntarget_branch: maintenance"), encoding="utf-8"
    )
    payload = {"session_id": session, "cwd": str(tmp_path)}

    def check(options):
        return publication_gate.check(payload, session, "Bash", {"command": "gh pr create --repo example/foo " + options})

    assert check("--base develop") is None
    maintenance = check("--base maintenance")
    assert maintenance is not None and late.name in maintenance[0]
    missing = check("--base unrecorded")
    assert missing is not None and "開始時集合がない" in missing[0]
    unspecified = check("")
    assert unspecified is not None and "比較を行っていない" in unspecified[0]


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
        denied = publication_gate.check(payload, session, "Bash", {"command": "git push"})
        assert denied is not None and "開始時集合がない" in denied[0]
        return
    assert progress_self_check.claim_notice(payload, now=1.0) is None
    try:
        notice = progress_self_check.claim_notice(payload, now=1801.0)
        assert notice is not None and new_name in notice and held.name not in notice
    finally:
        progress_self_check.release_reservations()

    denied = publication_gate.check(payload, session, "Bash", {"command": "git push"})
    assert denied is not None and new_name in denied[0] and held.name not in denied[0]
    contract = tmp_path / "work-contract.md"
    monkeypatch.setattr(work_contract, "record_path", lambda _sid: contract)
    contract.write_text(f"- 新着WIの判定: {new_name} | 次回: 別ホストで観測する\n", encoding="utf-8")
    assert publication_gate.check(payload, session, "Bash", {"command": "git push"}) is None


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
    try:
        notice = progress_self_check.claim_notice(payload, now=1801.0)
        assert notice is not None
        assert (late.name in notice) == valid
        assert ("新着WIは未確認" in notice) != valid
    finally:
        progress_self_check.release_reservations()
    denied = publication_gate.check(payload, session, "Bash", {"command": "git push"})
    assert denied is not None
    assert (late.name in denied[0]) == valid


def test_publication_keeps_gate_but_does_not_repeat_delivered_progress_wis(tmp_path, monkeypatch):
    sid = "progress-publication-shared"
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", sid)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    old = "20261124-061940-004.md"
    new = "20261124-061940-005.md"
    rows = [NewWorkItem(old, "implement", False)]
    monkeypatch.setattr(listing, "progress_new_wis", lambda *_args, **_kwargs: tuple(rows))
    monkeypatch.setattr(publication_gate, "progress_new_wis", listing.progress_new_wis)
    text, snapshot_id = hook_notice.new_wi_preview(sid, "main", rows)
    assert old in text
    hook_notice.stage_new_wi_preview(text, sid, "main", snapshot_id)
    hook_notice.record_delivery(text)
    rows.append(NewWorkItem(new, "implement", False))
    payload = {"session_id": sid, "cwd": str(tmp_path)}
    command = {"command": "git push origin develop"}
    denied = publication_gate.check(payload, sid, "Bash", command)
    assert denied is not None
    reason, _ = denied
    assert "計2件" in reason and new in reason and old not in reason
    assert_output_policy("wi_publication_delta", reason)
    assert publication_gate.check(payload, sid, "Bash", command) is None


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
    try:
        notice = progress_self_check.claim_notice(payload, now=1801.0)
        assert notice is not None and all(notice.count(name) == 1 for name in names.values())
        assert old.name not in notice and outside.name not in notice
    finally:
        progress_self_check.release_reservations()
    denied = publication_gate.check(payload, session, "Bash", {"command": f"git push origin HEAD:{destination}"})
    assert denied is not None
    expected = {names[destination]} if destination in names else set(names.values())
    assert {name for name in names.values() if name in denied[0]} == expected
    assert old.name not in denied[0] and outside.name not in denied[0]
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
    try:
        notice = progress_self_check.claim_notice(payload, now=1801.0)
        assert notice is not None and "新着WIは未確認" in notice
        assert all(value in notice for value in (repo, "develop", "maintenance", "atk config repos list", "手動照合"))
    finally:
        progress_self_check.release_reservations()
    for _ in range(2):
        denied = publication_gate.check(payload, session, "Bash", {"command": "git push origin develop"})
        assert denied is not None and "新着WIを取得できない" in denied[0]
        assert all(value in "\n".join(denied) for value in (repo, "develop", "maintenance", "手動照合"))
        assert "再発行すると実行" not in denied[1]


@pytest.mark.parametrize(
    "command",
    ["git -C /repo push origin develop", "timeout 30 git push origin develop", "cd /repo && git push origin develop"],
)
def test_unsupported_publication_form_explains_how_to_resolve_the_working_repository(tmp_path, monkeypatch, command):
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    configured = (config.ConfiguredRepository("github.com/example/foo", tmp_path, tmp_path / ".git"),)
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: configured)
    session = "unsupported-push-form"
    _register_circuit(tmp_path, monkeypatch, session)
    payload = {"session_id": session, "cwd": str(tmp_path)}
    for _ in range(2):
        denied = publication_gate.check(payload, session, "Bash", {"command": command})
        assert denied is not None and "公開対象を確定できない" in denied[0]
        assert all(value in denied[1] for value in ("workdir", "cwd", "単独のgit push", "引き継がれません"))
        assert "再発行すると実行" not in denied[1]
    assert publication_gate.check(payload, session, "Bash", {"command": "git push origin develop"}) is None
