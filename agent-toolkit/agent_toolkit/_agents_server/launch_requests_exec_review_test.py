"""`_agents_server/launch_requests.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import launch_requests, mcp_tools
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.launch_requests_support import TaskManager

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize(
    "defect",
    ["omitted", "direct", "columns", "major", "supplement", "duplicate", "missing-wi", "valid", "old", "old-scope", "human"],
)
@pytest.mark.parametrize("input_kind", ["record", "record-file", "plan"])
@pytest.mark.asyncio
async def test_exec_review_condition_table_preflight(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, defect: str, input_kind: str
) -> None:
    """公開された役割入力検査で新形式AWIの不足を拒否し、完全表と旧形式を受理する。"""
    name = "20261028-130047-001.md"
    notes = tmp_path / "notes"
    directory = notes / "2026" / "10"
    directory.mkdir(parents=True)
    body = (
        "---\ntype: awi\nsource: fix\nstate: inbox\ntarget_repo: github.com/ak110/dotfiles\n---\n# 要求\n## 完成条件\n- 条件\n"
    )
    if defect == "old":
        body += "## 実現性\n可能\n"
    elif defect == "old-scope":
        body += "## 適用範囲\n範囲\n"
    elif defect == "human":
        body = body.replace("source: fix\n", "")
    (directory / name).write_text(body, encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    contract = tmp_path / "contract.md"
    header = "| WI | 条件番号 | 区分 | 内容 | 出典 |\n| --- | --- | --- | --- | --- |\n"
    major = f"| {name} | 1 | 主要完成状態 | 完了 | 本文 |\n"
    supplement = f"| {name} | 2 | 補足条件 | 条件 | 完成条件 |\n"
    content = header + major + supplement
    if defect == "columns":
        content = "条件表なし"
    elif defect == "major":
        content = header + supplement
    elif defect == "supplement":
        content = header + major
    elif defect == "duplicate":
        content = header + major + supplement.replace("| 2 |", "| 1 |")
    contract.write_text(content, encoding="utf-8")
    params = {
        "レビュー基準": "WI",
        "引き継ぎ記録先": f"{tmp_path / 'handoff.md'}（新規）",
        "WI記録": name,
        "開始時のHEAD": "abc1234",
    }
    if input_kind == "record-file":
        record = tmp_path / "wi-record.md"
        record.write_text(name, encoding="utf-8")
        params["WI記録"] = str(record)
    elif input_kind == "plan":
        plan = tmp_path / "plan.md"
        plan.write_text(f"# 計画\n## 概要\n### 計画メタ情報\n- 関連WI:\n  - {name}: 要求\n", encoding="utf-8")
        params.pop("WI記録")
        params.pop("開始時のHEAD")
        params["レビュー基準"] = "計画"
        params["計画"] = str(plan)
    if defect not in {"omitted", "old", "old-scope", "human"}:
        params["review_contract"] = "直接契約" if defect == "direct" else str(contract)
    if defect == "missing-wi":
        other = "20261028-130047-002.md"
        (directory / other).write_text(body, encoding="utf-8")
        plan = tmp_path / "other-plan.md"
        plan.write_text(f"# 計画\n## 概要\n### 計画メタ情報\n- 関連WI:\n  - {other}: 別の要求\n", encoding="utf-8")
        params["計画"] = str(plan)
        params["レビュー基準"] = "計画、WI"
    document = launch_requests._SHARE_DIRECTORY / "exec-review.subagent.md"
    sender = AsyncMock(return_value={"session_id": "review", "status": "running", "label": "担当"})
    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=sender))
    if defect in {"valid", "old", "old-scope", "human"}:
        response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(document), extra_params=params)
        assert response["session_id"] == "review"
        sender.assert_awaited_once()
    else:
        with pytest.raises(ActionableError, match="review_contract") as rejected:
            await mcp_tools.start(str(tmp_path), subagent_md_path=str(document), extra_params=params)
        assert "exec-review-recording.md" in str(rejected.value)
        sender.assert_not_awaited()


@pytest.mark.parametrize("form", ["ordered", "bullet", "paragraph"])
@pytest.mark.parametrize("coverage", ["whole-section", "partial", "wrong-wi", "number-prefix", "complete", "reason"])
@pytest.mark.asyncio
async def test_exec_review_reflection_item_coverage_preflight(tmp_path, monkeypatch, form, coverage) -> None:
    """公開startで反映内容7項目の各番号が条件表の出典にあるかを確かめ、背景の理由行も受理する。"""
    name = "20261028-130047-001.md"
    notes = tmp_path / "notes"
    directory = notes / "2026/10"
    directory.mkdir(parents=True)
    items = [f"成果{number}を反映する。" for number in range(1, 8)]
    if form == "paragraph":
        reflection = "\n\n".join(items)
    else:
        reflection = "背景の説明。\n\n" + "\n".join(
            f"{str(number) + '.' if form == 'ordered' else '-'} {item}" for number, item in enumerate(items, start=1)
        )
    body = (
        "---\ntype: awi\nsource: fix\nstate: inbox\ntarget_repo: github.com/ak110/dotfiles\n---\n"
        f"# 要求\n## 反映内容と反映先\n{reflection}\n\n## 完成条件\n- 条件\n"
    )
    (directory / name).write_text(body, encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    rows = ["| WI | 条件番号 | 区分 | 内容 | 出典 |", "| --- | --- | --- | --- | --- |"]
    if coverage == "whole-section":
        rows.extend(f"| {name} | {n} | 主要完成状態 | 完了 | 目的・反映内容と反映先・原因分析 |" for n in range(1, 4))
    else:
        for number in range(1, 8):
            source = f"反映内容{number}"
            row_name = name
            kind, content = "主要完成状態", "完了"
            if number == 4:
                if coverage == "partial":
                    source = "反映内容と反映先"
                elif coverage == "number-prefix":
                    source = "反映内容40"
                elif coverage == "wrong-wi":
                    row_name = "20261028-130047-002.md"
                elif coverage == "reason":
                    content = "背景であり完成判定に使わない。"
            rows.append(f"| {row_name} | {number} | {kind} | {content} | {source} |")
    rows.append(f"| {name} | 8 | 補足条件 | 条件 | 完成条件1 |")
    contract = tmp_path / "contract.md"
    contract.write_text("\n".join(rows), encoding="utf-8")
    params = {
        "レビュー基準": "WI",
        "WI記録": name,
        "開始時のHEAD": "abc1234",
        "引き継ぎ記録先": f"{tmp_path / 'handoff.md'}（新規）",
        "review_contract": str(contract),
    }
    sender = AsyncMock(return_value={"session_id": "review", "status": "running", "label": "担当"})
    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=sender))
    if coverage in {"complete", "reason"}:
        response = await mcp_tools.start(str(tmp_path), subagent_md_path="exec-review", extra_params=params)
        assert response["session_id"] == "review"
        sender.assert_awaited_once()
    else:
        with pytest.raises(ActionableError, match="反映内容4") as rejected:
            await mcp_tools.start(str(tmp_path), subagent_md_path="exec-review", extra_params=params)
        assert "行を追加" in str(rejected.value)
        if coverage == "whole-section":
            assert all(f"反映内容{number}" in str(rejected.value) for number in range(1, 8))
        sender.assert_not_awaited()


@pytest.mark.parametrize("defect", ["valid", "missing-wi", "unreadable", "malformed", "relative"])
@pytest.mark.asyncio
async def test_exec_review_saved_wi_originals_preflight(tmp_path, monkeypatch, defect) -> None:
    """保存版指定の不足を現行キューで補わず、同じ保存本文を公開startが検査する。"""
    name = "20261028-130047-001.md"
    saved = tmp_path / name
    saved.write_text("---\ntype: awi\nsource: fix\n---\n# 保存要求\n## 完成条件\n- 保存条件\n", encoding="utf-8")
    notes = tmp_path / "notes/2026/10"
    notes.mkdir(parents=True)
    (notes / name).write_text(
        "---\ntype: awi\nsource: fix\n---\n# 後日の要求\n## 完成条件\n- 条件1\n- 条件2\n", encoding="utf-8"
    )
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes.parent.parent))
    manifest = tmp_path / "originals.json"
    manifest.write_text(json.dumps({name: str(saved)}), encoding="utf-8")
    if defect == "missing-wi":
        manifest.write_text("{}", encoding="utf-8")
    elif defect == "unreadable":
        saved.unlink()
    elif defect == "malformed":
        manifest.write_text("[]", encoding="utf-8")
    elif defect == "relative":
        manifest.write_text(json.dumps({name: name}), encoding="utf-8")
    contract = tmp_path / "contract.md"
    contract.write_text(
        "| WI | 条件番号 | 区分 | 内容 | 出典 |\n| --- | --- | --- | --- | --- |\n"
        f"| {name} | 1 | 主要完成状態 | 保存要求 | H1 |\n"
        f"| {name} | 2 | 補足条件 | 保存条件 | 完成条件1 |\n",
        encoding="utf-8",
    )
    params = {
        "レビュー基準": "WI",
        "WI記録": name,
        "開始時のHEAD": "abc1234",
        "引き継ぎ記録先": f"{tmp_path / 'handoff.md'}（新規）",
        "評価用WI原本": str(manifest),
        "review_contract": str(contract),
    }
    sender = AsyncMock(return_value={"session_id": "review", "status": "running", "label": "担当"})
    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=sender))
    if defect == "valid":
        response = await mcp_tools.start(str(tmp_path), subagent_md_path="exec-review", extra_params=params)
        assert response["session_id"] == "review"
        sender.assert_awaited_once()
    else:
        with pytest.raises(ActionableError, match="評価用WI原本"):
            await mcp_tools.start(str(tmp_path), subagent_md_path="exec-review", extra_params=params)
        sender.assert_not_awaited()


@pytest.mark.parametrize(
    "case,expected",
    [
        ("plan-path-in-basis", "`計画: "),
        ("plan-omitted", "`計画`に必要な入力がありません: `計画`"),
        ("unknown-basis", "`AWI`"),
        ("duplicate-basis", "重複"),
        ("ci-missing", "`原因commitOID`"),
        ("wi-without-plan-missing-head", "`開始時のHEAD`"),
        ("plan-unreadable", "通常ファイルを参照できない"),
    ],
)
@pytest.mark.asyncio
async def test_exec_review_basis_preflight_rejects_before_launch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, case: str, expected: str
) -> None:
    """公開`start`は基準名の不正と基準別の追加入力の欠落を、担当の起動前に修正案付きで拒否する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "notes"))
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n## 概要\n### 計画メタ情報\n- 関連WI: なし\n", encoding="utf-8")
    params = {"引き継ぎ記録先": f"{tmp_path / 'handoff.md'}（新規）"}
    if case == "plan-path-in-basis":
        params["レビュー基準"] = f"計画: {plan}"
    elif case == "plan-omitted":
        params["レビュー基準"] = "計画"
    elif case == "unknown-basis":
        params |= {"レビュー基準": "計画、AWI", "計画": str(plan)}
    elif case == "duplicate-basis":
        params |= {"レビュー基準": "計画, 計画", "計画": str(plan)}
    elif case == "ci-missing":
        params |= {"レビュー基準": "CI記録", "CI記録": "記録", "修正系列の開始時のHEAD": "abc1234"}
    elif case == "wi-without-plan-missing-head":
        params |= {"レビュー基準": "WI", "WI記録": "20261028-130047-001.md"}
    else:
        params |= {"レビュー基準": "計画", "計画": str(tmp_path / "missing.md")}
    document = launch_requests._SHARE_DIRECTORY / "exec-review.subagent.md"
    sender = AsyncMock(return_value={"session_id": "review", "status": "running", "label": "担当"})
    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=sender))
    with pytest.raises(ActionableError) as rejected:
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(document), extra_params=params)
    assert expected in str(rejected.value)
    if case != "plan-unreadable":
        assert "レビュー基準" in rejected.value.next_action
    sender.assert_not_awaited()


@pytest.mark.parametrize(
    "params",
    [
        {"レビュー基準": "計画、WI"},
        {"レビュー基準": "CI記録", "CI記録": "記録", "修正系列の開始時のHEAD": "abc1234", "原因commitOID": "def5678"},
        {"レビュー基準": "WI", "WI記録": "記録なし", "開始時のHEAD": "abc1234"},
    ],
)
@pytest.mark.asyncio
async def test_exec_review_basis_preflight_accepts_contract_inputs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, params: dict[str, str]
) -> None:
    """基準名と基準別の追加入力が揃った起動は、公開`start`から担当の起動へ進む。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "notes"))
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n## 概要\n### 計画メタ情報\n- 関連WI: なし\n", encoding="utf-8")
    extra = {"引き継ぎ記録先": f"{tmp_path / 'handoff.md'}（新規）", **params}
    if "計画" in params["レビュー基準"]:
        extra["計画"] = str(plan)
    document = launch_requests._SHARE_DIRECTORY / "exec-review.subagent.md"
    sender = AsyncMock(return_value={"session_id": "review", "status": "running", "label": "担当"})
    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=sender))
    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(document), extra_params=extra)
    assert response["session_id"] == "review"
    sender.assert_awaited_once()
