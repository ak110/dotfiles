"""外部情報の調査を手動の`atk run-skill`から起動し、取得から結果のUWIの保存と位置の確定までを通す。  pyfltr: allow-comment

モデルの代わりに、起動された子が行う操作（情報源の取得、読めた無料の情報源の記録、結果のUWIの保存）を
同じ公開のコマンド（`atk run-script research-sources`と`atk wi add`の実装）で行う。
キューは試験ごとの隔離したprivate-notesであり、外部への取得は全て記録した応答で置き換える。
"""

import argparse
import dataclasses
import datetime
import io
import json
import os
import pathlib
import time
import zoneinfo
from contextlib import redirect_stdout

import pytest

from agent_toolkit._atk import research_sources, run_script, run_skill, run_skill_batch, weekly_schedule
from agent_toolkit._atk.wi import add, frontmatter, layout, sync
from agent_toolkit._common import private_notes, session_review
from agent_toolkit._testing import git_repository

_REPO = "github.com/test/package"
_FULL_TEXT = "合成の投稿本文。この文は記録へ残してはならない長い本文の一部である。" * 10
_NOW = datetime.datetime(2026, 11, 22, 15, tzinfo=zoneinfo.ZoneInfo("Asia/Tokyo")).timestamp()


def _respond(url: str, _headers: dict[str, str]) -> tuple[int, bytes]:
    if url.startswith(research_sources.X_API_BASE):
        raise AssertionError(f"X APIを呼んだ: {url}")
    if url == research_sources.CLAUDE_CODE_CHANGELOG:
        return 200, f"## 2.3.0\n\n- {_FULL_TEXT}\n\n## 2.2.0\n\n- Old\n".encode()
    if "/commits?" in url:
        return 200, json.dumps([{"sha": "b" * 40}]).encode()
    if "/" + "b" * 40 + "/CHANGELOG.md" in url:
        return 200, b"## 2.2.0\n\n- Old\n"
    if "api.github.com/repos/" in url and "/releases" in url:
        return 200, b"[]"
    raise AssertionError(f"未記録のURL: {url}")


@dataclasses.dataclass
class _FlowState:
    work_dir: pathlib.Path
    runs: list[str] = dataclasses.field(default_factory=list)
    proposals: bool = True
    log: pathlib.Path | None = None


@pytest.fixture(name="flow")
def _flow(monkeypatch, tmp_path):
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    notes = private_notes.default_private_notes()
    git_repository.init_repository(notes)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_REPOS", json.dumps([_REPO]))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_ACCOUNTS", json.dumps(["poteto"]))
    monkeypatch.setattr("time.time", lambda: _NOW)
    monkeypatch.setattr(research_sources, "default_fetch", _respond)
    monkeypatch.setattr(run_skill_batch.repo, "resolve_repo_id_or_raise", lambda value, **_kwargs: value or _REPO)
    state = _FlowState(work_dir=tmp_path)

    def child(args: argparse.Namespace, _record, log, log_path, _progress) -> int:
        """子（モデル）の操作を、run-skillが子へ渡す環境変数の下で再現し、出力を実行ログへ写す。  pyfltr: allow-comment"""
        monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", f"run-{len(state.runs)}")
        monkeypatch.setenv(session_review.WEEKLY_SLOT_ENV, args.weekly_slot)
        state.runs.append(args.weekly_slot)
        state.log = log_path
        output = io.StringIO()
        with redirect_stdout(output):
            assert run_script.dispatch(argparse.Namespace(script_name="research-sources", script_args=[])) == 0
            mark = ["--mark-read", "web:hacker-news"]
            assert run_script.dispatch(argparse.Namespace(script_name="research-sources", script_args=mark)) == 0
        collected = json.loads(output.getvalue().partition("\n}\n")[0] + "\n}")
        event = {"type": "user", "message": {"content": [{"type": "tool_result", "content": output.getvalue()}]}}
        log.write(run_skill.summarize_event_line(json.dumps(event, ensure_ascii=False)) + "\n")
        sources = {source["source"]: source for source in collected["sources"]}
        lines = [f"- {name}: {value['status']} {value['count']}件 {value['reason']}" for name, value in sources.items()]
        body = "## 読んだ情報源\n\n" + "\n".join(lines) + "\n- web:hacker-news: read\n"
        if state.proposals:
            message = f"# 外部情報の調査の提案\n\n{body}\n## 提案\n\n- P1: Claude Code 2.3.0のhookを使う\n"
            question_type, choices = "choice", "推奨の提案を全て採用,回答欄に書いた提案IDだけ採用,全て見送る"
        else:
            message = f"---\nrun_skill_report: true\n---\n\n# 外部情報の調査の報告\n\n{body}\n提案は0件。\n"
            question_type, choices = "post-approval", None
        add.add_entries(
            private_notes.default_private_notes(),
            messages=[message],
            target_repo=args.target_repo,
            source="run-skill",
            entry_type="uwi",
            scope=session_review.RESEARCH_SCOPE,
            question_type=question_type,
            choices=choices,
            now=datetime.datetime.now(datetime.UTC),
            storage=sync.Storage(local_only=True),
        )
        return 0

    monkeypatch.setattr(run_skill, "_dispatch_with_log", child)
    yield state
    monkeypatch.undo()
    time.tzset()


