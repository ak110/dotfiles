from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import draft_review_rounds
import pytest
from agent_toolkit._agents_server import wait_summary


def _time(day: int) -> datetime:
    return datetime(2026, 11, day, tzinfo=UTC)


def _judgment(path: str, verdict: str, identity: str = "", *, stage: str = "本文") -> str:
    label = f"- 要求識別子: {identity}\n" if identity else ""
    subject = f"対象資料: {path}\n" if path else ""
    return f"{label}判定: {verdict}\n確認段階: {stage}\n{subject}"


class _Log:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[dict] = []

    def call(self, tool: str, args: dict, result: object, *, day: int = 26, sent_day: int | None = None) -> None:
        call_id = str(len(self.entries))
        self.entries.extend(
            [
                {
                    "type": "assistant",
                    "timestamp": _time(sent_day or day).isoformat(),
                    "message": {"content": [{"type": "tool_use", "name": tool, "id": call_id, "input": args}]},
                },
                {
                    "type": "user",
                    "timestamp": _time(day).isoformat(),
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": call_id,
                                "content": result if isinstance(result, str) else json.dumps(result),
                            }
                        ]
                    },
                },
            ]
        )

    def start(self, session: str, **kwargs: object) -> None:
        self.call("mcp__agents_server__start", kwargs, {"session_id": session})

    def wait(self, session: str, body: str, *, day: int = 26, sent_day: int | None = None, status: str = "completed") -> None:
        text = (
            f"{wait_summary.TERMINAL_LINE_PREFIX}session_id={session} label=原稿 status={status}\n"
            f"{wait_summary.WAIT_BODY_START_PREFIX}{session}\n{body}"
            f"{wait_summary.WAIT_BODY_END_PREFIX}{session}\n"
        )
        self.call("Bash", {"command": f"/example/scripts/atk agents wait {session}"}, text, day=day, sent_day=sent_day)

    def collect(self) -> draft_review_rounds._Collected:
        self.path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in self.entries), encoding="utf-8")
        return draft_review_rounds.collect([self.path.parent])


_ROLE = "/example/roles/wi-draft-review.subagent.md"


def _wrap_codex(entries: list[dict[str, object]], code: str, output: str, *, day: int = 26) -> None:
    call_id = str(len(entries))
    for payload in (
        {"type": "custom_tool_call", "name": "functions.exec", "input": code},
        {"type": "custom_tool_call_output", "output": "Script completed\nWall time: 1 seconds\nOutput:\n" + output},
    ):
        entries.append(
            {
                "type": "response_item",
                "timestamp": _time(day).isoformat(),
                "payload": {**payload, "call_id": call_id},
            }
        )


def test_collects_wrapped_codex_start_and_wait(tmp_path: Path) -> None:
    path = tmp_path / "rollout-parent.jsonl"
    entries: list[dict[str, object]] = []

    for index, verdict in enumerate(("修正要", "合格"), 1):
        if index == 1:
            _wrap_codex(
                entries,
                f'text((await tools.mcp__agents_server__start({{ subagent_md_path: "{_ROLE}" }})).structuredContent)',
                '{"session_id":"reviewer"}',
            )
        _wrap_codex(
            entries,
            "text(await tools.mcp__agents_server__wait({}))",
            json.dumps(
                {
                    "structuredContent": {
                        "sessions": [
                            {
                                "session_id": "reviewer",
                                "status": "completed",
                                "agent_message": _judgment("/draft.md", verdict),
                            }
                        ]
                    }
                }
            ),
        )
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    assert draft_review_rounds.period(draft_review_rounds.collect([path]), _time(25), _time(27))["passed"] == 1


