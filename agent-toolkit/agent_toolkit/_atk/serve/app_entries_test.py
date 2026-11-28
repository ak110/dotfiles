# pylint: disable=protected-access

import datetime
import os
import pathlib
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state, wi_operations, wi_routes
from agent_toolkit._atk.wi.layout import entry_path
from agent_toolkit._testing.serve_app_support import _OBSERVATION_RECORD, _fixed_path, _serve_app
from agent_toolkit._testing.serve_support import AWI_TEXT, _write_repo_entry


def test_operations_read_legacy_type_values_as_current_kinds(tmp_path: pathlib.Path) -> None:
    inbox = _fixed_path(tmp_path, "entry.md").parent
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "entry.md").write_text(
        "---\nstate: inbox\ntype: awi\ntarget_repo: example/repo\n---\n\n要約本文\n",
        encoding="utf-8",
    )
    (inbox / "question.md").write_text(
        "---\nstate: inbox\ntype: uwi\ntarget_repo: example/repo\n---\n\n質問本文\n",
        encoding="utf-8",
    )

    result, warnings = wi_operations.Operations(tmp_path).entries_with_warnings({})

    assert not warnings
    assert {str(item["filename"]): item["kind"] for item in result} == {"entry.md": "awi", "question.md": "uwi"}
    assert [item["answered"] for item in result if item["filename"] == "question.md"] == [False]


@pytest.mark.asyncio
@pytest.mark.parametrize("state_name", ["inbox", "hold"])
@pytest.mark.parametrize("kind", ["awi", "uwi", None])
@pytest.mark.parametrize(
    "deadline,awi_expected",
    [
        ("2099-01-02T03:04:00+09:00", "2099-01-02T03:04:00+09:00"),
        ("2020-01-02T03:04:00Z", "2020-01-02T03:04:00+00:00"),
        ("2099-01-02T03:04:00", None),
        ("bad", None),
        ("", None),
        (None, None),
    ],
)
async def test_entries_cooldown_conditions(
    tmp_path: pathlib.Path,
    state_name: str,
    kind: str | None,
    deadline: str | None,
    awi_expected: str | None,
) -> None:
    """一覧と詳細APIは冷却期限をAWIの有効な日時だけで返し、保存値と保存状態は詳細から読める。

    UWIや種別不明（`type`欠落）の項目へ有効な日時を返すと、着手可否判定が不正期限とする項目を画面が冷却中と示す。
    """
    type_line = f"type: {kind}\n" if kind is not None else ""
    cooldown_line = f"cooldown_until: '{deadline}'\n" if deadline is not None else ""
    original = f"---\nstate: {state_name}\n{type_line}{cooldown_line}---\n\n# 冷却\n"
    path = _fixed_path(tmp_path, "cool.md")
    path.write_text(original, encoding="utf-8")
    client = _serve_app(tmp_path).test_client()
    listed = await (await client.get(f"/api/entries?status={state_name}&period=all")).get_json()
    expected = awi_expected if kind == "awi" else None
    assert len(listed["entries"]) == 1
    assert listed["entries"][0]["cooldown_until"] == expected
    detail = await (await client.get(f"/api/entries/{state_name}/cool.md")).get_json()
    assert detail["entry"]["cooldown_until"] == expected
    assert detail["entry"]["state"] == state_name
    stored = {item["key"]["value"]: item["value"] for item in detail["entry"]["frontmatter_entries"]}
    assert stored.get("cooldown_until") == deadline
    assert detail["entry"]["content"] == original
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_entries_exclude_observation_only_awi_before_pagination(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wi_routes, "_ENTRY_PAGE_SIZE", 2)
    awi = "---\nstate: inbox\ntype: awi\ntarget_repo: example/repo\n---\n\n# 件名\n\n本文\n\n"
    uwi = "---\nstate: inbox\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
    other_record = _OBSERVATION_RECORD.replace("反映後の観測だけが残る", "再実装へ戻す")
    cases = {
        ("inbox", "verify.md"): (awi + _OBSERVATION_RECORD, True),
        ("inbox", "latest-verify.md"): (awi + other_record + "\n" + _OBSERVATION_RECORD, True),
        ("inbox", "plain.md"): (awi, False),
        ("processing", "processing.md"): (awi + _OBSERVATION_RECORD, True),
        ("hold", "hold.md"): (awi + _OBSERVATION_RECORD, False),
        ("inbox", "other.md"): (awi + _OBSERVATION_RECORD + "\n" + other_record, False),
        ("inbox", "fenced.md"): (awi + "```markdown\n" + _OBSERVATION_RECORD + "```\n", False),
        ("inbox", "fenced-marker.md"): (
            awi + other_record + "\n```markdown\n" + _OBSERVATION_RECORD.split("\n\n", 1)[1] + "```\n",
            False,
        ),
        ("inbox", "uwi.md"): (uwi + _OBSERVATION_RECORD, False),
    }
    for (state_name, filename), (text, _expected) in cases.items():
        _fixed_path(tmp_path, filename).write_text(text.replace("state: inbox", f"state: {state_name}"), encoding="utf-8")
    client = _serve_app(tmp_path).test_client()

    listed = await (await client.get("/api/entries?status=all&period=all")).get_json()
    inbox = await (await client.get("/api/entries?status=inbox&period=all")).get_json()
    detail = await (await client.get("/api/entries/inbox/verify.md")).get_json()

    flags = {(item["state"], item["filename"]): item["awaiting_observation"] for item in listed["entries"]}
    assert flags == {key: expected for key, (_text, expected) in cases.items()}
    assert ("inbox", "verify.md") in {(item["state"], item["filename"]) for item in inbox["entries"]}
    assert detail["entry"]["awaiting_observation"] is True
    assert detail["entry"]["state"] == "inbox"
    assert _OBSERVATION_RECORD.strip() in detail["entry"]["content"]
    url = "/api/entries?view=normal&status=active&period=all"
    normal = await (await client.get(url)).get_json()
    expected = {"hold.md", "plain.md", "other.md", "fenced.md", "fenced-marker.md", "uwi.md"}
    assert {item["filename"] for item in normal["entries"]} == expected
    pages = [await (await client.get(f"{url}&page={number}")).get_json() for number in (1, 2, 3)]
    assert [page["pagination"]["total_count"] for page in pages] == [6, 6, 6]
    assert [page["pagination"]["page_count"] for page in pages] == [3, 3, 3]
    assert [item for page in pages for item in page["entries"]] == normal["entries"]
    processing = await (await client.get("/api/entries?status=processing&period=all")).get_json()
    assert [item["filename"] for item in processing["entries"]] == ["processing.md"]


