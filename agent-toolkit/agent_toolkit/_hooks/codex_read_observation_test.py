"""公開Post hookを通して、Codexのnative読取の成功だけを読了として観測し、本文を配送しないことを確かめる。"""

import json
import pathlib
import shlex

import pytest

from agent_toolkit._common import delegated_session, session_state
from agent_toolkit._hooks import codex_read_observation as observation
from agent_toolkit._hooks import plugin_resources, posttooluse, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery


def _payload(tmp_path, command, body, *, exit_code=0, aggregated=None):
    records = [
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": "turn",
                "thread_id": "session",
                "item": {
                    "type": "CommandExecution",
                    "id": "exec-id",
                    "status": "completed" if not exit_code else "failed",
                    "exit_code": exit_code,
                    "cwd": tmp_path.as_uri(),
                    "command": ["/bin/bash", "-lc", command],
                    **({"aggregated_output": aggregated} if aggregated is not None else {}),
                },
            },
        },
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return {
        "session_id": "session",
        "turn_id": "turn",
        "tool_use_id": "exec-id",
        "hook_event_name": "PostToolUseFailure" if exit_code else "PostToolUse",
        "cwd": str(tmp_path),
        "transcript_path": str(transcript),
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": body,
    }


def _cut(body, tokens=100):
    return (
        f"Warning: truncated output (original token count: {tokens})\nTotal output lines: 20\n\n"
        f"{body[:12]}…90 tokens truncated…{body[-12:]}"
    )


def _additional_context(capsys) -> str:
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"].get("additionalContext", "") if out.strip() else ""


@pytest.mark.parametrize("kind", ["cat", "sed", "head", "tail", "jq", "chain"])
@pytest.mark.parametrize("truncated", [False, True])
def test_public_post_hook_never_delivers_document_body(tmp_path, capsys, kind, truncated):
    """成功した標準読取でも、省略の有無にかかわらず公開Post hookは資料本文を追加コンテキストへ載せない。

    2026年10月30日に省略補償として再導入された本文配送の再発を、公開経路の出力で検出する。
    """
    text = "".join(f"資料{i:03d}: 配送してはならない本文\n" for i in range(40))
    path = tmp_path / "資料 space.md"
    path.write_text(text, encoding="utf-8")
    operand = shlex.quote(str(path))
    command, expected = f"cat -- {operand}", text
    if kind == "sed":
        command, expected = f"sed -n '2,$p' -- {operand}", "".join(text.splitlines(keepends=True)[1:])
    elif kind == "head":
        command, expected = f"head -n 3 -- {operand}", "".join(text.splitlines(keepends=True)[:3])
    elif kind == "tail":
        command, expected = f"tail -n3 -- {operand}", "".join(text.splitlines(keepends=True)[-3:])
    elif kind == "jq":
        path.write_text(json.dumps({"body": text}), encoding="utf-8")
        command, expected = f"jq -r '.body' {operand}", text + "\n"
    elif kind == "chain":
        command, expected = f"cat -- {operand}; cat -- {operand}", text * 2
    payload = _payload(tmp_path, command, _cut(expected) if truncated else expected)
    assert posttooluse.main(json.dumps(payload)) == 0
    output = _additional_context(capsys)
    for line in expected.splitlines()[:3]:
        assert line not in output
    if truncated:
        assert observation.TRUNCATED_NOTICE in output
    else:
        assert output == ""


def test_successful_read_is_observed_and_truncation_gets_recovery(tmp_path):
    """成功した読取を観測し、省略時は元トークン数と回復の案内も返す。"""
    path = tmp_path / "note"
    path.write_text("元の全量\n" * 30, encoding="utf-8")
    full = observation.collect(_payload(tmp_path, f"cat {path}", path.read_text()))
    assert full == observation.Observation("", path.read_text())
    cut = observation.collect(_payload(tmp_path, f"cat {path}", _cut(path.read_text())))
    assert cut is not None
    assert cut.body == _cut(path.read_text())
    assert observation.TRUNCATED_NOTICE in cut.context
    assert 'source="codex-read-observation" kind="notice"' in cut.context