@pytest.mark.parametrize("pragma", ["", '// @exec: {"yield_time_ms": 1000}\n'])
def test_collects_wait_process_completion_once(tmp_path: Path, pragma: str) -> None:
    path = tmp_path / "rollout-parent.jsonl"
    entries: list[dict[str, object]] = []
    wait = 'text(await tools.exec_command({cmd:"/example/scripts/atk agents wait"}))'

    def wrap(code: str, *, day: int = 26, **output: object) -> None:
        _wrap_codex(entries, pragma + code, json.dumps(output), day=day)

    def poll(process: int, *, day: int = 26, **output: object) -> None:
        wrap(f'text(await tools.write_stdin({{session_id:{process}, chars:""}}))', day=day, **output)

    def result(session: str, path: str) -> str:
        return (
            f"{wait_summary.TERMINAL_LINE_PREFIX}session_id={session} label=原稿 status=completed\n"
            f"{wait_summary.WAIT_BODY_START_PREFIX}{session}\n{_judgment(path, '合格')}\n"
            f"{wait_summary.WAIT_BODY_END_PREFIX}{session}\n"
        )

    first, second, unrelated = result("one", "/one.md"), result("two", "/two.md"), result("other", "/other.md")
    wrap(wait, exit_code=0, output=first, day=25)
    wrap(wait, session_id=901, output=first[:25])
    wrap(wait, session_id=902, output="")
    wrap('text(await tools.exec_command({cmd:"echo agents wait"}))', session_id=903, output="")
    poll(903, exit_code=0, output=unrelated)
    poll(904, exit_code=0, output=unrelated)
    poll(902, exit_code=1, output=unrelated)
    poll(902, exit_code=0, output=unrelated)
    poll(901, session_id=901, output=first[25:])
    poll(901, exit_code=0, output=second)
    poll(901, exit_code=0, output=unrelated)
    wrap(wait, exit_code=0, output=second, day=27)
    wrap(wait, exit_code=1, output=unrelated, day=27)
    _wrap_codex(
        entries,
        'text(await tools.mcp__agents_server__send_message({session_id:"two", prompt:"再レビュー"}))',
        '{"delivery":"reply_started"}',
        day=28,
    )
    wrap(wait, exit_code=0, output=second, day=28)
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    assert [(row.timestamp, row.session, row.digest) for row in draft_review_rounds.collect([path]).receipts] == [
        (_time(day), session, hashlib.sha256(_judgment(subject, "合格").strip().encode("utf-8")).hexdigest())
        for day, session, subject in ((25, "one", "/one.md"), (26, "two", "/two.md"), (28, "two", "/two.md"))
    ]


def test_rounds_with_handoff_and_receipt_time(tmp_path: Path) -> None:
    log = _Log(tmp_path / "parent.jsonl")
    log.start("reviewer", subagent_md_path=_ROLE)
    log.start("successor", handoff_from="reviewer")
    log.start("other", subagent_md_path="/example/roles/exec-review.subagent.md")
    log.wait("other", _judgment("/unrelated.md", "合格"))
    log.wait(
        "reviewer",
        "レビュー結果:\n"
        + _judgment("/passed.md", "修正要", "a")
        + _judgment("/failed.md", "修正要", "b")
        + _judgment("/first.md", "合格", "c")
        + _judgment("/no-second.md", "修正要", "d")
        + _judgment("", "修正要", "e")
        + _judgment("/policy.md", "合格", "f", stage="方針"),
        sent_day=25,
    )
    log.wait("successor", _judgment("/passed.md", "合格"), day=27)
    log.wait("reviewer", _judgment("/failed.md", "修正要"), day=27)
    log.wait("reviewer", _judgment("/no-second.md", "合格"), status="failed")
    assert draft_review_rounds.period(log.collect(), _time(26), _time(26)) == {
        "passed": 1,
        "failed": 1,
        "first_pass": 1,
        "no_second": 1,
        "missing_path": 1,
        "unassigned": 0,
        "pass_rate": 0.5,
    }


