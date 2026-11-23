import pytest

from agent_toolkit._common import next_action


@pytest.mark.parametrize("case", ["plan-path", "plan-readback", "wi-dependencies", "wi-priority", "managed-temp-default"])
def test_recovery_for_storage_failure(case, monkeypatch, tmp_path):
    import contextlib
    from pathlib import Path

    from agent_toolkit._atk import plans_bundle, plans_storage
    from agent_toolkit._atk.managed_temp import paths
    from agent_toolkit._atk.managed_temp.errors import ManagedTempError
    from agent_toolkit._atk.wi.mutations import dependencies, priority
    from agent_toolkit._atk.wi.web_input import WebInputError

    if case == "plan-path":
        notes = tmp_path / "notes"
        with pytest.raises(WebInputError) as exc:
            plans_bundle.as_relative_notes_path(tmp_path / "outside", notes)
    elif case == "plan-readback":
        target = tmp_path / "plan.md"
        original_read = Path.read_bytes

        def read(path):
            return b"unexpected" if path == target else original_read(path)

        monkeypatch.setattr(Path, "read_bytes", read)
        with pytest.raises(WebInputError) as exc:
            plans_storage.update_saved_bundle(tmp_path, (), {"plan.md": b"plan"})
    elif case == "managed-temp-default":
        with pytest.raises(ManagedTempError) as exc:
            paths.contained_target(Path("relative"))
    else:
        mutation = dependencies if case == "wi-dependencies" else priority
        monkeypatch.setattr(mutation._wi_sync, "repo_lock", lambda *_a, **_kw: contextlib.nullcontext())
        monkeypatch.setattr(mutation._wi_sync, "push_pending_commits", lambda *_a: None)
        monkeypatch.setattr(mutation._wi_sync, "pull", lambda *_a: None)
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        name = "20261123-151142-001.md"
        (inbox / name).write_text("本文だけ\n", encoding="utf-8")
        with pytest.raises(WebInputError) as exc:
            if case == "wi-dependencies":
                dependencies.set_entry_dependencies(tmp_path, filename=name, depends_on=())
            else:
                priority.set_entry_priority(tmp_path, filename=name, urgent=False)

    guide = exc.value.next_action
    assert guide.index("原因を確かめる") < guide.index("認可の範囲で回復する") < guide.index("回復結果を伝える")


@pytest.mark.parametrize("case", ["plain-cli-error", "head-oid", "antigravity-transcript"])
def test_recovery_for_reported_failure(case, monkeypatch, tmp_path, capsys):
    import argparse
    import subprocess

    from agent_toolkit._atk import cli_support
    from agent_toolkit._atk.wi import repo
    from agent_toolkit._hooks import antigravity

    if case == "plain-cli-error":
        cli_support.report_rejected(argparse.Namespace(command="wi"), ValueError("引数が不正"))
    elif case == "head-oid":
        monkeypatch.setattr(
            repo._git_command, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "not-an-oid", "")
        )
        with pytest.raises(SystemExit, match="2"):
            repo.resolve_head_commit(tmp_path)
    else:
        assert antigravity._latest_steps(str(tmp_path / "missing.jsonl")) == (None, "")

    guide = capsys.readouterr().err
    assert guide.index("原因を確かめる") < guide.index("認可の範囲で回復する") < guide.index("回復結果を伝える")


@pytest.mark.parametrize("case", ["unreadable-index", "ambiguous-index", "review-readback", "orchestrator-no-identity"])
def test_recovery_for_observation_failure(case, monkeypatch, tmp_path):
    import contextlib
    import json
    import sys
    from pathlib import Path

    from agent_toolkit._agents_server import shared_roots
    from agent_toolkit._atk import review_table
    from agent_toolkit._atk.wi import process_loop_session

    with pytest.raises(next_action.ActionableError) as exc:
        if case in {"unreadable-index", "ambiguous-index"}:
            index = tmp_path / "index"
            index.mkdir()
            monkeypatch.setattr(shared_roots, "hosts_directory", lambda *_a: index)
            monkeypatch.setattr(shared_roots, "list_status_files", lambda *_a: ())
            if case == "unreadable-index":
                original = Path.iterdir

                def iterdir(path):
                    if path == index:
                        raise PermissionError("読取不可")
                    return original(path)

                monkeypatch.setattr(Path, "iterdir", iterdir)
            else:
                for writer in ("writer-one", "writer-two"):
                    (index / f"{writer}.json").write_text(
                        json.dumps({"version": 1, "host_session_id": "host-session"}), encoding="utf-8"
                    )
            shared_roots.resolve_host_writer(shared_roots.StatusFileIdentity("root", "owner.json", "host-session"), tmp_path)
        elif case == "review-readback":
            monkeypatch.setattr(review_table, "_read", lambda *_a: [])
            review_table.add(tmp_path / "review.tsv", "1", "exec-review", "example.py:1", "指摘")
        else:
            monkeypatch.setattr(process_loop_session._control, "resume_launch", lambda *_a: contextlib.nullcontext())
            monkeypatch.setattr(process_loop_session._process_loop_log, "launch_instructions", lambda: contextlib.nullcontext())
            monkeypatch.setattr(process_loop_session._registry.ProcessIdentity, "of", lambda *_a: None)
            process_loop_session._run_cli_session([sys.executable, "-c", "pass"], {}, tmp_path)

    guide = exc.value.next_action
    assert guide.index("原因を確かめる") < guide.index("認可の範囲で回復する") < guide.index("回復結果を伝える")