@pytest.mark.parametrize(
    "command",
    [
        "cat file > out",
        "cat file | head",
        "cat $(echo file)",
        "tail -f file",
        "sed -n '1w out' file",
        "jq env file",
        "jq 'input' file",
        "jq '.body' file; touch out",
    ],
)
def test_side_effects_and_external_jq_inputs_are_not_read_plans(command):
    assert not observation.read_plan(command)


@pytest.mark.parametrize("value", [None, [], {"payload": None}, {"type": "event_msg", "payload": {"item": None}}])
def test_malformed_transcript_is_not_observed(tmp_path, value):
    """保存用の記録が壊れていても成功イベントの読取を観測する。"""
    path = tmp_path / "note"
    path.write_text("本文")
    payload = _payload(tmp_path, f"cat {path}", "本文")
    pathlib.Path(payload["transcript_path"]).write_text(json.dumps(value) + "\n")
    assert observation.collect(payload) == observation.Observation("", "本文")


@pytest.mark.parametrize("exit_code", [0, 1, None])
@pytest.mark.parametrize("truncated", [False, True])
def test_document_read_state_follows_successful_full_native_read(tmp_path, capsys, exit_code, truncated):
    """PostToolUseの成功と失敗を分け、省略や保存記録の欠落で誤遮断しない。"""
    path = plugin_resources.plugin_root().parent / "agent-basics/skills/bugfix/references/root-cause-analysis.md"
    body = path.read_text(encoding="utf-8")
    payload = _payload(tmp_path, f"cat -- {path}", _cut(body) if truncated else body, exit_code=exit_code)
    if exit_code is None:
        pathlib.Path(payload["transcript_path"]).write_text("", encoding="utf-8")
    assert posttooluse.main(json.dumps(payload)) == 0
    capsys.readouterr()
    recorded = session_state.read_state("session").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    target = read_delivery.read_triggers.ReadTarget(path)
    assert read_delivery.target_ready(recorded, target, "main") == (exit_code != 1)


@pytest.mark.parametrize("launch_kind", ["", "explore", "shell"])
@pytest.mark.parametrize("command", ["rg -n 対象 -- .", "git diff"])
def test_truncated_non_read_output_gets_recovery_notice(tmp_path, monkeypatch, capsys, command, launch_kind):
    """標準読取以外の出力が省略された場合も、通常起動と軽量起動の委譲先へ担当内で回収する次の操作を示す。

    回収可能な省略を理由に続行不能を返した実例には、軽量起動の探索担当（2026年10月29日と31日）が含まれる。
    """
    if launch_kind:
        monkeypatch.setenv(delegated_session.LAUNCH_KIND_ENV, launch_kind)
    payload = _payload(tmp_path, command, _cut("検索結果の本文" * 40))
    assert posttooluse.main(json.dumps(payload)) == 0
    output = _additional_context(capsys)
    assert observation.TRUNCATED_OUTPUT_NOTICE in output
    assert "続行できない理由にしない" in output


def test_untruncated_non_read_output_has_no_notice(tmp_path, capsys):
    payload = _payload(tmp_path, "git status --short", " M file\n")
    assert posttooluse.main(json.dumps(payload)) == 0
    assert observation.TRUNCATED_OUTPUT_NOTICE not in _additional_context(capsys)


def test_linear_read_groups_do_not_guess_short_circuit_execution():
    assert observation.read_command_groups("cat first; git status; sed -n '1p' second", sequential=True)
    assert observation.read_command_groups("false && cat first; sed -n '1p' second", sequential=True) is None
    assert observation.read_command_groups("cat first && sed -n '1p' second")


