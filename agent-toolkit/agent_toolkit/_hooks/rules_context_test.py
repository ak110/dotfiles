"""実行主体別の規範追加handlerの契約テスト。"""

from __future__ import annotations

import datetime
import json
import pathlib
import re

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.wi import process_loop_log
from agent_toolkit._hooks import rules_context, rules_context_codex

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _output(capsys: pytest.CaptureFixture[str]) -> str:
    return json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact", "fork"])
def test_session_start_main_claude_includes_main_and_claude_rules(
    source: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": source}))
    output = _output(capsys)
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip() not in output
    assert (rules_context.QUALITY_CHECKPOINT_NOTICE in output) is (source == "compact")
    assert rules_context.ASK_USER_QUESTION_CHECKLIST in output
    assert rules_context.RESPONSE_LANGUAGE_NOTICE in output
    normative_start = (
        f'<{rules_context.NORMATIVE_ELEMENT} source="{rules_context.NORMATIVE_SOURCE}" '
        f'kind="{rules_context.NORMATIVE_KIND_MAIN}">'
    )
    assert output.count(normative_start) == 1
    assert output.endswith(f"</{rules_context.NORMATIVE_ELEMENT}>")
    normative_body = output.split(normative_start, maxsplit=1)[1]
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() in normative_body
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() in normative_body


@pytest.mark.parametrize(
    ("name", "value"),
    [("AGENT_TOOLKIT_DELEGATED_SESSION", "1"), ("AGENT_TOOLKIT_OWNER_SESSION", "owner")],
)
def test_session_start_delegated_omits_main_rules(
    name: str, value: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv(name, value)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))
    assert capsys.readouterr().out == ""


def test_session_start_compact_prepends_quality_notice(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact"}))
    output = _output(capsys)
    assert output.index(rules_context.QUALITY_CHECKPOINT_NOTICE) < output.index(
        rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip()
    )

    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact"}))
    output = _output(capsys)
    assert rules_context.QUALITY_CHECKPOINT_NOTICE in output
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() not in output
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() not in output


def test_session_start_main_places_response_language_notice_first(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """使用言語の1文を、同じ本文の他の条文より前へ置く。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact"}))
    output = _output(capsys)
    notice_index = output.index(rules_context.RESPONSE_LANGUAGE_NOTICE)

    assert notice_index < output.index(rules_context.QUALITY_CHECKPOINT_NOTICE)
    assert notice_index < output.index(rules_context.ASK_USER_QUESTION_CHECKLIST)
    assert notice_index < output.index(
        f'<{rules_context.NORMATIVE_ELEMENT} source="{rules_context.NORMATIVE_SOURCE}" '
        f'kind="{rules_context.NORMATIVE_KIND_MAIN}">'
    )


def test_response_language_notice_absent_for_delegates(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """委譲先とサブエージェントの本文へ使用言語の1文を渡さない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact"}))
    assert rules_context.RESPONSE_LANGUAGE_NOTICE not in _output(capsys)

    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "agent_type": "general-purpose"}))
    assert rules_context.RESPONSE_LANGUAGE_NOTICE not in _output(capsys)


def test_session_start_codex_excludes_claude_code_rules(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    rules_context_codex.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))
    output = _output(capsys)
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() not in output


@pytest.mark.parametrize("agent_type", ["Explore", "Plan", "general-purpose", "plan-reviewer"])
@pytest.mark.parametrize("environment", [None, "delegated", "owner"])
def test_subagent_start_claude_includes_common_and_claude_subagent_rules(
    agent_type: str, environment: str | None, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if environment == "delegated":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    elif environment == "owner":
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "owner")
    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "agent_type": agent_type}))
    output = _output(capsys)
    assert rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.SUBAGENT_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() not in output


def test_subagent_start_codex_excludes_claude_code_rules(capsys: pytest.CaptureFixture[str]) -> None:
    """CodexのSubagentStartへClaude固有規範を追加しない。"""
    rules_context_codex.main(json.dumps({"hook_event_name": "SubagentStart", "agent_type": "explorer"}))
    output = _output(capsys)
    assert rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.SUBAGENT_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() not in output