def _manual(work_dir: pathlib.Path) -> int:
    parser = argparse.ArgumentParser()
    run_skill.build_parser(parser)
    args = parser.parse_args(["--args", "research", "--work-dir", str(work_dir), "agent-toolkit:review-session"])
    assert session_review.LAUNCH_ENV not in os.environ
    return run_skill.dispatch(args)


def _entries() -> list[tuple[dict[str, object], str]]:
    result = []
    for path in layout.iter_entry_paths(private_notes.default_private_notes()):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        result.append(parsed)
    return result


@pytest.mark.parametrize(("proposals", "question_type"), [(True, "choice"), (False, "post-approval")])
def test_manual_research_without_x_key_saves_one_result(flow, capsys, proposals, question_type):
    flow.proposals = proposals
    assert _manual(flow.work_dir) == 0
    assert flow.runs == ["research@2026-11-22"]
    entries = _entries()
    assert len(entries) == 1
    data, body = entries[0]
    assert data["question_type"] == question_type
    assert data["weekly_slot"] == "research@2026-11-22"
    assert "x:account:poteto: skipped" in body
    assert "X_BEARER_TOKEN" in body
    assert "changelog:claude-code: read 1件" in body
    state = research_sources.load_state()
    assert state["positions"]["changelog:claude-code"] == "2.3.0"
    assert "web:hacker-news" in state["positions"]
    assert "x:account:poteto" not in state["positions"]
    assert weekly_schedule.read_record("research")["completed_slot"] == "research@2026-11-22"
    stored = "".join(
        path.read_text(encoding="utf-8")
        for root in (private_notes.default_private_notes(), session_review.directory().parent)
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )
    assert _FULL_TEXT[:30] not in stored
    assert flow.log is not None
    log = flow.log.read_text(encoding="utf-8")
    assert "omitted_chars" in log
    assert _FULL_TEXT[:30] not in log

    # 保存済みの週枠は、手動でもう一度起動しても子を起動せず、結果を増やさない。  # pyfltr: allow-comment
    capsys.readouterr()
    assert _manual(flow.work_dir) == 0
    assert flow.runs == ["research@2026-11-22"]
    assert len(_entries()) == 1
    assert "保存済み" in capsys.readouterr().out


def test_manual_research_after_save_interrupted_before_record_does_not_rerun(flow):
    flow.proposals = False
    assert _manual(flow.work_dir) == 0
    # 結果の保存の直後、完了の記録の前に止まった状態へ戻す。  # pyfltr: allow-comment
    weekly_schedule.write_record("research", {"slot": "research@2026-11-22", "started_at": "2026-11-22T06:00:00+00:00"})
    assert _manual(flow.work_dir) == 0
    assert flow.runs == ["research@2026-11-22"]
    assert len(_entries()) == 1
    assert weekly_schedule.read_record("research")["completed_slot"] == "research@2026-11-22"
