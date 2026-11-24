"""外部情報の調査を手動の`atk run-skill`から起動し、取得から結果のUWIの保存と位置の確定までを通す。  pyfltr: allow-comment

モデルの代わりに、起動された子が行う操作（情報源の取得、読めた無料の情報源の記録、結果のUWIの保存）を
同じ公開のコマンド（`atk run-script research-sources`と`atk wi add`の実装）で行う。
子の標準出力と標準エラーは、製品が子のセッションを待つ処理（`run_skill._wait_session`）へ渡し、実行ログへ書かせる。
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
import typing
import urllib.parse
import zoneinfo
from contextlib import redirect_stdout

import pytest

from agent_toolkit._atk import config_settings, research_sources, run_script, run_skill, run_skill_batch, weekly_schedule
from agent_toolkit._atk.run_skill_execution import _SessionOutput

if typing.TYPE_CHECKING:
    import subprocess
from agent_toolkit._atk.wi import add, frontmatter, layout, sync
from agent_toolkit._common import private_notes, session_review, skill_modes
from agent_toolkit._testing import git_repository

_REPO = "github.com/test/package"
_FULL_TEXT = "合成の投稿本文。この文は記録へ残してはならない長い本文の一部である。" * 10
_TOKEN = "AAAAAAAAAAAAAAAAAAAAAsynthetic-flow-token"
_NOW = datetime.datetime(2026, 11, 22, 15, tzinfo=zoneinfo.ZoneInfo("Asia/Tokyo")).timestamp()


def _respond(url: str, _headers: dict[str, str]) -> tuple[int, bytes]:
    if url.startswith(f"{research_sources.X_API_BASE}/users/by/username/"):
        return 200, b'{"data": {"id": "11"}}'
    if url.startswith(f"{research_sources.X_API_BASE}/users/11/tweets"):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert int(query["max_results"][0]) <= 100
        posts = [{"id": "1001", "created_at": "2026-11-21T00:00:00.000Z", "text": f"{_FULL_TEXT}X"}]
        return 200, json.dumps({"data": posts, "meta": {"newest_id": "1001", "oldest_id": "1001"}}).encode()
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
    x_calls: int = 0


class _FinishedProcess:
    """終了済みの子プロセスとして、記録した標準出力と標準エラーを返す。  pyfltr: allow-comment"""

    def __init__(self, stdout: bytes, stderr: bytes) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = 0
        self.args = ["claude"]
        self.pid = os.getpid()

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def poll(self) -> int:
        return 0


def _child_streams(output: str) -> tuple[bytes, bytes]:
    """調査の出力を読んだ会話の行、本文を含む失敗の診断の行、本文を含む標準エラーの行。  pyfltr: allow-comment"""
    events = [
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": output}]}},
        {"type": "error", "message": f"要約に失敗した投稿: {_FULL_TEXT}"},
        {"type": "result", "is_error": True, "result": f"失敗: {_FULL_TEXT}"},
    ]
    stdout = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
    return stdout.encode(), f"Error: {_FULL_TEXT}\n".encode()


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
    monkeypatch.setattr(run_skill._wi_add, "has_synced_run_report", lambda *_args, **_kwargs: True)  # noqa: SLF001  # pylint: disable=protected-access
    monkeypatch.setattr(run_skill_batch.repo, "resolve_repo_id_or_raise", lambda value, **_kwargs: value or _REPO)
    state = _FlowState(work_dir=tmp_path)

    def fetch(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        if url.startswith(research_sources.X_API_BASE):
            state.x_calls += 1
            assert headers["Authorization"] == f"Bearer {_TOKEN}"
        return _respond(url, headers)

    monkeypatch.setattr(research_sources, "default_fetch", fetch)

    def child(args: argparse.Namespace, _record, log, log_path, progress) -> int:
        """子（モデル）の操作を、run-skillが子へ渡す環境変数の下で再現する。  pyfltr: allow-comment"""
        run_id = f"run-{len(state.runs)}"
        monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", run_id)
        monkeypatch.setenv(session_review.WEEKLY_SLOT_ENV, args.weekly_slot)
        state.runs.append(args.weekly_slot)
        state.log = log_path
        output = io.StringIO()
        with redirect_stdout(output):
            assert run_script.dispatch(argparse.Namespace(script_name="research-sources", script_args=[])) == 0
            mark = ["--mark-read", "web:hacker-news"]
            assert run_script.dispatch(argparse.Namespace(script_name="research-sources", script_args=mark)) == 0
        collected = json.loads(output.getvalue().partition("\n}\n")[0] + "\n}")
        sources = {source["source"]: source for source in collected["sources"]}
        lines = [f"- {name}: {value['status']} {value['count']}件 {value['reason']}" for name, value in sources.items()]
        posts = [item for source in collected["sources"] if source["source"].startswith("x:") for item in source["items"]]
        lines += [f"- {post['url']}（{post['created_at']}）: {post['text'][:20]}" for post in posts]
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
        return run_skill._wait_session(  # noqa: SLF001  # pylint: disable=protected-access
            typing.cast("subprocess.Popen[bytes]", _FinishedProcess(*_child_streams(output.getvalue()))),
            orchestrator="claude",
            timeout=60,
            log=log,
            log_path=log_path,
            progress=progress,
            output=_SessionOutput(),
            codex_home=tmp_path,
            run_id=run_id,
            scope=skill_modes.invocation_scope(args.skill, args.args),
            executor=None,
        )

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


@pytest.mark.parametrize("x_key", [False, True])
@pytest.mark.parametrize(("proposals", "question_type"), [(True, "choice"), (False, "post-approval")])
def test_manual_research_saves_one_result_without_full_text(flow, capsys, x_key, proposals, question_type):
    if x_key:
        path = config_settings.config_dir() / "x-api.env"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{research_sources.X_TOKEN_VARIABLE}={_TOKEN}\n", encoding="utf-8")
    flow.proposals = proposals
    assert _manual(flow.work_dir) == 0
    assert flow.runs == ["research@2026-11-22"]
    assert flow.x_calls == (2 if x_key else 0)
    entries = _entries()
    assert len(entries) == 1
    data, body = entries[0]
    assert data["question_type"] == question_type
    assert data["weekly_slot"] == "research@2026-11-22"
    assert "changelog:claude-code: read 1件" in body
    if x_key:
        assert "x:account:poteto: read 1件" in body
        assert "https://x.com/poteto/status/1001" in body
    else:
        assert "x:account:poteto: skipped" in body
        assert "X_BEARER_TOKEN" in body
    state = research_sources.load_state()
    assert state["positions"]["changelog:claude-code"] == "2.3.0"
    assert "web:hacker-news" in state["positions"]
    assert state["positions"].get("x:account:poteto") == ("1001" if x_key else None)
    assert weekly_schedule.read_record("research")["completed_slot"] == "research@2026-11-22"
    assert flow.log is not None
    log = flow.log.read_text(encoding="utf-8")
    assert '[stdout] {"omitted_chars"' in log
    assert '"type": "error", "is_error": true' in log
    assert "[stderr] " in log
    stored = log + "".join(
        path.read_text(encoding="utf-8")
        for root in (private_notes.default_private_notes(), session_review.directory().parent)
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )
    assert _FULL_TEXT[:30] not in stored
    assert _TOKEN not in stored

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