def test_missing_identity_requires_unique_attribution(tmp_path: Path) -> None:
    log = _Log(tmp_path / "parent.jsonl")
    log.start("reviewer", subagent_md_path=_ROLE)
    log.wait("reviewer", _judgment("/same.md", "修正要", "a") + _judgment("/same.md", "修正要", "b"))
    log.wait("reviewer", _judgment("/same.md", "合格"))
    log.wait("reviewer", _judgment("/same.md", "合格"))
    assert draft_review_rounds.period(log.collect(), _time(26), _time(26)) == {
        "passed": 0,
        "failed": 0,
        "first_pass": 0,
        "no_second": 2,
        "missing_path": 0,
        "unassigned": 1,
        "pass_rate": "判定不能",
    }
    log.call(
        "mcp__agents_server__send_message",
        {"session_id": "reviewer", "prompt": "要求識別子: b\n対象資料: /same.md"},
        {"session_id": "reviewer", "status": "running"},
        day=27,
    )
    log.wait("reviewer", _judgment("/same.md", "合格"), day=27)
    result = draft_review_rounds.period(log.collect(), _time(26), _time(26))
    assert (result["passed"], result["unassigned"], result["no_second"], result["pass_rate"]) == (1, 1, 1, 1.0)


def test_collects_named_rejudgment_from_wrapped_codex(tmp_path: Path) -> None:
    path = tmp_path / "rollout-parent.jsonl"
    draft = r"C:\drafts\shared.md"
    entries: list[dict[str, object]] = []
    _wrap_codex(
        entries,
        f'text(await tools.mcp__agents_server__start({{ subagent_md_path: "{_ROLE}" }}))',
        '{"session_id":"reviewer"}',
    )

    def wait(body: str, *, day: int) -> None:
        _wrap_codex(
            entries,
            "text(await tools.mcp__agents_server__wait({}))",
            json.dumps({"session_id": "reviewer", "status": "completed", "agent_message": body}),
            day=day,
        )

    wait(_judgment(draft, "修正要", "a") + _judgment(draft, "修正要", "b"), day=26)
    args = {"session_id": "reviewer", "instruction": "再判定せよ", "extra_params": {"原稿": draft, "要求識別子": "b"}}
    _wrap_codex(
        entries,
        f"text(await tools.mcp__agents_server__send_message({json.dumps(args, ensure_ascii=False)}))",
        '{"delivery":"reply_started"}',
        day=27,
    )
    wait(_judgment(draft, "合格"), day=27)
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    result = draft_review_rounds.period(draft_review_rounds.collect([path]), _time(26), _time(27))
    assert (result["passed"], result["unassigned"], result["no_second"]) == (1, 0, 1)


@pytest.mark.parametrize(("shared_path", "moved_draft"), [(False, False), (True, False), (False, True), (True, True)])
def test_rejudgment_uses_draft_and_identity_from_named_continuation(
    tmp_path: Path, shared_path: bool, moved_draft: bool
) -> None:
    log = _Log(tmp_path / "parent.jsonl")
    log.start("reviewer", subagent_md_path=_ROLE)
    draft = "/shared.md"
    first = _judgment(draft, "修正要", "b")
    if shared_path:
        first = _judgment(draft, "修正要", "a") + first
    log.wait("reviewer", first)
    if moved_draft:
        draft = "/corrected.md"
    log.call(
        "mcp__agents_server__send_message",
        {
            "session_id": "reviewer",
            "instruction": "再判定せよ",
            "extra_params": {
                "原稿": "/mapping.json" if shared_path and moved_draft else draft,
                "要求識別子": "b",
                "指摘の扱い": "/corrections.md",
            },
        },
        {"delivery": "reply_started"},
        day=27,
    )
    log.wait("reviewer", _judgment(draft, "合格"), day=27)
    result = draft_review_rounds.period(log.collect(), _time(26), _time(27))
    assert (result["passed"], result["unassigned"], result["no_second"]) == (1, 0, int(shared_path))


@pytest.mark.parametrize("prior_paths", [["/before.md"], ["/before.md", "/another.md"]])
def test_single_draft_rejudgment_uses_the_only_prior_judgment(tmp_path: Path, prior_paths: list[str]) -> None:
    log = _Log(tmp_path / "parent.jsonl")
    log.start("reviewer", subagent_md_path=_ROLE, extra_params={"原稿": prior_paths[0]})
    log.wait("reviewer", "".join(_judgment(path, "修正要") for path in prior_paths))
    log.call(
        "mcp__agents_server__send_message",
        {
            "session_id": "reviewer",
            "instruction": "再判定せよ",
            "extra_params": {"原稿": "/corrected.md", "指摘の扱い": "/corrections.md"},
        },
        {"delivery": "reply_started"},
        day=27,
    )
    log.wait("reviewer", _judgment("/corrected.md", "合格"), day=27)
    result = draft_review_rounds.period(log.collect(), _time(26), _time(27))
    expected = (1, 0, 0) if len(prior_paths) == 1 else (0, 1, 2)
    assert (result["passed"], result["unassigned"], result["no_second"]) == expected
    assert result["first_pass"] == 0


