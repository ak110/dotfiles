"""公開生成入口とサーバーで同じ役割入力・継続入力を使えることを検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib

import pytest

from agent_toolkit._agents_server import launch_requests
from agent_toolkit._atk import run_script


def _generate(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], request: dict) -> tuple[int, dict]:
    input_path = tmp_path / "委譲 要求.json"
    input_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    result = run_script.dispatch(argparse.Namespace(script_name="delegation-prompt", script_args=["--input", str(input_path)]))
    captured = capsys.readouterr()
    return result, json.loads(captured.out if result == 0 else captured.err)


def test_start_and_builtin_prompt_share_input_contract(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """初回生成は必須値と役割参照を保ち、サーバーと同じ欠落・宣言外を拒否する。"""
    params = {
        "担当種別": "レーン担当",
        "引き継ぎ記録先": str(tmp_path / "引継ぎ 記録.md"),
        "再開位置": r"C:\資料 フォルダー\計画.md",
        "固有指示": "最初の要求\n\n追加の条件",
    }
    result, generated = _generate(tmp_path, capsys, {"kind": "initial", "subagent_md_path": "exec", "extra_params": params})
    assert result == 0
    assert generated["kind"] == "initial"
    document = pathlib.Path(__file__).resolve().parents[3] / "share" / "exec.subagent.md"
    assert generated["prompt"].splitlines()[0] == f"{document}の手順を実行せよ。"
    assert launch_requests.parse_named_inputs(generated["prompt"].splitlines()[1:]) == params
    assert "固有指示:\n  最初の要求\n  \n  追加の条件" in generated["prompt"]
    # サーバー側の生成は全体作業契約の解決を前提とするため、実行環境の会話へ依存しないよう明示する。
    whole = tmp_path / "work-contract.md"
    whole.write_text("目的: 入力契約の共有を確かめる\n", encoding="utf-8")
    _, _, server_prompt, _, _ = launch_requests.task_document_request("exec", params, whole_contract_path=str(whole))
    assert launch_requests.parse_named_inputs(server_prompt.rsplit("\n入力:\n", 1)[1].splitlines()) == params
    for invalid in ({"引き継ぎ記録先": params["引き継ぎ記録先"]}, {**params, "余分な説明": "説明"}):
        result, failure = _generate(tmp_path, capsys, {"kind": "initial", "subagent_md_path": "exec", "extra_params": invalid})
        assert result == 1
        assert failure["error"]
        with pytest.raises(ValueError):
            launch_requests.task_document_request("exec", invalid, whole_contract_path=str(whole))


def test_continuation_uses_existing_launch_record(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """既存の起動記録JSONを参照し、必須のroundだけで継続本文を生成する。"""
    document = pathlib.Path(__file__).resolve().parents[3] / "share" / "exec-review.subagent.md"
    history_path = tmp_path / "既存記録.json"
    history_path.write_text(
        json.dumps({"prompt": f"{document}の手順を実行せよ。", "followup_prompts": ["再レビューせよ\nround: 2"]}),
        encoding="utf-8",
    )
    result, generated = _generate(
        tmp_path,
        capsys,
        {
            "kind": "continuation",
            "history_file": str(history_path),
            "instruction": "再レビューせよ",
            "extra_params": {"round": "3"},
        },
    )
    assert result == 0
    assert generated == {"kind": "continuation", "prompt": "再レビューせよ\nround: 3"}
    result, failure = _generate(
        tmp_path,
        capsys,
        {
            "kind": "continuation",
            "history_file": str(history_path),
            "prompt": "再レビューせよ\nround: 4\n検証結果: 成功",
        },
    )
    assert result == 1
    assert "検証結果" in failure["error"]
