"""`_agents_server/launch_requests.py`の振る舞いを検証する。"""

from __future__ import annotations

import difflib

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import mcp_tools
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.agents_server_support import _actionable_message, _observed_input_params
from agent_toolkit._testing.git_repository import commit_all, git_output, init_repository
from agent_toolkit._testing.launch_requests_support import TaskManager

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize("review_kind", ["初回レビュー", "再レビュー", "引き継ぎ再レビュー"])
@pytest.mark.parametrize("external", [False, True])
@pytest.mark.asyncio
async def test_start_accepts_saved_reader_materials(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, review_kind: str, external: bool
) -> None:
    """管理内外の本文・差分・前回指摘を、同じ起動境界で受理する。"""
    params = _observed_input_params("reader-fit-review.subagent.md", tmp_path)
    current = tmp_path / ("回答 下書き.md" if external else "guide.md")
    findings = tmp_path / "findings.md"
    findings.write_text("前回指摘と応答・検証証拠\n", encoding="utf-8")
    params.update(
        {
            "成果物": str(current),
            "レビュー種別": review_kind,
            "比較資料": json.dumps(_saved_reader_materials(tmp_path, current), ensure_ascii=False),
            "未解決事項": str(findings),
        }
    )
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "reader", "status": "running", "label": "reader"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    response = await mcp_tools.start(str(tmp_path), subagent_md_path="reader-fit-review", extra_params=params)

    assert response["session_id"] == "reader"
    manager.start.assert_awaited_once()


@pytest.mark.parametrize(
    "defect", [None, "missing", "extra", "duplicate", "unreadable", "mismatch", "new-rereview", "no-array"]
)
@pytest.mark.asyncio
async def test_public_start_reader_material_collection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, defect: str | None
) -> None:
    """複数成果物の1対1の資料対応を公開startで確認し、不備を起動前に拒否する。"""
    params = _observed_input_params("reader-fit-review.subagent.md", tmp_path)
    paths = [tmp_path / "guide.md", tmp_path / "second.md"]
    materials = [_saved_reader_materials(tmp_path, path) for path in paths]
    params["成果物"] = json.dumps([str(path) for path in paths])
    if defect == "missing":
        materials.pop()
    elif defect == "extra":
        extra = tmp_path / "extra.md"
        extra.write_text("対象外\n", encoding="utf-8")
        materials.append({**materials[0], "現行本文": str(extra)})
    elif defect == "duplicate":
        materials[1] = materials[0]
    elif defect == "unreadable":
        materials[1]["今回差分"] = str(tmp_path / "missing.patch")
    elif defect == "mismatch":
        materials[1]["現行本文"] = materials[1]["比較元本文"]
    elif defect == "new-rereview":
        params.update({"レビュー種別": "再レビュー", "未解決事項": "なし"})
        materials[1].update({"比較元本文": "新規", "今回差分": "全文"})
    params["比較資料"] = json.dumps(materials[0] if defect == "no-array" else materials, ensure_ascii=False)
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "reader", "status": "running", "label": "reader"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    if defect:
        with pytest.raises(ActionableError) as raised:
            await mcp_tools.start(str(tmp_path), subagent_md_path="reader-fit-review", extra_params=params)
        assert "再発行" in _actionable_message(raised.value)
        manager.start.assert_not_awaited()
    else:
        assert (await mcp_tools.start(str(tmp_path), subagent_md_path="reader-fit-review", extra_params=params))[
            "session_id"
        ] == "reader"
        manager.start.assert_awaited_once()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.asyncio