def _persisted(body: str) -> str:
    """Codexが記録へ保存するときだけ入れる省略行を持つ、保存用の写しを返す。"""
    marker = "\n... command output truncated for persistence ...\n"
    raw = body.encode("utf-8")
    if len(raw) <= 65536:
        return body
    available = 65536 - len(marker.encode("utf-8"))
    head = raw[: available // 2].decode("utf-8", errors="ignore")
    tail = raw[-(available - available // 2) :].decode("utf-8", errors="ignore")
    padding = 65536 - len((head + marker + tail).encode("utf-8"))
    result = head + marker + "\n" * padding + tail
    assert len(result.encode("utf-8")) == 65536
    return result


@pytest.mark.parametrize("form", ["multiple", "mixed", "diagnostic", "single", "split"])
def test_full_return_with_persisted_copy_only_truncated_releases_write_block(tmp_path, capsys, monkeypatch, form):
    """65536バイトを超える応答の保存用写しが省略されても、成功した読取で書込遮断を解除する。

    複数資料の静的なcat、単一の長い資料、末尾まで分けた行範囲の取得を比べる。
    `diagnostic`は、先頭の読取成功を後続の`git status`などの診断によって失わないことを確かめる。
    """
    paths = [tmp_path / "first.md", tmp_path / "second.md"]
    for index, path in enumerate(paths):
        path.write_text("".join(f"資料{index}-{line:03d}: 本文" + "文" * 110 + "\n" for line in range(200)), encoding="utf-8")
        assert path.stat().st_size > 65536
    rows = [read_delivery.read_triggers.ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("tool:Write",), tuple(paths))]
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    monkeypatch.setattr(read_delivery.read_triggers, "reference_method_targets", lambda *_args, **_kwargs: ())
    operation = {"session_id": "session", "turn_id": "turn", "cwd": str(tmp_path)}
    write = {"file_path": str(tmp_path / "out.md"), "content": "本文"}
    first = read_delivery.deliver(operation, "Write", write, "session")
    read_delivery.notice_module.record_delivery("\n".join(first.notices))
    assert read_delivery.deliver(operation, "Write", write, "session").block is not None
    if form == "multiple":
        reads = [(f"cat -- {paths[0]} {paths[1]}", paths[0].read_text() + paths[1].read_text())]
    elif form == "mixed":
        reads = [(f"cat -- {paths[0]} && sed -n '1,$p' -- {paths[1]}", paths[0].read_text() + paths[1].read_text())]
    elif form == "diagnostic":
        command = f"cat -- {paths[0]} {paths[1]}; git status --short; sed -n '1,2p' -- {paths[0]}"
        head = "".join(paths[0].read_text().splitlines(keepends=True)[:2])
        reads = [(command, paths[0].read_text() + paths[1].read_text() + " M out.md\n" + head)]
    else:
        reads = []
        for path in paths:
            if form == "single":
                reads.append((f"cat -- {path}", path.read_text()))
            else:
                lines = path.read_text().splitlines(keepends=True)
                reads.append((f"sed -n '1,100p' -- {path}", "".join(lines[:100])))
                reads.append((f"sed -n '101,$p' -- {path}", "".join(lines[100:])))
    for command, body in reads:
        payload = _payload(tmp_path, command, body, aggregated=_persisted(body))
        assert posttooluse.main(json.dumps(payload)) == 0
    capsys.readouterr()
    assert read_delivery.deliver(operation, "Write", write, "session").block is None


def test_persisted_copy_delivered_as_the_response_is_not_a_full_read(tmp_path):
    """保存用の写しとの比較を使わず、成功イベントの本文を観測へ渡す。"""
    path = tmp_path / "note.md"
    path.write_text("本文の行\n" * 300, encoding="utf-8")
    persisted = _persisted(path.read_text())
    payload = _payload(tmp_path, f"cat -- {path}", persisted, aggregated=persisted)
    collected = observation.collect(payload)
    assert collected == observation.Observation("", persisted)
