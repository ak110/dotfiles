# pylint: disable=protected-access  # 原本の読込の記憶を試験ごとに消すため。
"""`_agents_server/launch_prompts.py`の振る舞いを検証する。"""

from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import sys
from collections.abc import Iterator

import pytest

from agent_toolkit._agents_server import (
    antigravity,
    claude_options,
    codex_instructions,
    launch_prompts,
    opencode,
    session_errors,
)
from agent_toolkit._agents_server.state import LaunchKind
from agent_toolkit._common import plugin_roots
from agent_toolkit._testing.agents_server_claude_support import _capture_options


@pytest.mark.skipif(os.name == "nt", reason="POSIX shellによる実行の受入例")
def test_python_runtime_information_runs_json_without_python_on_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配送された起動部分から空白・引用符を含むパスのPythonでJSONを照会する。"""
    executable = tmp_path / "Python's runtime with spaces"
    executable.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(executable))
    instructions = launch_prompts.python_runtime_instructions()
    prefix = "POSIX shellでの起動部分: "
    command = next(line.removeprefix(prefix) for line in instructions.splitlines() if line.startswith(prefix))
    code = 'import json; print(json.loads("[3, 7]")[1])'
    result = subprocess.run(
        ["/bin/sh", "-c", command + " -c " + shlex.quote(code)],
        env={"PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "7\n"
    assert not result.stderr


@pytest.mark.parametrize("existing", [False, True])
def test_plugin_resource_instructions_reject_invalid_toolkit_root(tmp_path: pathlib.Path, existing: bool) -> None:
    """所在案内は、実在するskillsを持たないtoolkitを配送しない。"""
    root = tmp_path / "toolkit"
    if existing:
        root.mkdir()
    with pytest.raises(plugin_roots.PluginRootError, match="agent-toolkitの実在するskills"):
        launch_prompts.plugin_resource_instructions(root, "codex", str(tmp_path))


_MARKERS = {
    "intro": "冒頭識別",
    "notice": "通知識別",
    "constraints": "制約識別",
    "result": "返却識別",
    "fenced": "## 囲みの中の見出し識別",
    "closing": "終了識別",
    "delegate": "通常識別",
    "explore": "探索識別",
    "shell": "実行識別",
    "write": "書込識別",
    "auto-resume": "再開識別",
}


def _playbook_fixture(markers: dict[str, str]) -> str:
    """見出しと標識だけを本物の原本にそろえ、節ごとに区別できる本文を置いた原本を返す。"""
    return f"""# 委譲先の入口

{markers["intro"]}

## 委譲を受けた担当であることの通知

{markers["notice"]}

## 硬い制約

{markers["constraints"]}

<!-- subagent-result-contract:start -->

## 返却形式の受け渡し

{markers["result"]}

```text
{markers["fenced"]}
```

<!-- subagent-result-contract:end -->

## 返却と終了

{markers["closing"]}

## 起動の種類ごとの役割

### 通常の委譲

{markers["delegate"]}

### 探索

{markers["explore"]}

### 実行

{markers["shell"]}

### 書込

{markers["write"]}

### 自動再開

