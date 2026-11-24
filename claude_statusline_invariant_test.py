"""statusline（`rust/claude-statusline/`）が他の領域と共有する契約の一致を検証する。

- agent-toolkitの`status_file.py`が書く状態ファイルの版・期限・キー・状態ディレクトリの構成が、
  Rust側のテストも読むfixture`rust/claude-statusline/testdata/agents_server_state.json`と一致する
- `release-statusline.yaml`が公開する配布物の名前が、`pytools/_internal/setup_statusline_binary.py`の取得する名前と一致する
"""

import json
import pathlib
import shlex

import pytest
import yaml
from agent_toolkit._agents_server import context_usage, state, status_file

from pytools._internal import setup_statusline_binary

_ROOT = pathlib.Path(__file__).resolve().parent
_STATE_CONTRACT = _ROOT / "rust" / "claude-statusline" / "testdata" / "agents_server_state.json"
_CI_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yaml"
_RELEASE_WORKFLOW = _ROOT / ".github" / "workflows" / "release-statusline.yaml"


@pytest.mark.asyncio
async def test_status_file_writer_matches_state_fixture(tmp_path: pathlib.Path) -> None:
    """書き込み側の版・ハートビートの期限・キーがstatuslineの読み取り側と共有するfixtureと一致する。

    書き込み側だけを変えると、statuslineは版の不一致か必須キーの欠落でその状態ファイルの行を表示しなくなる。
    """
    contract = json.loads(_STATE_CONTRACT.read_text(encoding="utf-8"))
    example = contract["example"]["sessions"][0]
    session = state.SessionState(example["session_id"], str(tmp_path))
    session.engine = example["engine"]
    session.model = example["model"]
    session.effort = example["effort"]
    session.fast_mode = example["fast_mode"]
    session.model_type = example["model_type"]
    session.label = example["label"]
    session.announced = True
    context_usage.record_context_usage(session, example["context_tokens"], example["context_window"])
    writer = status_file.StatusFileWriter(
        {session.session_id: session},
        status_file.StatusFileIdentity("root-session", "root.json", "host-session"),
        state_root=tmp_path,
    )

    writer.activate()
    try:
        written = json.loads(writer.path.read_text(encoding="utf-8"))
    finally:
        writer.deactivate()

    assert written["version"] == contract["version"]
    assert contract["heartbeat_expiry_seconds"] == status_file.HEARTBEAT_EXPIRY_SECONDS
    assert sorted(written) == sorted(contract["state_file_keys"])
    assert sorted(written["sessions"][0]) == sorted(contract["session_keys"])


def test_state_directory_layout_matches_state_fixture() -> None:
    contract = json.loads(_STATE_CONTRACT.read_text(encoding="utf-8"))
    directory = status_file.status_directory("root-1")
    expected = [("root-1" if part == "<root_session_id>" else part) for part in contract["state_directory"]]

    assert list(directory.parts[-len(expected) :]) == expected


def test_release_asset_names_match_setup_statusline_binary() -> None:
    workflow = yaml.safe_load(_RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    expected = {setup_statusline_binary.ASSET_NAME_POSIX, setup_statusline_binary.ASSET_NAME_WINDOWS}

    ci = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    built = {entry["asset"] for entry in ci["jobs"]["statusline-build"]["strategy"]["matrix"]["include"]}
    published = {
        token.removeprefix("assets/")
        for step in workflow["jobs"]["release"]["steps"]
        for line in str(step.get("run", "")).splitlines()
        for token in shlex.split(line.rstrip("\\"))
        if token.startswith("assets/")
    }

    assert built == expected
    assert published == expected


def test_statusline_release_consumes_attested_artifacts_from_triggering_ci() -> None:
    ci = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    build = ci["jobs"]["statusline-build"]
    assert build["needs"] == "common-ci-owner"
    assert build["if"] == "needs.common-ci-owner.outputs.statusline_release == 'true'"
    assert build["steps"][0]["with"]["ref"] == "${{ github.sha }}"
    assert build["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
        "artifact-metadata": "write",
    }
    attest, upload = build["steps"][-2:]
    assert attest["uses"].startswith("actions/attest@")
    assert "if" not in attest and "continue-on-error" not in attest
    assert attest["with"]["subject-path"] == upload["with"]["path"] == "${{ matrix.asset }}"
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"]["name"] == "statusline-release-${{ matrix.asset }}"
    assert upload["with"]["overwrite"] is True

    workflow = yaml.safe_load(_RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    release = workflow["jobs"]["release"]
    assert release["steps"][0]["with"]["ref"] == "${{ needs.gate.outputs.head_sha }}"
    download = next(step["with"] for step in release["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["run-id"] == "${{ github.event.workflow_run.id }}"
    assert download["github-token"] == "${{ github.token }}"
    assert download["pattern"] == "statusline-release-*"
    assert download["merge-multiple"] is True
    assert release["permissions"] == {"actions": "read", "contents": "write"}