@pytest.mark.parametrize(
    ("second", "expected"),
    [
        pytest.param(_judgment("/shared.md", "合格") + _judgment("/shared.md", "修正要"), (0, 0, 2, 2), id="ambiguous"),
        pytest.param(_judgment("/shared.md", "合格"), (1, 0, 0, 1), id="single"),
        pytest.param(
            _judgment("/corrected-a.md", "合格") + _judgment("/corrected-b.md", "合格"),
            (0, 0, 2, 2),
            id="different-paths",
        ),
        pytest.param(
            _judgment("/shared.md", "修正要", "a") + _judgment("/shared.md", "合格"),
            (1, 1, 0, 0),
            id="explicit-other",
        ),
        pytest.param(
            _judgment("/shared.md", "修正要") + _judgment("/shared.md", "合格", "b"),
            (1, 0, 1, 1),
            id="explicit-selected",
        ),
    ],
)
def test_rejudgment_assigns_only_unique_candidate_for_an_id(
    tmp_path: Path, second: str, expected: tuple[int, int, int, int]
) -> None:
    log = _Log(tmp_path / "parent.jsonl")
    log.start("reviewer", subagent_md_path=_ROLE)
    log.wait("reviewer", _judgment("/shared.md", "修正要", "a") + _judgment("/shared.md", "修正要", "b"))
    log.call(
        "mcp__agents_server__send_message",
        {
            "session_id": "reviewer",
            "instruction": "再判定せよ",
            "extra_params": {"原稿": "/mapping.json", "要求識別子": "b", "指摘の扱い": "/corrections.md"},
        },
        {"delivery": "reply_started"},
        day=27,
    )
    log.wait("reviewer", second, day=27)
    result = draft_review_rounds.period(log.collect(), _time(26), _time(27))
    assert (result["passed"], result["failed"], result["unassigned"], result["no_second"]) == expected
    assert result["first_pass"] == 0


def test_index_reuses_unchanged_records_across_periods(tmp_path, monkeypatch, capsys):
    first = _Log(tmp_path / "first.jsonl")
    first.call("mcp__agents_server__start", {"subagent_md_path": _ROLE}, {"session_id": "reviewer"}, day=23)
    first.wait("reviewer", _judgment("/draft.md", "修正要"))
    first.collect()
    later = _Log(tmp_path / "later.jsonl")
    later.wait("reviewer", _judgment("/draft.md", "合格"))
    later.collect()
    for item in (first.path, later.path):
        os.utime(item, (1, 1))
    index = tmp_path / "index.json"
    roots = [first.path, later.path]
    full = draft_review_rounds.period(draft_review_rounds.collect(roots), _time(26), _time(27))
    assert full["passed"] == 1
    for parsed, reused in ((2, 0), (0, 2)):
        cached = draft_review_rounds.collect(roots, index=index)
        assert draft_review_rounds.period(cached, _time(26), _time(27)) == full
        assert f"解析={parsed}件、再利用={reused}件" in capsys.readouterr().err
    later.wait("reviewer", _judgment("/next.md", "修正要"))
    later.collect()
    os.utime(later.path, (2, 2))
    assert draft_review_rounds.collect(roots, index=index) == draft_review_rounds.collect(roots)
    assert "解析=1件、再利用=1件" in capsys.readouterr().err
    monkeypatch.setattr(draft_review_rounds.derived_index, "code_version", lambda *args, **kwargs: "new")
    assert draft_review_rounds.collect(roots, index=index) == draft_review_rounds.collect(roots)
    assert "解析=2件、再利用=0件" in capsys.readouterr().err