{markers["auto-resume"]}
"""


@pytest.fixture(name="playbook")
def _playbook(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[pathlib.Path]:
    """起動の本文の原本を試験用のファイルへ差し替え、読込の記憶を前後で消す。"""
    path = tmp_path / "subagent.md"
    path.write_text(_playbook_fixture(_MARKERS), encoding="utf-8")
    monkeypatch.setattr(launch_prompts, "SUBAGENT_PLAYBOOK", path)
    launch_prompts._playbook_lines.cache_clear()
    yield path
    launch_prompts._playbook_lines.cache_clear()


_KINDS: tuple[LaunchKind, ...] = ("delegate", "explore", "shell", "write")


@pytest.mark.usefixtures("agents_server_isolation", "playbook")
def test_launch_kinds_reach_sdk_with_their_sections(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """原本の節が起動の種類ごとに切り出され、別の種類の本文を含まずにSDKへ届く。"""
    captured = _capture_options(monkeypatch)
    for kind in _KINDS:
        claude_options._build_options(str(tmp_path), "model", "medium", launch_kind=kind)  # pylint: disable=protected-access
        delivered = captured["system_prompt"]
        assert isinstance(delivered, str)
        assert delivered.count(_MARKERS["notice"]) == 1, kind
        assert delivered.count(_MARKERS["result"]) == 1, kind
        assert _MARKERS["fenced"] in delivered, kind
        assert _MARKERS["auto-resume"] in delivered, kind
        assert delivered.count(_MARKERS[kind]) == 1, kind
        for other in set(_KINDS) - {kind}:
            assert _MARKERS[other] not in delivered, (kind, other)
        for whole_only in ("intro", "constraints", "closing"):
            assert (_MARKERS[whole_only] in delivered) is (kind == "delegate"), (kind, whole_only)
        assert (launch_prompts.EXPLORE_CAPABILITIES in delivered) is (kind == "explore"), kind


@pytest.mark.usefixtures("agents_server_isolation", "playbook")
@pytest.mark.parametrize("kind", _KINDS)
def test_every_backend_delivers_the_same_launch_body(
    kind: LaunchKind, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude・Codex・OpenCode・Antigravityの最初の起動と再開は、同じ関数が組み立てた起動の本文を届ける。"""
    monkeypatch.setattr(opencode.host_homes, "claude_config_dir", lambda: tmp_path / "claude")
    captured = _capture_options(monkeypatch)
    claude_options._build_options(str(tmp_path), "model", "medium", launch_kind=kind)  # pylint: disable=protected-access
    body = launch_prompts.launch_prompt(kind)
    delivered = {
        "claude": captured["system_prompt"],
        "codex": codex_instructions._developer_instructions(kind),  # pylint: disable=protected-access
        "opencode": opencode.system_prompt(kind),
        "agy": antigravity._system_prompt(kind),  # pylint: disable=protected-access
    }
    for engine, prompt in delivered.items():
        assert isinstance(prompt, str)
        assert prompt.count(body) == 1, engine
    assert launch_prompts.base_instructions() == _MARKERS["notice"]


@pytest.mark.parametrize(
    ("removed", "message"),
    [
        ("## 委譲を受けた担当であることの通知", "##「委譲を受けた担当であることの通知」が無い"),
        ("### 探索", "###「探索」が無い"),
        ("<!-- subagent-result-contract:end -->", "subagent-result-contract"),
    ],
)
def test_missing_heading_or_marker_fails_with_source_and_heading(playbook: pathlib.Path, removed: str, message: str) -> None:
    """見出しか標識が無い原本では、空の本文で起動せず、原本のパスと欠けた見出しを示して失敗する。"""
    playbook.write_text(_playbook_fixture(_MARKERS).replace(removed, ""), encoding="utf-8")
    with pytest.raises(session_errors.ActionableRuntimeError) as raised:
        launch_prompts.launch_prompt("explore")
    assert str(playbook) in str(raised.value)
    assert message in str(raised.value)


@pytest.mark.parametrize("kind", _KINDS)
def test_distributed_playbook_assembles_every_launch_kind(kind: LaunchKind) -> None:
    """配布する原本から全modeの起動の本文と自動再開を組み立てられる。本文の文言には依存しない。"""
    launch_prompts._playbook_lines.cache_clear()
    body = launch_prompts.launch_prompt(kind)
    assert body.count(launch_prompts.base_instructions()) == 1
    assert body.count(launch_prompts.result_contract()) == 1
    assert "## 返却形式の受け渡し" in launch_prompts.result_contract()
    assert "## " + launch_prompts.ROLES_HEADING not in body
    assert launch_prompts.auto_resume_notice()
