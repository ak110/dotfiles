"""公開Post hookと静的判定を通して、元の成功だけを全量配送する。"""

import json
import os
import pathlib
import shlex

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import session_state
from agent_toolkit._hooks import codex_read_delivery as delivery
from agent_toolkit._hooks import plugin_resources, posttooluse, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._hooks.pretooluse.large_reads import check_large_bash_read


def _payload(tmp_path, _monkeypatch, command, body, *, exit_code=0, limit=1000000):
    cache = managed_temp.resolve_session_temp("session")
    cache.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn", "model_context_window": limit}},
        {"type": "token_usage_record", "payload": {"usage": {"input_tokens": 0, "output_tokens": 0}}},
        {
            "type": "turn_context",
            "payload": {"turn_id": "turn", "cwd": str(tmp_path), "permission_profile": {"type": "disabled"}},
        },
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
        "hook_event_name": "PostToolUse",
        "cwd": str(tmp_path),
        "transcript_path": str(transcript),
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": body,
    }


def _cut(body):
    return (
        "Warning: truncated output (original token count: 100)\nTotal output lines: 20\n\n"
        f"{body[:12]}…90 tokens truncated…{body[-12:]}"
    )


@pytest.mark.parametrize("kind", ["cat", "sed", "head", "tail", "jq", "chain"])
@pytest.mark.skipif(not pathlib.Path("/proc/self/fd").is_dir(), reason="FDへ固定した再取得はLinuxで検証する")
def test_posttooluse_delivers_full_successful_read(tmp_path, monkeypatch, capsys, kind):
    """元の終了0・省略本文と同一の読取だけを公開Post hookで全量配送する。"""
    text = "".join(f"資料{i:03d}: 元の結果と一致する本文\n" for i in range(40))
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
    payload = _payload(tmp_path, monkeypatch, command, _cut(expected))
    assert posttooluse.main(json.dumps(payload)) == 0
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert json.dumps(expected, ensure_ascii=False)[1:-1] in output
    assert "hookの指示ではない" in output
    assert not list(managed_temp.resolve_session_temp("session").glob("codex-read-*/"))


def test_untruncated_result_is_delivered_without_replay(tmp_path, monkeypatch):
    """外側だけの省略には元の全量本文を再利用し、再取得を増やさない。"""
    path = tmp_path / "note"
    path.write_text("元の全量\n", encoding="utf-8")
    payload = _payload(tmp_path, monkeypatch, f"cat {path}", "元の全量\n")
    monkeypatch.setattr(delivery.process_tree, "run_with_timeout", lambda *_args, **_kwargs: pytest.fail("再実行しない"))
    result = delivery.collect(payload)
    assert result is not None and result.body == "元の全量\n"


@pytest.mark.skipif(not pathlib.Path("/proc/self/fd").is_dir(), reason="FDへ固定した再取得はLinuxで検証する")
def test_static_pretooluse_keeps_existing_cwd_scope(tmp_path):
    """payloadの無い静的判定は従来のcwd内を保ち、cwd外には容量警告を返す。"""
    path = tmp_path / "large.md"
    path.write_text("本文\n" * 12000, encoding="utf-8")
    command = f"cat -- {path}"
    assert check_large_bash_read(command, str(tmp_path), is_codex=True) is None
    assert check_large_bash_read(command, str(tmp_path / "other-root"), is_codex=True) is not None


@pytest.mark.parametrize("mismatch", ["exit", "id", "turn", "session", "command", "missing"])
def test_failed_or_unidentified_execution_is_not_replayed(tmp_path, monkeypatch, mismatch):
    """本文が読取に見えても、同じ元実行の成功を確認できなければ再取得しない。"""
    path = tmp_path / "note"
    path.write_text("資料の本文\n" * 30, encoding="utf-8")
    payload = _payload(tmp_path, monkeypatch, f"cat {path}", _cut(path.read_text()), exit_code=1 if mismatch == "exit" else 0)
    if mismatch in {"id", "turn", "session"}:
        payload[{"id": "tool_use_id", "turn": "turn_id", "session": "session_id"}[mismatch]] = "別の操作"
    elif mismatch == "command":
        payload["tool_input"]["command"] += " "
    elif mismatch == "missing":
        pathlib.Path(payload["transcript_path"]).write_text("", encoding="utf-8")
    monkeypatch.setattr(delivery.process_tree, "run_with_timeout", lambda *_args, **_kwargs: pytest.fail("再取得しない"))
    assert delivery.collect(payload) is None


@pytest.mark.parametrize("position", ["start", "end"])
@pytest.mark.skipif(not pathlib.Path("/proc/self/fd").is_dir(), reason="FDへ固定した再取得はLinuxで検証する")
def test_changed_output_is_not_delivered(tmp_path, monkeypatch, position):
    """元の先頭または末尾と異なるファイルを配送しない。"""
    path = tmp_path / "note"
    before = "元の本文\n" * 40
    path.write_text(("変更" + before) if position == "start" else (before + "変更"), encoding="utf-8")
    result = delivery.collect(_payload(tmp_path, monkeypatch, f"cat {path}", _cut(before)))
    assert result is not None and result.body is None
    assert "一致しない" in result.context