def test_interrupted_index_prunes_deleted_values_and_recovers(tmp_path, monkeypatch, capsys):
    first = _Log(tmp_path / "first.jsonl")
    first.start("reviewer", subagent_md_path=_ROLE, prompt="要求識別子: item-1\n要求識別子: item-1\n本文保全の検査")
    first.start("other", subagent_md_path=_ROLE, prompt="要求識別子: one\n要求識別子: two\n保存しない依頼")
    first.wait("reviewer", "保存しない返却\n" + _judgment("/draft.md", "修正要"))
    first.collect()
    later = _Log(tmp_path / "later.jsonl")
    later.wait("reviewer", _judgment("/draft.md", "合格"))
    later.collect()
    for item in (first.path, later.path):
        os.utime(item, (1, 1))
    index = tmp_path / "index.json"
    monkeypatch.setattr(draft_review_rounds, "_CHECKPOINT_RECORDS", 1)
    extract = draft_review_rounds._extract  # pylint: disable=protected-access

    def interrupt(path):
        if path == later.path:
            raise KeyboardInterrupt
        return extract(path)

    with monkeypatch.context() as suspend:
        suspend.setattr(draft_review_rounds, "_extract", interrupt)
        with pytest.raises(KeyboardInterrupt):
            draft_review_rounds.collect([tmp_path], index=index)
    text = index.read_text(encoding="utf-8")
    assert "本文保全の検査" in text
    assert "保存しない依頼" not in text
    assert "保存しない返却" not in text
    assert draft_review_rounds.collect([tmp_path], index=index) == draft_review_rounds.collect([tmp_path])
    assert "解析=1件、再利用=1件" in capsys.readouterr().err

    first.path.unlink()
    assert draft_review_rounds.collect([tmp_path], index=index) == draft_review_rounds.collect([tmp_path])
    assert "本文保全の検査" not in index.read_text(encoding="utf-8")
    temporary = tmp_path / ".index.json.abandoned.tmp"
    temporary.write_text(text, encoding="utf-8")
    later.wait("reviewer", _judgment("/another.md", "合格"))
    later.collect()
    os.utime(later.path, (2, 2))
    monkeypatch.setattr(draft_review_rounds, "_INDEX_MAX_BYTES", 1)
    assert draft_review_rounds.collect([tmp_path], index=index) == draft_review_rounds.collect([tmp_path])
    assert not index.exists()
    assert not temporary.exists()
    assert "容量上限を超えた" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("prompts", "passed", "unassigned"),
    [
        (("要求識別子: a",), 1, 0),
        (("要求識別子: a\n要求識別子: a",), 1, 0),
        (("要求識別子: a\n要求識別子: b",), 0, 1),
        (("要求識別子: a", "要求識別子: a"), 1, 0),
        (("要求識別子: a", "要求識別子: a\n別の依頼"), 0, 1),
    ],
)
def test_index_preserves_attribution_from_same_timestamp_requests(tmp_path, prompts, passed, unassigned):
    log = _Log(tmp_path / "source.jsonl")
    log.start("reviewer", subagent_md_path=_ROLE)
    log.wait("reviewer", _judgment("/same.md", "修正要", "a") + _judgment("/same.md", "修正要", "b"))
    for prompt in prompts:
        log.call(
            "mcp__agents_server__send_message",
            {"session_id": "reviewer", "prompt": prompt + "\n対象資料: /same.md"},
            {"delivery": "steered"},
            day=27,
        )
    log.wait("reviewer", _judgment("/same.md", "合格"), day=28)
    expected = draft_review_rounds.period(log.collect(), _time(26), _time(28))
    assert (expected["passed"], expected["unassigned"]) == (passed, unassigned)
    os.utime(log.path, (1, 1))
    index = tmp_path / "index.json"
    for _ in range(2):
        found = draft_review_rounds.collect([log.path], index=index)
        assert draft_review_rounds.period(found, _time(26), _time(28)) == expected