async def test_start_accepts_new_reader_artifact_full_text(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    """初回の新規成果物は全文差分を明示すれば旧本文を要しない。"""
    params = _observed_input_params("reader-fit-review.subagent.md", tmp_path)
    params["修正範囲"] = "新規文書。全文が差分"
    if not legacy:
        params["比較資料"] = json.dumps(
            {"比較元本文": "新規", "今回差分": "全文", "現行本文": str(tmp_path / "guide.md")}, ensure_ascii=False
        )
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "reader", "status": "running", "label": "reader"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    await mcp_tools.start(str(tmp_path), subagent_md_path="reader-fit-review", extra_params=params)

    manager.start.assert_awaited_once()


def _saved_reader_materials(
    root: pathlib.Path, current: pathlib.Path, *, before_text: str = "旧本文\n", current_text: str = "現行本文\n"
) -> dict[str, str]:
    """比較元本文・現行本文と、両者の本文間の完全な統一差分を保存し、比較資料の3項目を返す。"""
    current.write_text(current_text, encoding="utf-8")
    before = root / f"{current.stem}-before.md"
    before.write_text(before_text, encoding="utf-8")
    patch = root / f"{current.stem}.patch"
    patch.write_text(
        "".join(
            difflib.unified_diff(
                before_text.splitlines(keepends=True),
                current_text.splitlines(keepends=True),
                f"a/{current.name}",
                f"b/{current.name}",
            )
        ),
        encoding="utf-8",
    )
    return {"比較元本文": str(before), "今回差分": str(patch), "現行本文": str(current)}


@pytest.mark.parametrize("defect", ["other-artifact", "scope-as-full", "stale-current", "described-findings"])
@pytest.mark.asyncio
async def test_reader_fit_start_rejects_mismatched_materials(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    """別成果物の差分、全差分として渡した判定範囲差分、異なる版の現行本文、説明の混在した未解決事項を起動前に拒否する。"""
    params = _observed_input_params("reader-fit-review.subagent.md", tmp_path)
    current = tmp_path / "guide.md"
    materials = _saved_reader_materials(tmp_path, current, before_text="一\n二\n三\n", current_text="一\n改\n三\n")
    findings = tmp_path / "findings.md"
    findings.write_text("前回指摘\n", encoding="utf-8")
    params.update({"成果物": str(current), "レビュー種別": "再レビュー", "未解決事項": str(findings)})
    if defect == "other-artifact":
        other = _saved_reader_materials(
            tmp_path, tmp_path / "other.md", before_text="一\n二\n三\n", current_text="一\n改\n三\n"
        )
        current.write_text("一\n改\n三\n", encoding="utf-8")
        materials["今回差分"] = other["今回差分"]
    elif defect == "scope-as-full":
        # 計画別に帰属を限定した差分の形（行位置を持たない@@）を全差分として渡す。
        pathlib.Path(materials["今回差分"]).write_text("--- a/guide.md\n+++ b/guide.md\n@@\n-二\n+改\n", encoding="utf-8")
    elif defect == "stale-current":
        current.write_text("一\n未commitの別の版\n三\n", encoding="utf-8")
    else:
        params["未解決事項"] = f"{findings}（前回指摘2件。修正範囲はsecond.md）"
    params["比較資料"] = json.dumps(materials, ensure_ascii=False)
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "unexpected", "status": "running", "label": "reader"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    with pytest.raises(ActionableError) as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path="reader-fit-review", extra_params=params)

    message = _actionable_message(raised.value)
    assert "再発行" in message
    expected = {"other-artifact": "異なる成果物", "scope-as-full": "判定範囲差分", "stale-current": "現行本文と一致しない"}
    assert expected.get(defect, "説明は保存物へ書く") in message
    manager.start.assert_not_awaited()


@pytest.mark.parametrize("defect", ["outside-paths", "other-artifact"])
@pytest.mark.asyncio
async def test_reader_fit_start_rejects_git_scope_not_matching_paths(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    """Git識別子の形式の判定範囲差分が、pathsに無い成果物や別の成果物の差分を指す場合は起動前に拒否する。"""
    params = _observed_input_params("reader-fit-review.subagent.md", tmp_path)
    repository = init_repository(tmp_path / "repo", files={"guide.md": "旧本文\n"}, commit_message="比較元")
    base = git_output(repository, "rev-parse", "HEAD")
    (repository / "guide.md").write_text("現行本文\n", encoding="utf-8")
    head = commit_all(repository, "本文を更新")
    scope = tmp_path / "scope.diff"
    target = "other.md"
    scope.write_text(f"--- a/{target}\n+++ b/{target}\n@@\n-旧\n+新\n", encoding="utf-8")
    key = "other.md" if defect == "outside-paths" else "guide.md"
    comparison = {
        "repository": str(repository),
        "base": base,
        "head": head,
        "paths": ["guide.md"],
        "判定範囲差分": {key: str(scope)},
    }
    params.update({"成果物": str(repository / "guide.md"), "比較資料": json.dumps(comparison, ensure_ascii=False)})
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "unexpected", "status": "running", "label": "reader"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    with pytest.raises(ActionableError) as raised:
        await mcp_tools.start(str(repository), subagent_md_path="reader-fit-review", extra_params=params)

    assert "判定範囲差分" in _actionable_message(raised.value)
    manager.start.assert_not_awaited()


@pytest.mark.parametrize("form", ["single", "scope", "collection", "legacy", "external", "git", "git-scope"])
@pytest.mark.asyncio
async def test_reader_fit_start_accepts_consistent_material_forms(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    """本文・差分・現行本文が対応する単一・判定範囲付き・集約・旧形式・Git管理外・Git識別子の比較を受理する。"""
    params = _observed_input_params("reader-fit-review.subagent.md", tmp_path)
    cwd = tmp_path
    if form in {"git", "git-scope"}:
        repository = init_repository(tmp_path / "repo", files={"guide.md": "旧本文\n"}, commit_message="比較元")
        base = git_output(repository, "rev-parse", "HEAD")
        (repository / "guide.md").write_text("現行本文\n", encoding="utf-8")
        head = commit_all(repository, "本文を更新")
        comparison: Any = {"repository": str(repository), "base": base, "head": head, "paths": ["guide.md"]}
        if form == "git-scope":
            scope = tmp_path / "guide-scope.diff"
            scope.write_text("--- a/guide.md\n+++ b/guide.md\n@@\n-旧本文\n+現行本文\n", encoding="utf-8")
            comparison["判定範囲差分"] = {"guide.md": str(scope)}
        params["成果物"] = str(repository / "guide.md")
        cwd = repository
    elif form == "collection":
        paths = [tmp_path / "guide.md", tmp_path / "second.md"]
        comparison = [_saved_reader_materials(tmp_path, path) for path in paths]
        params["成果物"] = json.dumps([str(path) for path in paths])
    elif form == "external":
        # Git管理外の回答下書きは、managed-tempの旧本文とdiff -uの絶対パスの見出しで比べる。
        current = tmp_path / "回答 下書き.md"
        comparison = _saved_reader_materials(tmp_path, current)
        patch = pathlib.Path(comparison["今回差分"])
        patch.write_text(
            patch.read_text(encoding="utf-8")
            .replace(f"--- a/{current.name}", f"--- {comparison['比較元本文']}\t2026-10-19 00:00:00")
            .replace(f"+++ b/{current.name}", f"+++ {current}\t2026-10-19 00:00:00"),
            encoding="utf-8",
        )
        params["成果物"] = str(current)
    else:
        current = tmp_path / "guide.md"
        comparison = _saved_reader_materials(tmp_path, current)
        params["成果物"] = str(current)
        if form == "scope":
            scope = tmp_path / "scope.diff"
            scope.write_text("", encoding="utf-8")
            comparison["判定範囲差分"] = str(scope)
        if form == "legacy":
            params["修正範囲"] = json.dumps([comparison["比較元本文"], comparison["今回差分"]], ensure_ascii=False)
            comparison = None
    if comparison is not None:
        params["比較資料"] = json.dumps(comparison, ensure_ascii=False)
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "reader", "status": "running", "label": "reader"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    response = await mcp_tools.start(str(cwd), subagent_md_path="reader-fit-review", extra_params=params)

    assert response["session_id"] == "reader"
    manager.start.assert_awaited_once()


@pytest.mark.parametrize(
    "defect",
    [
        "commit-only",
        "description-only",
        "missing-findings",
        "command-findings",
        "missing-file",
        "directory",
        "invalid-utf8",
        "missing-current",
        "new-rereview",
        "missing-key",
    ],
)
@pytest.mark.asyncio
async def test_start_rejects_reader_material_defects_before_launch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    """比較元・差分・証拠の欠落と参照不能は探索を起動せず補完を案内する。"""
    params = _observed_input_params("reader-fit-review.subagent.md", tmp_path)
    params["レビュー種別"] = "再レビュー"
    params["未解決事項"] = "なし"
    materials = {
        "比較元本文": str(tmp_path / "before.md"),
        "今回差分": str(tmp_path / "diff.patch"),
        "現行本文": str(tmp_path / "guide.md"),
    }
    if defect == "commit-only":
        materials["比較元本文"] = "abc1234"
    elif defect == "description-only":
        materials["今回差分"] = "設定保存節を修正した"
    elif defect == "missing-findings":
        del params["未解決事項"]
    elif defect == "command-findings":
        params["未解決事項"] = "git show HEAD"
    elif defect == "missing-file":
        materials["今回差分"] = str(tmp_path / "missing.patch")
    elif defect == "directory":
        materials["比較元本文"] = str(tmp_path)
    elif defect == "invalid-utf8":
        (tmp_path / "before.md").write_bytes(b"\xff")
    elif defect == "missing-current":
        params["成果物"] = str(tmp_path / "missing.md")
    elif defect == "new-rereview":
        materials.update({"比較元本文": "新規", "今回差分": "全文"})
    elif defect == "missing-key":
        del materials["比較元本文"]
    params["比較資料"] = json.dumps(materials, ensure_ascii=False)
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "unexpected", "status": "running", "label": "reader"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    with pytest.raises(ActionableError) as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path="reader-fit-review", extra_params=params)

    message = _actionable_message(raised.value)
    assert "保存" in message and "再発行" in message
    manager.start.assert_not_awaited()


@pytest.mark.parametrize(
    ("role", "field"),
    [
        ("external-write-review", "文面ファイル"),
        ("external-write-review", "根拠の所在"),
        ("bulk-replace-review", "差分ファイル"),
    ],
)
@pytest.mark.asyncio
async def test_start_rejects_missing_explore_materials_and_accepts_repair(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, role: str, field: str
) -> None:
    """他の探索役割も保存物を欠く起動を拒否し、補完後の再発行を受理する。"""
    params = _observed_input_params(f"{role}.subagent.md", tmp_path)
    params[field] = str(tmp_path / "saved material.md")
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "explorer", "status": "running", "label": "explorer"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    with pytest.raises(ActionableError) as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path=role, extra_params=params)
    assert field in str(raised.value)
    assert "再発行" in _actionable_message(raised.value)
    manager.start.assert_not_awaited()

    pathlib.Path(params[field]).write_text("保存済み資料\n", encoding="utf-8")
    await mcp_tools.start(str(tmp_path), subagent_md_path=role, extra_params=params)
    manager.start.assert_awaited_once()


@pytest.mark.parametrize("json_array", [False, True])
@pytest.mark.asyncio
async def test_start_checks_all_external_evidence_files(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, json_array: bool
) -> None:
    """複数の根拠は全ファイルを確認し、識別子の注記を保存物と取り違えない。"""
    params = _observed_input_params("external-write-review.subagent.md", tmp_path)
    first = tmp_path / "diff.patch"
    second = tmp_path / "CI result.txt"
    third = tmp_path / "additional.txt"
    params["根拠の所在"] = (
        json.dumps([str(first), str(second), str(third)])
        if json_array
        else f"`{first}`（commit `abc123`）\n`{second}`（run ID `123`）\n`{third}`"
    )
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "review", "status": "running", "label": "review"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    with pytest.raises(ActionableError) as rejected:
        await mcp_tools.start(str(tmp_path), subagent_md_path="external-write-review", extra_params=params)
    assert str(second) in str(rejected.value)
    assert str(third) in str(rejected.value)
    manager.start.assert_not_awaited()

    second.write_text("成功したCIの保存結果\n", encoding="utf-8")
    third.write_text("追加の証拠\n", encoding="utf-8")
    await mcp_tools.start(str(tmp_path), subagent_md_path="external-write-review", extra_params=params)
    manager.start.assert_awaited_once()