@pytest.mark.asyncio
async def test_entries_api_keeps_readable_entries_and_reports_unreadable_files(tmp_path: pathlib.Path) -> None:
    inbox = _fixed_path(tmp_path, "entry.md").parent
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "readable.md").write_text(
        "---\nstate: inbox\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (inbox / "invalid.md").write_bytes(b"\xff")
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state)

    response = await app.test_client().get("/api/entries?status=inbox")

    assert response.status_code == 200
    payload = await response.get_json()
    assert [item["filename"] for item in payload["entries"]] == ["readable.md"]
    invalid = inbox / "invalid.md"
    assert payload["warnings"] == [
        {
            "state": "unknown",
            "filename": "invalid.md",
            "path": str(invalid),
            "reason": "UTF-8として読み取れません",
            "next_action": f"{invalid}を別名で複製して元の内容を保全し、内容をUTF-8として保存し直す",
        }
    ]


@pytest.mark.asyncio
async def test_entries_api_returns_identical_pages_while_reusing_unchanged_files(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = _fixed_path(tmp_path, "entry.md").parent
    inbox.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        path = inbox / f"entry-{index}.md"
        path.write_text(f"---\nstate: inbox\ntype: awi\ntarget_repo: example/repo\n---\n\n本文{index}\n", encoding="utf-8")
        os.utime(path, (1_700_000_000, 1_700_000_000))
    client = _serve_app(tmp_path).test_client()
    query = "/api/entries?status=all&page=1"
    first = await (await client.get(query)).get_json()
    built: list[str] = []
    resolved: list[pathlib.Path] = []
    original_entry = wi_operations._entry
    original_resolve = pathlib.Path.resolve

    def counting_entry(path: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> dict[str, object]:
        built.append(path.name)
        return original_entry(path, *args, **kwargs)

    def counting_resolve(self: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> pathlib.Path:
        resolved.append(self)
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(wi_operations, "_entry", counting_entry)
    monkeypatch.setattr(pathlib.Path, "resolve", counting_resolve)

    second = await (await client.get(query)).get_json()

    assert second == first
    assert not built
    assert not [path for path in resolved if path.suffix == ".md"]

    changed = inbox / "entry-1.md"
    changed.write_text("---\nstate: inbox\ntype: awi\ntarget_repo: example/repo\n---\n\n更新後の本文\n", encoding="utf-8")
    third = await (await client.get(query)).get_json()

    by_name = {item["filename"]: item for item in third["entries"]}
    assert by_name["entry-1.md"]["summary"] == "更新後の本文"
    assert built == ["entry-1.md"]
    assert third["pagination"] == first["pagination"]


def test_target_repos_keeps_recent_terminal_values(tmp_path: pathlib.Path) -> None:
    now = datetime.datetime(2026, 9, 13, 12, tzinfo=datetime.UTC)
    _write_repo_entry(tmp_path, "inbox", "a.md", "github.com/x/beta")
    _write_repo_entry(tmp_path, "processing", "b.md", "github.com/x/alpha")
    _write_repo_entry(tmp_path, "processing", "c.md", "github.com/x/beta")
    _write_repo_entry(tmp_path, "adopted", "d.md", "github.com/x/adopted-only")
    _write_repo_entry(tmp_path, "rejected", "e.md", "github.com/x/rejected-only")
    recent = entry_path(tmp_path, "d.md")
    recent.write_text(
        recent.read_text(encoding="utf-8")
        + "\n## 処理結果\n\n````markdown\n## 起草中の節\n````\n- 処理日時: 2026-09-06T12:00:00+00:00\n",
        encoding="utf-8",
    )
    old = entry_path(tmp_path, "e.md")
    old.write_text(
        old.read_text(encoding="utf-8") + "\n## 処理結果\n\n- 処理日時: 2026-09-06T11:59:59+00:00\n", encoding="utf-8"
    )
    _fixed_path(tmp_path, "broken.md").write_text("frontmatterなし\n", encoding="utf-8")
    operations = wi_operations.Operations(tmp_path)
    assert operations.target_repos(now=now) == ["github.com/x/adopted-only", "github.com/x/alpha", "github.com/x/beta"]
    assert operations.target_repos("adopted") == ["github.com/x/adopted-only"]


@pytest.mark.parametrize("entry_type", ["", "type: foo\n", "type: [awi]\n"])
@pytest.mark.asyncio
async def test_typeless_entries_keep_server_running(tmp_path: pathlib.Path, entry_type: str) -> None:
    inbox = _fixed_path(tmp_path, "entry.md").parent
    inbox.mkdir(parents=True, exist_ok=True)
    broken = f"---\nstate: inbox\ntarget_repo: example/repo\n{entry_type}---\n\n欠落の本文\n"
    (inbox / "broken.md").write_text(broken, encoding="utf-8")
    (inbox / "normal.md").write_text(AWI_TEXT, encoding="utf-8")
    client = _serve_app(tmp_path).test_client()
    listed = await client.get("/api/entries?status=inbox&period=all")
    assert listed.status_code == 200
    items = (await listed.get_json())["entries"]
    assert {item["filename"]: item["kind"] for item in items} == {"normal.md": "awi", "broken.md": "unknown"}
    repos = await client.get("/api/repos")
    assert repos.status_code == 200
    detail = await client.get("/api/entries/inbox/broken.md")
    assert detail.status_code == 200
    assert (await detail.get_json())["entry"]["kind"] == "unknown"
    assert (await detail.get_json())["entry"]["content"] == broken
    answered = await client.post("/api/entries/answer", json={"filename": "broken.md", "answer": "回答", "state": "inbox"})
    assert 400 <= answered.status_code < 600
    error = (await answered.get_json())["error"]
    assert "処理に失敗" in error and "標準エラー" in error and "atk serve logs" in error
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/api/entries/inbox/normal.md")).status_code == 200
