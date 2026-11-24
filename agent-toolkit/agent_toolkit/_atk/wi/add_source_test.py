# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

# pylint: disable=protected-access,subprocess-run-check

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._common import session_state, uwi_watch
from agent_toolkit._common.process_loop_session import REPORT_SAVED_AT_KEY
from agent_toolkit._testing.wi_add_support import _cmd_add_args, _patch_cmd_add_operations, _run_public_add
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY as _AGENT_AWI_BODY
from agent_toolkit.atk_test import _FIXED_DT, _make_subprocess_fake, _setup_notes, _write_awi_file


@pytest.mark.windows
def test_edit_accepts_minimal_agent_awi(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = _setup_notes(tmp_path)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setenv("CLAUDECODE", "1")
    path = _write_awi_file(notes, "entry.md", body=f"{_AGENT_AWI_BODY}\n\n## 完成条件\n結果", source="test")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    body_path = tmp_path / "edit.md"
    body_path.write_text(_AGENT_AWI_BODY, encoding="utf-8")

    argv = ["wi", "edit", "entry.md", "--body-file", str(body_path), "--target-repo", "github.com/example/foo"]
    with pytest.raises(SystemExit) as result:
        atk.main(argv, home=tmp_path)
    assert result.value.code == 0, capsys.readouterr().err
    assert "## 完成条件" not in path.read_text(encoding="utf-8")


def test_cmd_add_accepts_agent_awi_with_all_required_sections(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)

    _run_public_add(
        _cmd_add_args(tmp_path, _AGENT_AWI_BODY, source="test"),
        notes,
        _FIXED_DT,
        tmp_path,
    )

    assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == 1


def test_cmd_add_accepts_agent_awi_without_alignment_record(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)

    _run_public_add(_cmd_add_args(tmp_path, _AGENT_AWI_BODY, source="test"), notes, _FIXED_DT, tmp_path)
    assert "scope_alignment" not in next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
    assert "1件をinboxへ投入した" in capsys.readouterr().out


def test_cmd_add_ignores_retired_frontmatter(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    message = (
        "---\nscope_alignment: old-value\nconfirmation: required\nobservation_wait: old-value\n"
        f"origin_session: old-value\norigin_locator: old-value\nsource: test\n---\n\n{_AGENT_AWI_BODY}"
    )
    _run_public_add(_cmd_add_args(tmp_path, message), notes, _FIXED_DT, tmp_path)
    saved_path = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    saved = saved_path.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    for key in ("scope_alignment", "confirmation", "observation_wait", "origin_session", "origin_locator"):
        assert key not in saved and key not in output


def test_add_parser_rejects_removed_alignment_option(capsys: pytest.CaptureFixture[str]) -> None:
    parser = atk._build_parser()  # noqa: SLF001
    with pytest.raises(SystemExit) as option_exit:
        parser.parse_args(["wi", "add", "--scope-aligned"])
    assert option_exit.value.code == 2
    assert "--scope-aligned" in capsys.readouterr().err


def test_cmd_add_accepts_agent_awi_with_required_section_body_starting_with_quoted_h2(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)

    message = _AGENT_AWI_BODY.replace(
        "## 反映内容と反映先\n対象と反映先",
        "## 反映内容と反映先\n\n> ## 引用した規範の見出し\n>\n> 対象と反映先",
    )
    _run_public_add(_cmd_add_args(tmp_path, message, source="test"), notes, _FIXED_DT, tmp_path)

    assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == 1


def test_cmd_add_accepts_human_awi_without_required_sections(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)

    _run_public_add(_cmd_add_args(tmp_path, "本文"), notes, _FIXED_DT, tmp_path)

    assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == 1


def test_cmd_add_omits_origin_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    _run_public_add(_cmd_add_args(tmp_path, "本文"), notes, _FIXED_DT, tmp_path)

    saved = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(saved)
    assert parsed is not None
    assert "origin_session" not in parsed[0]
    assert "origin_locator" not in parsed[0]


@pytest.mark.parametrize(
    ("environment", "entry_type", "message", "expected"),
    [
        ({"CLAUDE_CODE_SESSION_ID": "main-session"}, WI_TYPE_UWI, "質問本文", "main-session"),
        ({"CODEX_THREAD_ID": "codex-thread"}, WI_TYPE_UWI, "質問本文", "codex-thread"),
        (
            {"CLAUDE_CODE_SESSION_ID": "child-session", "AGENT_TOOLKIT_OWNER_SESSION": "owner-session"},
            WI_TYPE_UWI,
            "質問本文",
            "owner-session",
        ),
        ({"AGENT_TOOLKIT_OWNER_SESSION": "mcp-0123abcd"}, WI_TYPE_UWI, "質問本文", None),
        ({}, WI_TYPE_UWI, "質問本文", None),
        ({"CLAUDE_CODE_SESSION_ID": "main-session"}, "awi", "本文", None),
    ],
)
def test_cmd_add_records_submitter_session_only_for_uwi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    entry_type: str,
    message: str,
    expected: str | None,
) -> None:
    """UWIだけへ投入元セッションを保存する。

    回答済みUWIの通知は保存した値で宛先を決めるため、ownerを持たないCodex最上位の投入分はその会話の値、委譲先の投入分は委譲元の値、
    会話へ対応しないプロセス専用の値と未解決の場合はキーを持たない状態にする。
    本文のfrontmatterによる宛先指定の拒否は予約キーの入力テストで確かめる。
    回答の検出は宛先の会話の記録にあるUWIだけを確かめるため、保存したファイル名を同じ会話のセッション状態へ記録する。
    """
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    _run_public_add(_cmd_add_args(tmp_path, message, entry_type=entry_type), notes, _FIXED_DT, tmp_path)

    saved = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    parsed = frontmatter.parse_frontmatter(saved.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0].get("submitter_session") == expected
    recorded = {session_id: uwi_watch.awaiting(session_state.read_state(session_id)) for session_id in environment.values()}
    assert recorded == {session_id: [saved.name] if session_id == expected else [] for session_id in environment.values()}


def test_cmd_add_rejects_missing_source_in_agent_environment(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("AI_AGENT", "1")

    with pytest.raises(SystemExit) as exc_info:
        _run_public_add(_cmd_add_args(tmp_path, "本文"), notes, _FIXED_DT, tmp_path)

    assert exc_info.value.code == 1
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    assert "--source" in capsys.readouterr().err


@pytest.mark.parametrize("entry_type", ["awi", "uwi"])
@pytest.mark.parametrize(
    ("cli_source", "body_source", "accepted"),
    [
        ("human", None, False),
        (None, "human", False),
        ("agent", "human", False),
        ("human", "agent", True),
        ("agent", None, True),
        (None, "add-awi-by-user", True),
        ("session-review", None, True),
        (None, "unknown-skill", True),
    ],
)
def test_public_add_validates_effective_source(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry_type: str,
    cli_source: str | None,
    body_source: str | None,
    accepted: bool,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("AI_AGENT", "1")
    body = _AGENT_AWI_BODY if entry_type == "awi" else "変更を承認するか？"
    message = f"---\nsource: {body_source}\n---\n\n{body}" if body_source is not None else body
    args = _cmd_add_args(tmp_path, message, source=cli_source, entry_type=entry_type)
    if accepted:
        _run_public_add(args, notes, _FIXED_DT, tmp_path)
    else:
        with pytest.raises(SystemExit) as rejected:
            _run_public_add(args, notes, _FIXED_DT, tmp_path)
        assert rejected.value.code == 1
        error = capsys.readouterr().err
        assert "human" in error
        assert "起動中の作業かスキルの名前を指定する（プラグイン接頭辞を除く。" in error
        assert "agent" in error
    saved = list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    assert len(saved) == int(accepted)
    if accepted:
        parsed = frontmatter.parse_frontmatter(saved[0].read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["source"] == (body_source or cli_source)


@pytest.mark.parametrize("entry_type", ["awi", "uwi"])
def test_public_add_rejects_human_source_without_partial_save(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, entry_type: str
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("AI_AGENT", "1")
    body = _AGENT_AWI_BODY if entry_type == "awi" else "変更を承認するか？"
    args = _cmd_add_args(tmp_path, body, source="agent", entry_type=entry_type)
    second = tmp_path / "human.md"
    second.write_text(f"---\nsource: human\n---\n\n{body}", encoding="utf-8")
    args.body_file.append(str(second))
    with pytest.raises(SystemExit) as rejected:
        _run_public_add(args, notes, _FIXED_DT, tmp_path)
    assert rejected.value.code == 1
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.parametrize("source", [None, "human"])
def test_public_add_preserves_human_environment_source(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, source: str | None
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    for name in ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT"):
        monkeypatch.delenv(name, raising=False)
    _run_public_add(_cmd_add_args(tmp_path, _AGENT_AWI_BODY, source=source), notes, _FIXED_DT, tmp_path)
    saved = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    parsed = frontmatter.parse_frontmatter(saved.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0].get("source") == source


@pytest.mark.windows
@pytest.mark.parametrize("source", [None, "human", "agent", "session-review", "unknown-skill"])
def test_public_batch_preserves_source(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, source: str | None) -> None:
    notes = _setup_notes(tmp_path)
    monkeypatch.setenv("AI_AGENT", "1")
    _patch_cmd_add_operations(monkeypatch)
    source_line = f"source: {source}\n" if source is not None else ""
    original = f"---\ntype: awi\nstate: inbox\ntarget_repo: github.com/example/repo\n{source_line}---\n\n本文\n"
    body_file = tmp_path / "batch.md"
    body_file.write_text("### 20260101-000000-001.md [inbox]\n" + original, encoding="utf-8")
    with pytest.raises(SystemExit) as imported:
        atk.main(["wi", "add", "--batch", "--body-file", str(body_file)], home=tmp_path, now=_FIXED_DT)
    assert imported.value.code == 0
    assert (notes / "2026" / "01" / "20260101-000000-001.md").read_text(encoding="utf-8") == original


def test_cmd_add_accepts_frontmatter_source_in_agent_environment(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("AI_AGENT", "1")
    message = f"---\nsource: test\n---\n\n{_AGENT_AWI_BODY}"

    _run_public_add(_cmd_add_args(tmp_path, message), notes, _FIXED_DT, tmp_path)

    assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == 1


def test_cmd_add_accepts_uwi_without_required_sections(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)

    _run_public_add(
        _cmd_add_args(tmp_path, "質問本文", source="test", entry_type=WI_TYPE_UWI),
        notes,
        _FIXED_DT,
        tmp_path,
    )

    assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == 1


@pytest.mark.parametrize("case", ["report", "ordinary", "dry-run", "other-thread", "delegated"])
def test_cmd_add_records_codex_report_save(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "report-thread")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "other" if case == "other-thread" else "report-thread")
    if case == "delegated":
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "report-thread")
    body = (
        "設定変更を承認しますか？"
        if case == "ordinary"
        else "\n2026年11月23日のdotfilesの作業結果について、この対応で問題ありませんか？"
    )
    args = _cmd_add_args(tmp_path, body, source="process-wi", entry_type=WI_TYPE_UWI, dry_run=case == "dry-run")
    args.question_type = "post-approval"
    _run_public_add(args, notes, _FIXED_DT, tmp_path)

    assert (REPORT_SAVED_AT_KEY in session_state.read_state("report-thread")) == (case == "report")
