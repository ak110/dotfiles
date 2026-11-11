"""起動入力と事前確認を組み立てる共有補助。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import contextlib
import json
import pathlib
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from agent_toolkit._agents_server import launch_requests, mcp_tools
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._testing.agents_server_support_backends import DelayedUnavailableBackend
from agent_toolkit._testing.agents_server_support_common import FakeBackend, _install_backend

_REAL_PLUGIN_PREFLIGHT_ASYNC = launch_requests.check_plugin_commands


def _use_real_plugin_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """事前確認を実装どおりに実行させる。"""
    monkeypatch.setattr(launch_requests, "check_plugin_commands", _REAL_PLUGIN_PREFLIGHT_ASYNC)


def _start_tool() -> Any:
    tool = mcp_tools.mcp._tool_manager.get_tool("start")
    assert tool is not None
    return tool


def _write_declared_task_document(tmp_path: pathlib.Path, input_block: str) -> pathlib.Path:
    """agent-toolkitのshare配下と同じ構造の一時の`<役割名>.subagent.md`を作成し、その絶対パスを返す。"""
    plugin_root = tmp_path / "plugin"
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document = plugin_root / "share" / "declared.subagent.md"
    task_document.parent.mkdir()
    if "作業ツリーへの書込: " not in input_block:
        # 書込宣言は必須行のため、宣言の他の行を確かめる試験では書き込む役割として補う。
        input_block += "\n作業ツリーへの書込: あり"
    task_document.write_text(f"# 担当\n\n## 入力\n\n```text\n{input_block}\n```\n\n## 出力\n\n結果を返す。\n", encoding="utf-8")
    return task_document


def _observed_input_lines(task_name: str, root: pathlib.Path, *, rereview: bool = False) -> list[str]:
    """実運用で観測した委譲プロンプトの名前付き入力を組み立てる。"""
    handoff = f"引き継ぎ記録先: {root / 'handoff.md'}"
    if task_name == "exec-review.subagent.md":
        return [
            "レビュー基準: 計画",
            f"計画: {root / 'plan.md'}",
            handoff,
        ]
    if task_name == "exec.subagent.md":
        return [
            "担当種別: レーン担当",
            '書込担当一覧: ["root-session"]',
            f"選定結果の出力先ファイル: {root / 'selection.json'}",
            "レーン識別子: lane-01",
            handoff,
        ]
    if task_name == "repository-process.subagent.md":
        return [
            "target_repo: github.com/example/repository",
            f"worktree: {root}",
            f"candidate_file: {root / 'candidates.jsonl'}",
            'readiness: {"status": "ready"}',
            "lane_limit: 2",
            f"ownership_record: {root / 'ownership.json'}",
            "ownership_token: token",
            "lane_session_id: lane-session",
            handoff,
        ]
    if task_name == "lane-integration.subagent.md":
        return [
            "統合区分: マージあり",
            "レーン開始時刻: 2026-10-08T00:38:16Z",
            "実行レビュー済みHEAD: 0123abc",
            f"統合先worktree: {root}",
            "統合先branch: develop",
            '計画ファイル名一覧: ["plan.md"]',
            "AWI終了区分: 20260101-000000-001.md=adopt",
            '書込担当一覧: ["root-session", "lane-session"]',
        ]
    if task_name == "session-termination.subagent.md":
        return [
            "bump種別: bump不要",
            handoff,
        ]
    if task_name == "add-wi.subagent.md":
        return ["投入する要求: request-1=/repo=awi=検出条件の追加", handoff]
    if task_name == "external-write-review.subagent.md":
        for name in ("pr-body.md", "diff.patch"):
            (root / name).write_text("投稿の比較資料\n", encoding="utf-8")
        return [
            f"文面ファイル: {root / 'pr-body.md'}（12行）",
            "投稿先と目的: GitHubのPR本文。変更の目的をレビュアーへ伝える",
            f"根拠の所在: {root / 'diff.patch'}",
        ]
    if task_name == "copilot-review-audit.subagent.md":
        return [f"pending取得結果: {root / 'pending.json'}", handoff]
    if task_name == "reader-fit-review.subagent.md":
        for name in ("guide.md", "before.md", "diff.patch"):
            (root / name).write_text("読者別探索の比較資料\n", encoding="utf-8")
        lines = [
            f"成果物: {root / 'guide.md'}（40行）",
            "種別: エンドユーザー向け文書",
            "読者像: ツールを初めて導入するエンドユーザー。内部の実装は知らない",
            f"修正範囲: `{root / 'before.md'}`と`{root / 'diff.patch'}`。直接影響は再読込節",
        ]
        if rereview:
            lines.extend(
                [
                    "レビュー種別: 再レビュー",
                    "未解決事項: なし",
                ]
            )
        return lines
    if task_name == "bulk-replace-review.subagent.md":
        (root / "word-diff.txt").write_text("[-旧-]{+新+}\n", encoding="utf-8")
        return [f"差分ファイル: {root / 'word-diff.txt'}（120行）"]
    if task_name == "defect-investigation.subagent.md":
        return ["対象の不良: 委譲プロンプトの必須入力検査が見出しを誤認する（agents_server_mcp.py）", handoff]
    raise ValueError(f"未対応の`<役割名>.subagent.md`: {task_name}")


def _observed_input_params(task_name: str, root: pathlib.Path) -> dict[str, str]:
    """実運用の入力行を専用startの名前付きパラメータへ変換する。"""
    return dict(line.split(": ", 1) for line in _observed_input_lines(task_name, root))


@contextlib.asynccontextmanager
async def _start_late_unavailability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    candidates: list[tuple[str, str, str]],
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, dict[str, Any]]]:
    """起動応答の返却後に最初の候補を失敗させ、結果の回収前で返す。"""
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    async with server_manager.AgentsServerManager() as manager:
        failure_release = asyncio.Event()
        delayed = DelayedUnavailableBackend(manager.sessions, "codex", manager._condition, failure_release=failure_release)
        _install_backend(manager, "codex", delayed)

        started = await manager.start("plan", "調査", str(tmp_path))
        assert started["status"] == "running"
        failure_release.set()
        await asyncio.gather(*delayed.pending)
        yield manager, started


@contextlib.asynccontextmanager
async def _carry_over_late_unavailability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    candidates: list[tuple[str, str, str]],
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, FakeBackend]]:
    """開始確認の上限後に候補が失敗した状態まで進め、利用できるbackendへ差し替える。"""
    async with _start_late_unavailability(monkeypatch, tmp_path, candidates) as (manager, _):
        assert (await manager.wait())["status"] == "failed"
        available = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", available)
        yield manager, available


def _codex_model_rejected_error(param: str = "model") -> dict[str, Any]:
    """接続先がCodex候補の引数を拒否した失敗（2026-09-30に観測した`TurnError`の形）を返す。"""
    body = {
        "error": {
            "message": "gpt-6.1-sol は利用できません。使用可能なモデル: gpt-5.6-sol, gpt-6-sol",
            "type": "aichat_error",
            "code": "invalid_parameter_value",
            "param": param,
        }
    }
    return {
        "message": json.dumps(body, ensure_ascii=False),
        "codexErrorInfo": "other",
        "additionalDetails": None,
        "misalignment": None,
    }


_LAUNCH_VALUES: dict[str, Any] = {
    "label": "lane-05-review",
    "prompt": "起動時の依頼本文",
    "created_at": "2026-09-25T21:58:13+00:00",
}


def _recording_candidates(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """候補列の解決へ渡った`model_type`を記録し、常に同じ候補を返す。"""
    requested: list[str] = []

    def fake_parse(model_type: str, **_kwargs: str | None) -> list[tuple[str, str, str]]:
        requested.append(model_type)
        return [("codex", "model", "high")]

    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", fake_parse)
    return requested


_MODE_INPUT_CASES = [
    pytest.param("task", {}, "subagent_md_path", id="task-missing-document"),
    pytest.param("task", {"subagent_md_path": "/abs/x.subagent.md", "prompt": "本文"}, "prompt", id="task-mixed-prompt"),
    pytest.param("delegate", {"prompt": "本文"}, "model_type", id="delegate-missing-model-type"),
    pytest.param(
        "delegate",
        {"prompt": "本文", "model_type": "execute", "extra_params": {}},
        "extra_params",
        id="delegate-mixed-extra-params",
    ),
    pytest.param("explore", {}, "prompt", id="explore-missing-prompt"),
    pytest.param("write", {"prompt": "本文", "command": "ls"}, "command", id="write-mixed-command"),
    pytest.param("shell", {"command": "make test"}, "summary_policy", id="shell-missing-policy"),
    pytest.param(
        "shell",
        {"command": "make test", "summary_policy": "終了状態", "prompt": "本文"},
        "prompt",
        id="shell-mixed-prompt",
    ),
    pytest.param(
        "explore",
        {"prompt": "本文", "subagent_md_path": "/abs/x.subagent.md"},
        "subagent_md_path",
        id="explore-mixed-document",
    ),
]


def _write_failing_uv(bin_dir: pathlib.Path) -> None:
    """未trustのmise shimと同じく、標準エラーへ理由を書いて終了コード1で終わる`uv`・`uvx`を置く。"""
    bin_dir.mkdir()
    for name in ("uv", "uvx"):
        path = bin_dir / name
        path.write_text("#!/bin/sh\necho 'mise ERROR Config files in ./mise.toml are not trusted.' >&2\nexit 1\n")
        path.chmod(0o755)
