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
from agent_toolkit._agents_server import state, status_file

from pytools._internal import setup_statusline_binary

_ROOT = pathlib.Path(__file__).resolve().parent
_STATE_CONTRACT = _ROOT / "rust" / "claude-statusline" / "testdata" / "agents_server_state.json"
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
    """状態ディレクトリの`platformdirs`の配下の構成が、statuslineの解決する構成と一致する。"""
    contract = json.loads(_STATE_CONTRACT.read_text(encoding="utf-8"))
    directory = status_file.status_directory("root-1")
    expected = [("root-1" if part == "<root_session_id>" else part) for part in contract["state_directory"]]

    assert list(directory.parts[-len(expected) :]) == expected


def test_release_asset_names_match_setup_statusline_binary() -> None:
    """release workflowがビルドして公開する配布物の名前が、post-applyが取得する名前と一致する。

    一方だけを変えると、post-applyはReleaseに無い名前を取得しようとして配置に失敗する。
    """
    workflow = yaml.safe_load(_RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    expected = {setup_statusline_binary.ASSET_NAME_POSIX, setup_statusline_binary.ASSET_NAME_WINDOWS}

    built = {entry["asset"] for entry in workflow["jobs"]["build"]["strategy"]["matrix"]["include"]}
    published = {
        token.removeprefix("assets/")
        for step in workflow["jobs"]["release"]["steps"]
        for line in str(step.get("run", "")).splitlines()
        for token in shlex.split(line.rstrip("\\"))
        if token.startswith("assets/")
    }

    assert built == expected
    assert published == expected