def test_hooks_json_registers_rules_context_without_matcher() -> None:
    hooks = json.loads((_PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    for event in ("SessionStart", "SubagentStart"):
        assert "matcher" not in hooks[event][0]
        assert hooks[event][0]["hooks"][0]["command"].endswith("hook.py rules_context")


def _session_start_length_report(output: str) -> str:
    """SessionStartの追加本文が上限を超えた場合の失敗本文を組み立てる。

    超過分と文書別の長さを示し、追随すべき対象を失敗本文から確定できるようにする。
    """
    total = len(output)
    overage = total - rules_context.CLAUDE_CODE_OUTPUT_LIMIT
    breakdown = "、".join(
        f"{path.name}={len(path.read_text(encoding='utf-8'))}文字"
        for path in (rules_context.MAIN_RULES_PATH, rules_context.MAIN_RULES_CLAUDE_CODE_PATH)
    )
    return (
        f"SessionStartの追加本文が{total}文字であり、上限{rules_context.CLAUDE_CODE_OUTPUT_LIMIT}文字を"
        f"{overage}文字超過した。文書別の長さ: {breakdown}"
    )


def test_session_start_context_fits_claude_code_cap(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(managed_temp, "_state_root_path", lambda: tmp_path / "external-state")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "session-1"}))
    output = _output(capsys)
    assert len(output) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT, _session_start_length_report(output)


def test_subagent_start_context_fits_claude_code_cap(capsys: pytest.CaptureFixture[str]) -> None:
    """Claude固有規範を含むSubagentStart本文がhook上限へ収まる。"""
    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "agent_type": "general-purpose"}))
    assert len(_output(capsys)) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT


def test_session_start_length_report_shows_overage_and_breakdown() -> None:
    fake_output = "x" * (rules_context.CLAUDE_CODE_OUTPUT_LIMIT + 5)
    report = _session_start_length_report(fake_output)
    assert "5文字超過した" in report
    assert "文書別の長さ" in report
    assert f"{rules_context.MAIN_RULES_PATH.name}=" in report
    assert f"{rules_context.MAIN_RULES_CLAUDE_CODE_PATH.name}=" in report


def test_share_task_documents_have_no_bare_return_line_examples() -> None:
    """`share/*.md`の返却形式の書式例が、フェンス外の裸のラベル行として置かれていないことを検査する。

    条件付き出力の書式例をフェンスの外へ置くと、常時出力する行と誤読される。
    母集団は`rules_context.SHARE_DIR`直下の`*.md`全体とし、フェンスの内外を判別したうえで走査する。
    """
    bare_label_line = re.compile(r"^[^\s#\-*>|`][^\n:`]*: \S.*$")
    offending: list[str] = []
    for path in sorted(rules_context.SHARE_DIR.glob("*.md")):
        in_fence = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if bare_label_line.match(line):
                offending.append(f"{path.name}:{lineno}: {line}")
    assert not offending


