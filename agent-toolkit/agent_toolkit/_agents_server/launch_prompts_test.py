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
from agent_toolkit._common import delegated_session, plugin_roots, runtime_environment
from agent_toolkit._testing.agents_server_claude_support import _capture_options


@pytest.mark.skipif(os.name == "nt", reason="POSIX shellによる実行の受入例")
def test_python_runtime_information_runs_json_without_python_on_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    root = tmp_path / "toolkit"
    if existing:
        root.mkdir()
    with pytest.raises(plugin_roots.PluginRootError, match="agent-toolkitの実在するskills"):
        launch_prompts.plugin_resource_instructions(root, "codex", str(tmp_path))


def _toolkit(root: pathlib.Path, version: str) -> pathlib.Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        f'{{"name": "agent-toolkit", "version": "{version}"}}', encoding="utf-8"
    )
    return root


_SERVER_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("starting", [True, False], ids=["start", "continue"])
@pytest.mark.parametrize(
    ("caller_version", "delegated", "expected"),
    [
        ("2.305.2", False, "caller"),
        ("9999.0.0", False, "caller"),
        ("same", False, None),
        (None, False, "unknown"),
        ("relative", False, "unknown"),
        ("missing", False, "unknown"),
        ("2.305.2", True, None),
    ],
    ids=["older-caller", "newer-caller", "same-version", "old-forwarder", "relative-root", "missing-root", "delegated-caller"],
)
def test_resource_instructions_name_caller_launcher_on_version_skew(
    tmp_path: pathlib.Path, starting: bool, caller_version: str | None, delegated: bool, expected: str | None
) -> None:
    server_version = launch_prompts._plugin_version(_SERVER_ROOT)
    assert server_version is not None
    environment = {}
    caller = tmp_path / "caller"
    if caller_version == "relative":
        environment[delegated_session.CALLER_PLUGIN_ROOT_ENV] = "caller"
    elif caller_version == "missing":
        environment[delegated_session.CALLER_PLUGIN_ROOT_ENV] = str(caller)
    elif caller_version is not None:
        _toolkit(caller, server_version if caller_version == "same" else caller_version)
        environment[delegated_session.CALLER_PLUGIN_ROOT_ENV] = str(caller)
    if delegated:
        environment[delegated_session.OWNER_SESSION_ENV] = "parent"
    with runtime_environment.bind(runtime_environment.CallerContext(environment, tmp_path)):
        text = launch_prompts.plugin_resource_instructions(_SERVER_ROOT, "claude", str(tmp_path), starting=starting)
    if expected is None:
        assert 'kind="caller-runtime"' not in text
        return
    assert 'kind="caller-runtime"' in text
    server_command, _note = launch_prompts.plugin_environment.launcher_command(_SERVER_ROOT)
    if expected == "caller":
        caller_command, _note = launch_prompts.plugin_environment.launcher_command(caller)
        assert f"版{caller_version}（起動部分: {caller_command}）" in text
        assert f"版{server_version}" in text
        assert "合否は、委譲元の起動部分で実行した結果で決めよ" in text
    else:
        assert "委譲元のCLIの版は不明" in text
        assert f"版{server_version}、起動部分: {server_command}" in text
        assert "委譲元が同じ原稿・引数で実行した結果を使え" in text
        assert "委譲先の起動部分での結果だけで合否を確定したり、原稿を差し戻したりしてはいけない" in text
        assert "CLIの判定は未確認" in text
        assert "サブコマンド・引数と原稿の絶対パスを返せ" in text


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
    path = tmp_path / "subagent.md"
    path.write_text(_playbook_fixture(_MARKERS), encoding="utf-8")
    monkeypatch.setattr(launch_prompts, "SUBAGENT_PLAYBOOK", path)
    launch_prompts._playbook_lines.cache_clear()
    yield path
    launch_prompts._playbook_lines.cache_clear()


_KINDS: tuple[LaunchKind, ...] = ("delegate", "explore", "shell", "write")


@pytest.mark.usefixtures("agents_server_isolation", "playbook")
def test_launch_kinds_reach_sdk_with_their_sections(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """原本の節が起動の種類ごとに切り出され、別の種類の本文を含まずにSDKへ届く。硬い制約は全ての種類へ届く。"""
    captured = _capture_options(monkeypatch)
    for kind in _KINDS:
        claude_options._build_options(str(tmp_path), "model", "medium", launch_kind=kind)  # pylint: disable=protected-access
        delivered = captured["system_prompt"]
        assert isinstance(delivered, str)
        assert delivered.count(_MARKERS["notice"]) == 1, kind
        assert delivered.count(_MARKERS["constraints"]) == 1, kind
        assert delivered.count(_MARKERS["result"]) == 1, kind
        assert _MARKERS["fenced"] in delivered, kind
        assert _MARKERS["auto-resume"] in delivered, kind
        assert delivered.count(_MARKERS[kind]) == 1, kind
        for other in set(_KINDS) - {kind}:
            assert _MARKERS[other] not in delivered, (kind, other)
        for whole_only in ("intro", "closing"):
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
    codex = codex_instructions.codex_instruction_fields(kind)
    delivered = {
        "claude": captured["system_prompt"],
        "opencode": opencode.system_prompt(kind),
        "agy": antigravity._system_prompt(kind),  # pylint: disable=protected-access
    }
    for engine, prompt in delivered.items():
        assert isinstance(prompt, str)
        assert prompt.count(body) == 1, engine
        assert "WebFetch・WebSearch" not in prompt
    assert codex["baseInstructions"] == _MARKERS["notice"]
    developer = codex["developerInstructions"]
    assert _MARKERS["notice"] not in developer
    assert launch_prompts.NOTICE_HEADING not in developer
    assert developer.count(_MARKERS["constraints"]) == 1
    assert developer.count(_MARKERS[kind]) == 1
    assert developer.count(_MARKERS["result"]) == 1
    assert ("WebFetch・WebSearch" in developer) is (kind == "explore")
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
    launch_prompts._playbook_lines.cache_clear()
    body = launch_prompts.launch_prompt(kind)
    assert body.count(launch_prompts.base_instructions()) == 1
    assert body.count("## " + launch_prompts.CONSTRAINTS_HEADING) == 1
    assert body.count(launch_prompts.result_contract()) == 1
    assert "## 返却形式の受け渡し" in launch_prompts.result_contract()
    assert "## " + launch_prompts.ROLES_HEADING not in body
    assert launch_prompts.auto_resume_notice()


# test-copy-contract: 起動側とhook側の区分を比べ、スキルを使わない軽量の委譲先へスキルの起動を案内する不整合を防ぐ。
def test_lightweight_launch_kinds_match_hook_copy() -> None:
    assert delegated_session.LIGHTWEIGHT_LAUNCH_KINDS == launch_prompts.LIGHTWEIGHT_LAUNCH_KINDS
