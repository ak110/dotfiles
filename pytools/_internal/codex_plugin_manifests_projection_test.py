# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""codex_plugin_manifestsのprojectionの試験。"""

# pylint: disable=protected-access

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pytools._internal import codex_plugin_manifests as subject
from pytools._internal.codex_plugin_manifests_test_helpers import (
    _CODEX_HOOK_PREFIX,
    _CODEX_PLUGIN_VALIDATOR,
    _CODEX_PLUGIN_VALIDATOR_AVAILABLE,
)
from pytools._internal.codex_plugin_manifests_test_helpers import _proxy_endpoint as _proxy_endpoint
from pytools._internal.codex_plugin_manifests_test_helpers import _quality_root as _quality_root
from pytools._internal.codex_plugin_manifests_test_helpers import manifest_root_fixture as manifest_root_fixture


def test_codex_interface_descriptions_and_prompts(manifest_root: Path) -> None:
    """Codex向けinterfaceが`longDescription`と`defaultPrompt`の契約を満たす。"""
    subject.sync(manifest_root)
    generated = json.loads((manifest_root / subject.PLUGIN_TARGET).read_text(encoding="utf-8"))
    interface = generated["interface"]

    assert isinstance(interface["longDescription"], str)
    assert interface["longDescription"]
    assert isinstance(interface["defaultPrompt"], list)
    assert interface["defaultPrompt"]
    assert all(isinstance(prompt, str) and prompt and len(prompt) <= 128 for prompt in interface["defaultPrompt"])


@pytest.mark.skipif(
    not _CODEX_PLUGIN_VALIDATOR_AVAILABLE,
    reason="Codex CLIまたは同梱plugin検証器が存在しない",
)
def test_codex_plugin_validator_reports_only_known_schema_deviations() -> None:
    """Codex検証器の既知の指摘集合だけを許容する。

    Codex 0.154.0同梱の`plugin-creator/references/plugin-json-spec.md`は、`hooks`を正規fieldとして
    定義しながら、検証の節では未対応fieldとして拒否すると述べており、同一資料内で矛盾する。
    `hooks`と`./.mcp.codex.json`を持つ現行manifestは`installed: true`かつ`enabled: true`である。
    Claude向けfrontmatterの`disable-model-invocation: true`は、Codex向けの
    `agents/openai.yaml`で同じ明示起動限定を表しても検証器が指摘する。
    資料上の保証がないまま動作中の構成を変えないため、この前提が変わるまで期待値を空にしない。
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_CODEX_PLUGIN_VALIDATOR), str(subject.repo_root() / subject.CODEX_PLUGIN_ROOT_TARGET)],
        capture_output=True,
        check=False,
        text=True,
    )
    findings = {line.removeprefix("- ") for line in result.stdout.splitlines() if line.startswith("- ")}
    expected = {
        "plugin.json field `hooks` is not accepted by plugin validation",
        "plugin.json field `mcpServers` must resolve to `.mcp.json`",
        "skill `add-awi-by-user` frontmatter field `disable-model-invocation` must be false",
    }
    details = f"終了コード: {result.returncode}\n標準出力:\n{result.stdout}\n標準エラー:\n{result.stderr}"
    if result.returncode == 0:
        details = "意図的な逸脱が解消されたため、期待値の更新が必要である。\n" + details

    assert result.returncode == 1 and findings == expected, details


def test_codex_projection_limits_matchers_and_timeout(manifest_root: Path) -> None:
    """Claude向けの空matcherと広いmatcherを引き継がず、SessionEndへ上限を明示する。"""
    subject.sync(manifest_root)
    generated = json.loads((manifest_root / subject.HOOKS_TARGET).read_text(encoding="utf-8"))["hooks"]

    assert generated["PreToolUse"][0]["matcher"] == subject.CODEX_HOOK_ALLOWLIST["PreToolUse"].matcher
    assert "spawn_agent" in generated["PreToolUse"][0]["matcher"].split("|")
    assert generated["PostToolUse"][0]["matcher"] == subject.CODEX_HOOK_ALLOWLIST["PostToolUse"].matcher
    assert generated["SessionEnd"][0]["hooks"][0]["timeout"] <= 3
    assert "matcher" not in generated["SubagentStop"][0]
    assert generated["Stop"] == [
        {"hooks": [{"type": "command", "command": _CODEX_HOOK_PREFIX + "stop" + subject.HOOK_FAILURE_SUFFIX}]}
    ]
    assert "Bash" in generated["PostToolUse"][0]["matcher"].split("|")
    # PostToolUseは資料本文を返さないため、追加コンテキストの退避上限を外さない。
    assert all("additionalContextLimit" not in handler for handler in generated["PostToolUse"][0]["hooks"])
    assert all("timeout" not in handler for handler in generated["PreToolUse"][0]["hooks"])


def test_codex_projection_replaces_output_command() -> None:
    projection = subject.CodexHookProjection(("source",), output_command="target")

    assert projection.project({"hooks": []}, [{"type": "command", "command": "source"}]) == {
        "hooks": [{"type": "command", "command": "target"}]
    }


def test_codex_projection_preserves_command_without_replacement() -> None:
    projection = subject.CodexHookProjection(("source",))

    assert projection.project({"hooks": []}, [{"type": "command", "command": "source"}]) == {
        "hooks": [{"type": "command", "command": "source"}]
    }


def test_codex_projection_omits_events_without_allowlisted_handler(manifest_root: Path) -> None:
    """許可表に無いイベントはhooks.jsonにあってもCodexへ配布しない。"""
    hooks = json.loads((manifest_root / subject.HOOKS_SOURCE).read_text(encoding="utf-8"))
    hooks["hooks"]["PreCompact"] = [{"hooks": [{"type": "command", "command": "uv run --no-project --script other.py"}]}]
    (manifest_root / subject.HOOKS_SOURCE).write_text(json.dumps(hooks), encoding="utf-8")

    subject.sync(manifest_root)

    generated = json.loads((manifest_root / subject.HOOKS_TARGET).read_text(encoding="utf-8"))["hooks"]
    assert "PreCompact" not in generated
    assert set(generated) == set(subject.CODEX_HOOK_ALLOWLIST) | set(subject.CODEX_ONLY_HOOKS)


def test_codex_projection_sets_additional_context_limit() -> None:
    group: dict[str, Any] = {"hooks": []}
    handler = [{"type": "command", "command": "source"}]
    projected = subject.CodexHookProjection(("source",), additional_context_limit=0).project(group, handler)
    assert projected["hooks"][0]["additionalContextLimit"] == 0
    projected = subject.CodexHookProjection(("source",)).project(group, handler)
    assert "additionalContextLimit" not in projected["hooks"][0]


def test_sync_reads_all_json_as_utf8(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同期中の全JSON読取がロケールで決まる文字コードを使用しないことを確認する。"""
    subject.sync(manifest_root)
    original_read_text = Path.read_text
    encodings: list[str | None] = []

    def read_text(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        encodings.append(encoding)
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", read_text)

    assert subject.sync(manifest_root) is False
    assert encodings
    assert set(encodings) == {"utf-8"}


__all__ = ["manifest_root_fixture"]
