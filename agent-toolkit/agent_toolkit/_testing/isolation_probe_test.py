import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import textwrap

import pytest

from agent_toolkit import hook
from agent_toolkit._common import session_review
from agent_toolkit._testing import isolation

_PENDING = "session-review/pending/claude-probe-budget.json"
_PROBE = textwrap.dedent(
    """\
    import contextlib, io, json, pathlib, sys
    from agent_toolkit import hook
    from agent_toolkit._testing import isolation

    root, cwd = sys.argv[1], sys.argv[2]
    payload = {
        "hook_event_name": "SessionStart",
        "source": "compact",
        "session_id": "probe-budget",
        "transcript_path": str(pathlib.Path(cwd) / "probe-budget.jsonl"),
        "cwd": cwd,
    }
    sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8")))
    scope = isolation.isolated_development_state(pathlib.Path(root)) if root else contextlib.nullcontext()
    with scope:
        sys.exit(hook.main(["rules_context"]))
    """
)


def _session_start_input(cwd: pathlib.Path) -> str:
    payload = {
        "hook_event_name": "SessionStart",
        "source": "compact",
        "session_id": "probe-budget",
        "transcript_path": str(cwd / "probe-budget.jsonl"),
        "cwd": str(cwd),
    }
    return json.dumps(payload)


def _additional_context(stdout: str) -> str:
    return json.loads(stdout)["hookSpecificOutput"]["additionalContext"]


def test_pytest_replay_registers_pending_under_test_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(_session_start_input(tmp_path).encode())))

    assert hook.main(["rules_context"]) == 0

    assert _additional_context(capsys.readouterr().out)
    assert [entry["session_id"] for entry in session_review.pending()] == ["probe-budget"]
    assert session_review.directory().is_relative_to(tmp_path)


@pytest.mark.parametrize("isolated", [True, False], ids=["isolated-probe", "default-store"])
def test_one_shot_probe_registers_pending_only_in_its_store(tmp_path: pathlib.Path, isolated: bool) -> None:
    """単発スクリプトは隔離の区間内で再生した入力を試験用の保存先へ登録し、プロセス環境が指す保存先を変えない。

    対照では、隔離を指定しない最上位のSessionStartが、実運用の代わりにプロセス環境が指す一時領域へ登録される。
    """
    probe_root = tmp_path / "probe-root"
    default_store = tmp_path / "default-store"
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    (script_dir / "probe.py").write_text(_PROBE, encoding="utf-8")
    env = {
        **os.environ,
        "XDG_STATE_HOME": str(default_store),
        "LOCALAPPDATA": str(default_store),
        "PYTHONPATH": str(pathlib.Path(hook.__file__).resolve().parents[1]),
    }

    result = subprocess.run(
        [sys.executable, str(script_dir / "probe.py"), str(probe_root) if isolated else "", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=tmp_path,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert _additional_context(result.stdout)
    in_probe = list(probe_root.rglob(_PENDING)) if probe_root.exists() else []
    in_default = list(default_store.rglob(_PENDING)) if default_store.exists() else []
    assert (len(in_probe), len(in_default)) == ((1, 0) if isolated else (0, 1))


def test_isolated_development_state_applies_pytest_isolation_and_restores(tmp_path: pathlib.Path) -> None:
    before = dict(os.environ)

    with isolation.isolated_development_state(tmp_path / "probe-root"):
        isolation.assert_development_state_isolated(tmp_path / "probe-root")

    assert dict(os.environ) == before


@pytest.mark.skipif(sys.platform == "win32", reason="PATHの置換はPOSIXの実行ビットとシンボリックリンクで再現する")
def test_isolated_development_state_reentry_keeps_non_cli_tools(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    for name in ("codex", "probe-tool"):
        (host_bin / name).write_text("#!/bin/sh\n", encoding="utf-8")
        (host_bin / name).chmod(stat.S_IRWXU)
    monkeypatch.setitem(isolation._HOST_ENVIRON, "PATH", str(host_bin))  # pylint: disable=protected-access

    for _ in range(2):
        with isolation.isolated_development_state(tmp_path / "probe-root"):
            assert shutil.which("probe-tool") is not None
            assert shutil.which("codex") is None
