"""公開起動でのfrontmatterと旧宣言の読取を検証する。"""

import json
from pathlib import Path

import pytest
import yaml

from agent_toolkit._agents_server import launch_requests
from agent_toolkit._testing.task_document_contract import validate_frontmatter


def _document(tmp_path: Path, name: str, settings: dict) -> Path:
    """独立したpluginのshareへ、公開起動が受理する役割文書を置く。"""
    manifest = tmp_path / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(exist_ok=True)
    manifest.write_text(json.dumps({"name": "agent-toolkit"}), encoding="utf-8")
    path = tmp_path / "share" / f"{name}.subagent.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        "---\n"
        + yaml.safe_dump(settings, allow_unicode=True, sort_keys=False)
        + "---\n\n# 単発の確認\n\n入力を読んで結果を返す。\n",
        encoding="utf-8",
    )
    return path


def _settings() -> dict:
    return {
        "mode": "explore",
        "model_type": "low_tier",
        "usage": "chores",
        "required_inputs": ["対象"],
        "optional_inputs": ["観点"],
        "writes_worktree": False,
        "continuations": [{"instruction": "追加確認", "required": ["追加の対象"]}],
    }


def test_new_role_launches_without_role_name_mapping(tmp_path: Path) -> None:
    """役割名の対応表を使わずに起動本文を生成し、継続指示の必須入力を確かめる。"""
    path = _document(tmp_path, "new-lightweight-check", _settings())
    model, usage, prompt, mode, handoff = launch_requests.task_document_request(
        str(path), {"対象": "今回の成果物", "観点": "参照の到達性"}
    )
    assert (model, usage, mode, handoff) == ("low_tier", "chores", "explore", None)
    assert "対象: 今回の成果物" in prompt and "観点: 参照の到達性" in prompt
    assert 'kind="work-contract"' not in prompt and "引き継ぎ記録先:" not in prompt
    assert (
        launch_requests.continuation_prompt(prompt, (), instruction="追加確認", extra_params={"追加の対象": "追加成果物"})
        == "追加確認\n追加の対象: 追加成果物"
    )
    with pytest.raises(ValueError, match="必須欠落"):
        launch_requests.continuation_prompt(prompt, (), instruction="追加確認", extra_params={})


@pytest.mark.parametrize("inputs, expected", [({}, "必須入力が欠け"), ({"対象": "x", "説明": "y"}, "宣言していない")])
def test_frontmatter_inputs_are_checked_before_launch(tmp_path: Path, inputs: dict, expected: str) -> None:
    path = _document(tmp_path, "input-check", _settings())
    with pytest.raises(ValueError, match=expected):
        launch_requests.task_document_request(str(path), inputs)


@pytest.mark.parametrize(
    "field, value",
    [
        ("mode", "unknown"),
        ("model_type", ""),
        ("usage", "unknown"),
        ("required_inputs", "対象"),
        ("optional_inputs", ["対象"]),
        ("writes_worktree", "false"),
        ("unexpected", True),
        ("continuations", [{"instruction": "追加確認", "paths": ["未宣言"]}]),
    ],
)
def test_static_validation_detects_invalid_definitions(tmp_path: Path, field: str, value: object) -> None:
    settings = _settings()
    settings[field] = value
    path = _document(tmp_path, "refine-prompt", settings)
    declaration = validate_frontmatter(path, path.read_text(encoding="utf-8"))
    assert isinstance(declaration, str) and "frontmatter" in declaration


def test_duplicate_frontmatter_key_is_rejected(tmp_path: Path) -> None:
    path = _document(tmp_path, "refine-prompt", _settings())
    path.write_text(
        path.read_text(encoding="utf-8").replace("mode: explore", "mode: explore\nmode: delegate"), encoding="utf-8"
    )
    result = validate_frontmatter(path, path.read_text(encoding="utf-8"))
    assert isinstance(result, str) and "重複" in result


@pytest.mark.parametrize("text", ["---\nmode: [\n---\n", "---\nmode: explore\n", "---\nmode: explore\n---\n"])
def test_unreadable_frontmatter_does_not_fall_back_to_legacy(tmp_path: Path, text: str) -> None:
    path = _document(tmp_path, "refine-prompt", _settings())
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        launch_requests.task_document_request(str(path), {"対象": "x"})


def test_legacy_role_configuration_and_continuation_stay_readable(tmp_path: Path) -> None:
    path = _document(tmp_path, "refine-prompt", _settings())
    path.write_text(
        "# 旧版の単発評価\n\n## 入力\n\n```text\n必須入力名: 対象プロンプト,シナリオ\n"
        "mode: explore\n作業ツリーへの書込: なし\n"
        '継続入力: {"instruction":"追加確認","required":["対象"]}\n```\n',
        encoding="utf-8",
    )
    model, usage, prompt, mode, handoff = launch_requests.task_document_request(
        str(path), {"対象プロンプト": "評価対象", "シナリオ": "単発評価"}
    )
    assert (model, usage, mode, handoff) == ("medium_tier", "base", "explore", None)
    assert (
        launch_requests.continuation_prompt(prompt, (), instruction="追加確認", extra_params={"対象": "追加"})
        == "追加確認\n対象: 追加"
    )