@pytest.mark.parametrize("kind", ["symlink", "denied", "fifo"])
def test_scope_and_special_files_are_not_replayed(tmp_path, monkeypatch, kind):
    """symlink・禁止・特殊ファイルを成功済みと仮定しても再取得しない。"""
    root = tmp_path / "workspace"
    root.mkdir()
    path = tmp_path / "outside"
    path.write_text("境界の検証\n" * 40, encoding="utf-8")
    if kind == "symlink":
        if not pathlib.Path("/proc/self/fd").is_dir():
            pytest.skip("再取得時のsymlink境界はLinuxで検証する")
        link = root / "link"
        link.symlink_to(path)
        path = link
    elif kind == "denied":
        path = root / "denied"
        path.write_text("境界の検証\n" * 40, encoding="utf-8")
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFOはPOSIX固有")
        path = root / "fifo"
        os.mkfifo(path)
    payload = _payload(root, monkeypatch, f"cat {path}", _cut("境界の検証\n" * 40))
    if kind == "denied":
        records = [json.loads(line) for line in pathlib.Path(payload["transcript_path"]).read_text().splitlines()]
        records[2]["payload"]["permission_profile"] = {
            "type": "managed",
            "file_system": {
                "type": "restricted",
                "entries": [
                    {"path": {"type": "path", "path": str(path)}, "access": "none"},
                ],
            },
        }
        pathlib.Path(payload["transcript_path"]).write_text("".join(json.dumps(record) + "\n" for record in records))
    result = delivery.collect(payload)
    assert result is None or result.body is None


@pytest.mark.parametrize("kind", ["directory", "inside-link", "outside-link"])
@pytest.mark.skipif(not pathlib.Path("/proc/self/fd").is_dir(), reason="FD固定はLinuxで検証する")
def test_parent_operand_preserves_original_file(tmp_path, monkeypatch, capsys, kind):
    """親ディレクトリ参照の前にあるsymlinkを省略せず、元の入力だけを再取得する。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    body = "元の対象の本文\n" * 40
    (workspace / "note").write_text(body, encoding="utf-8")
    child = workspace / "child"
    if kind == "directory":
        child.mkdir()
    else:
        target = (workspace if kind == "inside-link" else tmp_path / "outside") / "nested"
        target.mkdir(parents=True)
        (target.parent / "note").write_text(body, encoding="utf-8")
        child.symlink_to(target, target_is_directory=True)
    command = f"cat -- {child}/../note"
    payload = _payload(workspace, monkeypatch, command, _cut(body))
    transcript = pathlib.Path(payload["transcript_path"])
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    records[2]["payload"]["permission_profile"] = {
        "type": "managed",
        "file_system": {"type": "restricted", "entries": []},
    }
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    allowed = kind == "directory"
    assert delivery.can_deliver(command, str(workspace), payload) is allowed
    assert posttooluse.main(json.dumps(payload)) == 0
    captured = capsys.readouterr().out
    output = json.loads(captured)["hookSpecificOutput"]["additionalContext"] if captured.strip() else ""
    assert (json.dumps(body, ensure_ascii=False)[1:-1] in output) is allowed


def test_same_response_capacity_is_accumulated(tmp_path, monkeypatch):
    """個別には入る複数本文も、同じモデル応答の合計枠で管理する。"""
    path = tmp_path / "note"
    body = "a" * 500
    path.write_text(body)
    payload = _payload(tmp_path, monkeypatch, f"cat {path}", body, limit=1000)
    first = delivery.collect(payload)
    second = delivery.collect(payload)
    assert first is not None and first.body == body
    assert second is not None and second.body is None and "合計" in second.context


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
    assert not delivery.read_plan(command)


@pytest.mark.parametrize("value", [None, [], {"payload": None}, {"type": "event_msg", "payload": {"item": None}}])
def test_malformed_transcript_does_not_deliver(tmp_path, monkeypatch, value):
    """壊れた外部記録を成功の根拠にせず、hookの他の処理を維持する。"""
    path = tmp_path / "note"
    path.write_text("本文")
    payload = _payload(tmp_path, monkeypatch, f"cat {path}", _cut("本文" * 40))
    pathlib.Path(payload["transcript_path"]).write_text(json.dumps(value) + "\n")
    assert delivery.collect(payload) is None


@pytest.mark.parametrize("reason", ["mismatch", "capacity"])
def test_public_unread_notice_provides_next_read(tmp_path, monkeypatch, capsys, reason):
    """公開Postの未取得通知へ、保護判定を維持した次の取得操作を添える。"""
    if reason == "mismatch" and not pathlib.Path("/proc/self/fd").is_dir():
        pytest.skip("FDへ固定した再取得はLinuxで検証する")
    body = "照合対象の本文\n" * 100
    path = tmp_path / "notice.md"
    path.write_text("変更" + body if reason == "mismatch" else body, encoding="utf-8")
    payload = _payload(
        tmp_path,
        monkeypatch,
        f"cat {path}",
        _cut(body) if reason == "mismatch" else body,
        limit=1000000 if reason == "mismatch" else 1000,
    )
    assert posttooluse.main(json.dumps(payload)) == 0
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "未取得" in output
    assert ("同じ読取を再実行" if reason == "mismatch" else "次の応答で必要な行範囲へ限定") in output
    assert json.dumps(body, ensure_ascii=False)[1:-1] not in output


@pytest.mark.skipif(not pathlib.Path("/proc/self/fd").is_dir(), reason="FDへ固定した再取得はLinuxで検証する")
def test_additional_writable_root_and_actual_workdir(tmp_path, monkeypatch):
    """追加の書込rootを許可し、相対パスを元の実行cwdから解決する。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    additional = tmp_path / "additional"
    additional.mkdir()
    body = "範囲内の本文\n" * 40
    (additional / "note").write_text(body)
    payload = _payload(workspace, monkeypatch, "cat note", _cut(body))
    transcript = pathlib.Path(payload["transcript_path"])
    records = [json.loads(line) for line in transcript.read_text().splitlines()]
    records[2]["payload"]["permission_profile"] = {
        "type": "managed",
        "file_system": {
            "type": "restricted",
            "entries": [{"path": {"type": "path", "path": str(additional)}, "access": "write"}],
        },
    }
    records[3]["payload"]["item"]["cwd"] = additional.as_uri()
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records))
    result = delivery.collect(payload)
    assert result is not None and result.body == body