def test_subagent_start_does_not_create_session_scoped_managed_temp(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SubagentStartはsession_idを受け取っても領域を確保しない。"""
    monkeypatch.setattr(
        rules_context.managed_temp,
        "create_managed_temp",
        lambda *_args, **_kwargs: pytest.fail("SubagentStartで領域を作成した"),
    )

    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "session_id": "session-1"}))

    assert rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip() in _output(capsys)


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact", "fork"])
def test_session_start_provides_one_session_scoped_managed_temp(
    source: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """同じsession_idの全SessionStartで1件の領域と同じ絶対パスを渡す。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(managed_temp, "_state_root_path", lambda: tmp_path / "external-state")
    payload = json.dumps({"hook_event_name": "SessionStart", "source": source, "session_id": "session-1"})

    rules_context.main(payload)
    first_output = _output(capsys)
    rules_context.main(payload)
    second_output = _output(capsys)
    entries = managed_temp.list_managed_temp(session_id="session-1")

    assert len(entries) == 1
    assert entries[0]["path"] in first_output
    assert entries[0]["path"] in second_output
    assert managed_temp.sweep_expired_managed_temp(now=datetime.datetime.now(datetime.UTC)) == []
    session_root = pathlib.Path(entries[0]["path"])
    assert session_root.exists()
    child = managed_temp.create_session_temp("child", session_root)
    assert child.parent == session_root


def test_rules_files_have_no_role_specific_sentences() -> None:
    pattern = re.compile(r"^(?:- |\d+\. )?(?:委譲先|サブエージェント|メインエージェント)は")
    actual = {
        line
        for path in (_PLUGIN_ROOT / "rules").glob("*.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if pattern.match(line)
    }
    assert not actual


def test_rules_files_have_no_main_only_capabilities() -> None:
    prohibited = (
        "`AskUserQuestion`で",
        "ユーザーへ報告",
        "UWIへ記録",
        "をSkill機能で起動して登録",
        "をSkill機能で起動してUWI",
    )
    common = "\n".join(path.read_text(encoding="utf-8") for path in (_PLUGIN_ROOT / "rules").glob("*.md"))
    assert not any(value in common for value in prohibited)


@pytest.mark.parametrize(
    "payload",
    [{"hook_event_name": "Unknown"}, {"hook_event_name": "SessionStart"}],
)
def test_rejects_unknown_event_and_missing_source(payload: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        rules_context.main(json.dumps(payload))


def test_session_start_context_fits_cap_with_maximum_instruction(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """追加指示が上限まで載った場合も、固定分との合計がhook上限へ収まる。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv(rules_context.PROCESS_LOOP_INSTRUCTION_ENV, "あ" * process_loop_log.INSTRUCTION_MAX_CHARS)
    monkeypatch.setattr(managed_temp, "_state_root_path", lambda: tmp_path / "external-state")

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "session-1"}))

    output = _output(capsys)
    assert len(output) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT, _session_start_length_report(output)


def test_session_start_injects_process_loop_instruction(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """常駐処理が渡した追加指示をメインへ注入し、委譲先へは注入しない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv(rules_context.PROCESS_LOOP_INSTRUCTION_ENV, "既存のテストコードを先に読む")
    monkeypatch.setattr(managed_temp, "_state_root_path", lambda: tmp_path / "external-state")

    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "session-1"}))
    main_output = _output(capsys)

    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "session-2"}))
    delegated_output = _output(capsys)

    assert "既存のテストコードを先に読む" in main_output
    assert f"<{rules_context.PROCESS_LOOP_INSTRUCTION_ELEMENT} " in main_output
    assert 'origin="user"' in main_output
    assert "既存のテストコードを先に読む" not in delegated_output


def test_session_start_temp_notice_names_tmp_and_delegation(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """SessionStartの領域通知は、`/tmp`を使わずこの領域へ置く行動と、委譲先へ所在を渡す行動を示す。

    所在だけの通知では一時ファイルの置き場所を選ぶ時点で想起されず、`/tmp`へ置いた一時ファイルの削除が
    権限判定に拒否される事象が起きた。
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(managed_temp, "_state_root_path", lambda: tmp_path / "external-state")

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "session-2"}))
    output = _output(capsys)
    entries = managed_temp.list_managed_temp(session_id="session-2")

    assert f"このセッションの管理対象一時領域: {entries[0]['path']}" in output
    assert "一時ファイルは`/tmp`ではなくこの領域の直下へ置く" in output
    assert "サブエージェントへ委ねる場合は、この絶対パスを起動文へ渡す" in output


def test_subagent_start_notifies_existing_session_temp_without_creating(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """SubagentStartは親のSessionStartが作成した領域を新たに作成せずに解決し、SessionStartと同じ所在と行動を通知する。

    Agentツールのサブエージェントへ所在が届かないと、委譲元が起動文へ渡し忘れた場合に`/tmp`が選ばれる。
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(managed_temp, "_state_root_path", lambda: tmp_path / "external-state")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "session-3"}))
    _output(capsys)
    session_root = managed_temp.list_managed_temp(session_id="session-3")[0]["path"]
    monkeypatch.setattr(
        rules_context.managed_temp,
        "create_managed_temp",
        lambda *_args, **_kwargs: pytest.fail("SubagentStartで領域を作成した"),
    )

    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "session_id": "session-3"}))
    output = _output(capsys)

    assert f"このセッションの管理対象一時領域: {session_root}" in output
    assert "一時ファイルは`/tmp`ではなくこの領域の直下へ置く" in output
    assert rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip() in output


def test_subagent_start_without_parent_session_temp_omits_notice(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """親の領域が無い場合は領域の通知を加えない。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(managed_temp, "_state_root_path", lambda: tmp_path / "external-state")

    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "session_id": "no-parent-area"}))

    assert "このセッションの管理対象一時領域" not in _output(capsys)
