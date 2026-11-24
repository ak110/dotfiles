from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from agent_toolkit._agents_server import launch_prompts, launch_requests, result_contract, state
from agent_toolkit._common import task_documents
from agent_toolkit._testing.task_document_contract import validate_frontmatter

_PLUGIN_ROOT = Path(__file__).resolve().parent
_ROLES_DIR = _PLUGIN_ROOT / "skills" / "atk-mode" / "roles"


@pytest.mark.parametrize("path", task_documents.role_documents(_PLUGIN_ROOT), ids=lambda path: path.name)
def test_distributed_frontmatter_is_valid(path: Path) -> None:
    result = validate_frontmatter(path, path.read_text(encoding="utf-8"))
    assert isinstance(result, task_documents.TaskDocumentDeclaration), result
    assert result == task_documents.read_declaration(path)


@pytest.mark.parametrize("path", task_documents.role_documents(_PLUGIN_ROOT), ids=lambda path: path.name)
def test_distributed_outputs_use_shared_result_contract(path: Path) -> None:
    """将来の役割も共通契約へ接続し、理由の区分欠落・集合外・インデント違反を検出する。"""
    body = path.read_text(encoding="utf-8").split("## 出力", 1)[1]
    assert "subagent.md" in body and "返却形式の受け渡し" in body
    declaration = task_documents.read_declaration(path)
    assert isinstance(declaration, task_documents.TaskDocumentDeclaration)
    session = state.SessionState(
        "role-form",
        str(path.parent),
        prompt=task_documents.task_prompt_header(path),
        launch_kind=declaration.launch_kind,
    )
    examples = re.findall(r"(?ms)^```text\n(.*?)^```[ \t]*$", body)
    if path.name == "reader-fit-review.subagent.md":
        session.prompt += f"\n入力:\n成果物: {path}"
        examples = [examples[0].replace("<絶対パス>", str(path)).replace("<completed|blocked>", "completed")]
    if path.name == "usability-review.subagent.md":
        examples = [examples[0].replace("判定: 合格 | 不合格 | 未確定", "判定: 合格") + examples[1]]
    reasons = []
    for example in examples:
        example = example.replace("<共通契約の区分>", "owner_input")
        example = re.sub(r"(?m)^状態: <[a-z]+(?:\|[a-z]+)+>$", "状態: completed", example)
        reasons.append(result_contract.invalid_result_reason(session, {"status": "completed", "agent_message": example}))
    assert None in reasons, reasons


def test_shared_classification_table_matches_runtime() -> None:
    rules = launch_prompts.result_contract()
    values = re.findall(r"^\| `([a-z_]+)` \|", rules, flags=re.MULTILINE)
    assert values and len(values) == len(set(values))
    document = task_documents.require_role_document(_PLUGIN_ROOT, "exec")
    session = state.SessionState("role-form", str(document.parent), prompt=task_documents.task_prompt_header(document))
    for value in values:
        session.api_error = {"type": "unavailable", "first_at": session.started_at} if value == "runtime_unavailable" else None
        message = f"続行できない区分: {value}\n続行できない理由: 入力が必要"
        assert result_contract.invalid_result_reason(session, {"status": "completed", "agent_message": message}) is None
    assert result_contract.invalid_result_reason(
        session, {"status": "completed", "agent_message": "続行できない区分: unknown\n続行できない理由: 入力が必要"}
    )


def test_role_documents_have_no_bare_return_line_examples() -> None:
    bare_label_line = re.compile(r"^[^\s#\-*>|`][^\n:`]*: \S.*$")
    offending: list[str] = []
    for path in sorted(_ROLES_DIR.glob("*.md")):
        in_fence = False
        in_frontmatter = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if lineno == 1 and line == "---":
                in_frontmatter = True
                continue
            if in_frontmatter:
                if line == "---":
                    in_frontmatter = False
                continue
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue

            if line.lstrip().startswith("<!--") and line.rstrip().endswith("-->"):
                continue
            if bare_label_line.match(line) and not line.startswith("方針: "):
                offending.append(f"{path.name}:{lineno}: {line}")
    assert not offending


@pytest.mark.parametrize("grouped", [False, True])
def test_repository_process_public_request_accepts_single_and_group(tmp_path: Path, grouped: bool) -> None:
    target = {
        "target_repo": "example.test/team/one",
        "worktree": str(tmp_path / "one"),
        "candidate_file": str(tmp_path / "candidate.jsonl"),
        "readiness": "ready",
        "lane_limit": "1",
        "lane_session_id": "test-lane-session",
    }
    params = {"引き継ぎ記録先": str(tmp_path / "handoff.md")}
    source = tmp_path / "repositories.json"
    if grouped:
        targets = [target, {**target, "target_repo": "example.test/team/two", "worktree": str(tmp_path / "two")}]
        source.write_text(json.dumps(targets), encoding="utf-8")
        params["repositories"] = str(source)
    else:
        params.update(target)
    role = _PLUGIN_ROOT / "skills/atk-mode/roles/repository-process.subagent.md"
    whole = tmp_path / "work-contract.md"
    whole.write_text("目的: 対象別の公開入力を保持する\n", encoding="utf-8")
    model, prompt, mode, _handoff = launch_requests.task_document_request(str(role), params, whole_contract_path=str(whole))
    assert (model, mode) == ("execute", "delegate")
    if grouped:
        assert f"repositories: {source}" in prompt
        assert "target_repo: example.test/team/one" not in prompt
    else:
        assert "target_repo: example.test/team/one" in prompt
        assert "candidate_file:" in prompt and "lane_session_id: test-lane-session" in prompt