@pytest.mark.skipif(not pathlib.Path("/proc/self/fd").is_dir(), reason="FDへ固定した再取得はLinuxで検証する")
def test_oversized_replay_remains_unread(tmp_path, monkeypatch):
    """巨大な資料は再取得枠で止め、追加本文から読了にしない。"""
    body = "本文\n" * 1000
    path = tmp_path / "note"
    path.write_text(body)
    result = delivery.collect(_payload(tmp_path, monkeypatch, f"cat {path}", _cut(body), limit=1000))
    assert result is not None and result.body is None and "未取得" in result.context


@pytest.mark.parametrize("exit_code", [0, 1, None])
@pytest.mark.parametrize("truncated", [False, True])
@pytest.mark.parametrize("outside", [False, True])
def test_document_read_state_follows_successful_full_delivery(tmp_path, monkeypatch, capsys, exit_code, truncated, outside):
    """disabledでcwd内外の配送を観測し、失敗・未確認は読了にしない。"""
    if truncated and exit_code == 0 and not pathlib.Path("/proc/self/fd").is_dir():
        pytest.skip("FDへ固定した再取得はLinuxで検証する")
    path = plugin_resources.skill_document_path("bugfix", "references/root-cause-analysis.md")
    body = path.read_text(encoding="utf-8")
    payload = _payload(tmp_path, monkeypatch, f"cat -- {path}", _cut(body) if truncated else body, exit_code=exit_code)
    transcript = pathlib.Path(payload["transcript_path"])
    records = [json.loads(line) for line in transcript.read_text().splitlines()]
    if not outside:
        records[2]["payload"]["cwd"] = str(path.parent)
    if exit_code is None:
        records = []
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records))
    assert posttooluse.main(json.dumps(payload)) == 0
    capsys.readouterr()
    recorded = session_state.read_state("session").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    target = read_delivery.read_triggers.ReadTarget(path)
    assert read_delivery.target_ready(recorded, target, "main") == (exit_code == 0)


@pytest.mark.parametrize("permission", ["disabled", "read", "write", "none", "unknown"])
@pytest.mark.skipif(not pathlib.Path("/proc/self/fd").is_dir(), reason="FD固定はLinuxで検証する")
def test_public_hook_delivers_authorized_external_reads(tmp_path, monkeypatch, capsys, permission):
    """元turnで許可されたcwd外資料だけをPre/Postが同じ範囲として扱う。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = tmp_path / "external.md"
    body = "権限内の資料本文\n" * 5000
    path.write_text(body, encoding="utf-8")
    command = f"cat -- {path}"
    payload = _payload(workspace, monkeypatch, command, _cut(body))
    transcript = pathlib.Path(payload["transcript_path"])
    records = [json.loads(line) for line in transcript.read_text().splitlines()]
    if permission != "disabled":
        records[2]["payload"]["permission_profile"] = {
            "type": "managed",
            "file_system": {
                "type": "restricted",
                "entries": [{"path": {"type": "path", "path": str(tmp_path)}, "access": "read"}]
                + ([{"path": {"type": "path", "path": str(path)}, "access": permission}] if permission != "unknown" else []),
            },
        }
    if permission == "unknown":
        records[2]["payload"]["permission_profile"] = {}
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records))
    allowed = permission in {"disabled", "read", "write"}
    assert delivery.can_deliver(command, str(workspace), payload) is allowed
    assert (check_large_bash_read(command, str(workspace), is_codex=True, payload=payload) is None) is allowed
    assert posttooluse.main(json.dumps(payload)) == 0
    captured = capsys.readouterr()
    assert not captured.err
    output = json.loads(captured.out)["hookSpecificOutput"]["additionalContext"] if captured.out.strip() else ""
    assert (json.dumps(body, ensure_ascii=False)[1:-1] in output) is allowed