def test_code_version_follows_transitive_script_imports(tmp_path):
    (tmp_path / "entry.py").write_text("from helper import value\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("from detail import value\n", encoding="utf-8")
    detail = tmp_path / "detail.py"
    detail.write_text("value = 1\n", encoding="utf-8")
    version = draft_review_rounds.derived_index.code_version
    first = version("entry", script_root=tmp_path)
    detail.write_text("value = 2\n", encoding="utf-8")
    version.cache_clear()
    assert version("entry", script_root=tmp_path) != first


def test_index_retries_only_unreadable_records(tmp_path, monkeypatch, capsys):
    log = _Log(tmp_path / "source.jsonl")
    log.start("reviewer", subagent_md_path=_ROLE)
    log.wait("reviewer", _judgment("/draft.md", "修正要"))
    log.wait("reviewer", _judgment("/draft.md", "合格"), day=27)
    log.collect()
    empty = tmp_path / "empty.jsonl"
    empty.touch()
    for path in (log.path, empty):
        os.utime(path, (1, 1))
    index = tmp_path / "index.json"
    opened = Path.open

    def inaccessible(path, *args, **kwargs):
        if path == log.path:
            raise PermissionError(path)
        return opened(path, *args, **kwargs)

    with monkeypatch.context() as denied:
        denied.setattr(Path, "open", inaccessible)
        assert not draft_review_rounds.collect([tmp_path], index=index).receipts
    expected = draft_review_rounds.collect([tmp_path])
    assert draft_review_rounds.period(expected, _time(26), _time(27))["passed"] == 1
    assert draft_review_rounds.collect([tmp_path], index=index) == expected
    assert "解析=1件、再利用=1件" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        (0, {"child": ["parent"]}),
        (1, [{}]),
        (2, [["2026-11-26T10:00:00+00:00", "reviewer", "digest", 1, None]]),
        (2, [["2026-11-26T10:00:00", "reviewer", "digest", 1, []]]),
        (2, [["2026-11-26T10:00:00+00:00", [], "digest", 1, []]]),
        (2, [["2026-11-26T10:00:00+00:00", "reviewer", "digest", 1, [{"判定": []}]]]),
        (3, {"reviewer": [["2026-11-26T10:00:00+00:00", "digest", 42, "prompt"]]}),
    ],
)
def test_index_rebuilds_corrupted_derived_values(tmp_path, capsys, field, replacement):
    log = _Log(tmp_path / "source.jsonl")
    log.start("reviewer", subagent_md_path=_ROLE, prompt="要求識別子: item")
    log.wait("reviewer", _judgment("/draft.md", "合格"))
    expected = log.collect()
    os.utime(log.path, (1, 1))
    index = tmp_path / "index.json"
    assert draft_review_rounds.collect([log.path], index=index) == expected
    capsys.readouterr()
    saved = json.loads(index.read_text(encoding="utf-8"))
    saved["entries"][str(log.path)][3 + field] = replacement
    index.write_text(json.dumps(saved), encoding="utf-8")
    assert draft_review_rounds.collect([log.path], index=index) == expected
    assert "解析=1件、再利用=0件" in capsys.readouterr().err
    assert draft_review_rounds.collect([log.path], index=index) == expected
    assert "解析=0件、再利用=1件" in capsys.readouterr().err


@pytest.mark.parametrize("broken", [False, True])
def test_invalid_index_drops_deleted_record_even_when_no_records_remain(tmp_path, monkeypatch, broken):
    log = _Log(tmp_path / "source.jsonl")
    log.start("reviewer", subagent_md_path=_ROLE, prompt="要求識別子: item\n削除する依頼本文")
    log.collect()
    os.utime(log.path, (1, 1))
    index = tmp_path / "index.json"
    draft_review_rounds.collect([log.path], index=index)
    prior = index.read_text(encoding="utf-8")
    assert "削除する依頼本文" in prior
    temporary = tmp_path / ".index.json.interrupted.tmp"
    temporary.write_text(prior, encoding="utf-8")
    if broken:
        index.write_text(prior[:-1], encoding="utf-8")
    else:
        monkeypatch.setattr(draft_review_rounds.derived_index, "code_version", lambda *args, **kwargs: "new")
    log.path.unlink()
    assert draft_review_rounds.collect([log.path], index=index) == draft_review_rounds.collect([log.path])
    assert json.loads(index.read_text(encoding="utf-8"))["entries"] == {}
    assert not temporary.exists()
