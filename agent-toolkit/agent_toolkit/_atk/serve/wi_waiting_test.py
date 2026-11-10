"""判断待ち一覧APIと連続回答画面の結合テスト。"""

import pathlib

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state
from agent_toolkit._atk.wi import uwi
from agent_toolkit._testing import wi_entry_files
from agent_toolkit._testing.serve_support import _disable_wi_git, _run_node_ui


@pytest.mark.asyncio
async def test_waiting_api_selects_active_unanswered_across_pages_and_repos(tmp_path: pathlib.Path) -> None:
    """判断待ちは期間や検索条件から独立し、重要度内で古い順に全ページを返す。"""
    names = [f"20200101-000000-{index:03d}.md" for index in range(101)]
    for index, name in enumerate(names):
        wi_entry_files.write_uwi(
            tmp_path,
            name,
            state=("inbox", "processing", "hold")[index % 3],
            target_repo=f"github.com/example/repo-{index % 2}",
        )
    important = wi_entry_files.entry_path(tmp_path, names[-1])
    important.write_text(
        important.read_text(encoding="utf-8").replace("type: uwi", "type: uwi\nimportant: true"), encoding="utf-8"
    )
    wi_entry_files.write_uwi(tmp_path, "answered.md", answer="保存済み")
    wi_entry_files.write_uwi(tmp_path, "adopted.md", state="adopted")
    wi_entry_files.write_uwi(tmp_path, "rejected.md", state="rejected")
    wi_entry_files.write_awi(tmp_path, "awi.md")
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    first = await client.get("/api/entries?view=waiting&page=1&type=awi&period=2w&status=rejected&q=absent")
    assert first.status_code == 200
    payload = await first.get_json()
    assert [item["filename"] for item in payload["entries"]] == [names[-1], *names[:99]]
    assert payload["pagination"] == {"page": 1, "page_size": 100, "page_count": 2, "total_count": 101}
    second = await (await client.get("/api/entries?view=waiting&page=2")).get_json()
    assert [item["filename"] for item in second["entries"]] == [names[99]]
    limited = await (await client.get("/api/entries?view=waiting&target_repo=github.com/example/repo-1")).get_json()
    assert [item["filename"] for item in limited["entries"]] == names[1::2]
    path = wi_entry_files.entry_path(tmp_path, names[0])
    path.write_text(path.read_text(encoding="utf-8") + uwi.ANSWER_MARKER + "\n回答\n", encoding="utf-8")
    wi_entry_files.entry_path(tmp_path, names[1]).unlink()
    moved = wi_entry_files.entry_path(tmp_path, names[2])
    moved.write_text(moved.read_text(encoding="utf-8").replace("state: hold", "state: adopted"), encoding="utf-8")
    updated = await (await client.get("/api/entries?view=waiting")).get_json()
    assert [item["filename"] for item in updated["entries"]] == [names[-1], *names[3:-1]]
    invalid = await client.get("/api/entries?view=unknown")
    assert invalid.status_code == 400


@pytest.mark.asyncio
async def test_waiting_answer_api_keeps_dependent_awi_held(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """holdの回答を保存して候補から除き、依存するAWIの状態を独自に再開しない。"""
    _disable_wi_git(monkeypatch)
    wi_entry_files.write_uwi(tmp_path, "held.md", state="hold")
    path = wi_entry_files.entry_path(tmp_path, "held.md")
    content = path.read_text(encoding="utf-8") + uwi.ANSWER_MARKER + "\n"
    path.write_text(content, encoding="utf-8")
    dependent = wi_entry_files.write_awi(tmp_path, "dependent.md", state="hold", depends_on=("held.md",))
    before = dependent.read_text(encoding="utf-8")
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    response = await client.post(
        "/api/entries/answer",
        json={"filename": "held.md", "state": "hold", "answer": "この方針で進める", "expected_content": content},
    )
    assert response.status_code == 200
    assert await response.get_json() == {"changed": True, "state": "hold"}
    assert path.read_text(encoding="utf-8").endswith(uwi.ANSWER_MARKER + "\nこの方針で進める\n")
    remaining = await (await client.get("/api/entries?view=waiting&page=1")).get_json()
    assert not remaining["entries"]
    assert remaining["pagination"]["total_count"] == 0
    assert dependent.read_text(encoding="utf-8") == before


def test_waiting_controls_preserve_search_and_advance_after_success_only() -> None:
    """選択は未送信、失敗・二重クリックは同じ入力を保ち、保存成功だけ次項目へ進む。"""
    result = _run_node_ui("""
bindEvents();
elements['search-input'].value = '検索入力';
elements['state-filter'].value = 'hold';
elements['target-filter'].value = 'example/search';
setDisplayMode('waiting');
elements['waiting-target-filter'].value = 'example/waiting';
const query = buildQuery().toString();
const first = {kind: 'uwi', state: 'hold', filename: 'a.md', important: true, answered: false,
  content: '原文a', frontmatter_entries: [], question_type: 'yes-no', answer: ''};
const last = {...first, filename: 'z.md', important: false, content: '原文z'};
entries = [first, last];
pagination.total_count = 2;
displayEntry(first);
openDetailDialog(null);
elements['answer-choices'].children[0].listeners.click();
const selected = {value: elements['answer-input'].value, calls: fetchCalls.length};
let release;
let fail = true;
fetchHandler = async (url, options) => {
  if (options.method === 'POST') {
    await new Promise(resolve => { release = resolve; });
    return {ok: !fail, status: fail ? 409 : 200, json: async () => fail
      ? {error: '保存失敗', code: 'lock_conflict'} : {changed: true, state: 'hold'}};
  }
  if (url.endsWith('/z.md')) return {ok: true, json: async () => ({entry: last})};
  return {ok: true, json: async () => ({entries: [last], warnings: [],
    pagination: {page: 1, page_size: 100, page_count: 1, total_count: 1}})};
};
const saving = saveAnswer({next: true});
await Promise.resolve(); await Promise.resolve();
await saveAnswer({next: true});
release(); await saving;
const failed = {filename: currentEntry.filename, input: elements['answer-input'].value,
  posts: fetchCalls.filter(call => call.options.method === 'POST').length};
fail = false;
const success = saveAnswer({next: true});
await Promise.resolve(); await Promise.resolve();
release(); await success;
const advanced = {filename: currentEntry.filename, input: elements['answer-input'].value,
  progress: elements['waiting-progress'].textContent};
await skipWaitingAnswer();
setDisplayMode('search');
process.stdout.write(JSON.stringify({query, selected, failed, advanced,
  closed: !elements['detail-dialog'].open, search: buildQuery().toString(),
  bodies: fetchCalls.filter(call => call.options.method === 'POST').map(call => JSON.parse(call.options.body))}));
""")
    assert result["query"] == "view=waiting&page=1&target_repo=example%2Fwaiting"
    assert result["selected"] == {"value": "はい", "calls": 0}
    assert result["failed"] == {"filename": "a.md", "input": "はい", "posts": 1}
    assert result["advanced"] == {"filename": "z.md", "input": "", "progress": "現在 1 / 1件・未回答 残り1件"}
    assert result["closed"] is True
    assert "q=%E6%A4%9C%E7%B4%A2%E5%85%A5%E5%8A%9B" in result["search"]
    assert "status=hold" in result["search"]
    assert "target_repo=example%2Fsearch" in result["search"]
    assert result["bodies"] == [
        {"filename": "a.md", "state": "hold", "answer": "はい", "expected_content": "原文a"},
        {"filename": "a.md", "state": "hold", "answer": "はい", "expected_content": "原文a"},
    ]


@pytest.mark.parametrize("code", ["edit_conflict", "sync_incomplete"])
def test_waiting_failure_preserves_input_and_current_item(code: str) -> None:
    """競合とローカル保存後のcommit失敗は入力を保ち、次の項目へ送らない。"""
    result = _run_node_ui(
        """
setDisplayMode('waiting');
displayEntry({kind: 'uwi', state: 'processing', filename: 'a.md', important: false, answered: false,
  content: '原文', frontmatter_entries: [], question_type: 'free-form', answer: ''});
openDetailDialog(null);
elements['answer-input'].value = '入力中';
fetchHandler = async () => ({ok: false, status: 409, json: async () => ({error: '保存失敗', code: CODE})});
await saveAnswer({next: true});
process.stdout.write(JSON.stringify({filename: currentEntry.filename, input: elements['answer-input'].value,
  open: elements['detail-dialog'].open, disabled: elements['save-next-answer-button'].disabled,
  skip: elements['skip-answer-button'].disabled, requests: fetchCalls.length}));
""".replace("CODE", repr(code))
    )
    assert result == {"filename": "a.md", "input": "入力中", "open": True, "disabled": True, "skip": False, "requests": 1}


def test_waiting_next_ignores_stale_list_and_changed_candidates() -> None:
    """古い一覧応答と候補の回答済み化・削除・終了状態への移動が次対象を誤らせない。"""
    result = _run_node_ui("""
setDisplayMode('waiting');
const entry = name => ({kind: 'uwi', state: 'inbox', filename: name, important: false, answered: false,
  content: name, frontmatter_entries: [], question_type: 'free-form', answer: ''});
const first = entry('a.md');
displayEntry(first); openDetailDialog(null);
let oldList;
fetchHandler = async () => new Promise(resolve => { oldList = resolve; });
const stale = loadEntries();
fetchHandler = async url => {
  if (url.endsWith('/b.md')) return {ok: true, json: async () => ({entry: {...entry('b.md'), answered: true}})};
  if (url.endsWith('/c.md')) return {ok: false, status: 404, json: async () => ({error: '消失'})};
  if (url.endsWith('/d.md')) return {ok: true, json: async () => ({entry: {...entry('d.md'), state: 'adopted'}})};
  if (url.endsWith('/z.md')) return {ok: true, json: async () => ({entry: entry('z.md')})};
  return {ok: true, json: async () => ({entries: ['a.md', 'b.md', 'c.md', 'd.md', 'z.md'].map(entry),
    warnings: [], pagination: {page: 1, page_size: 100, page_count: 1, total_count: 5}})};
};
await skipWaitingAnswer();
oldList({ok: true, json: async () => ({entries: [first], warnings: [],
  pagination: {page: 1, page_size: 100, page_count: 1, total_count: 1}})});
await stale;
process.stdout.write(JSON.stringify({filename: currentEntry.filename, keys: entries.map(item => item.filename),
  pending: userListOperationPending || listRequestPending}));
""")
    assert result == {"filename": "z.md", "keys": ["a.md", "b.md", "c.md", "d.md", "z.md"], "pending": False}


@pytest.mark.parametrize("change", ["answered", "moved"])
def test_waiting_external_change_keeps_typed_answer_and_allows_skip(change: str) -> None:
    """現在の回答が外部で対象外になっても入力を保持し、保存を止めて見送れる。"""
    result = _run_node_ui(
        """
setDisplayMode('waiting');
const first = {kind: 'uwi', state: 'hold', filename: 'a.md', important: false, answered: false,
  content: '原文', frontmatter_entries: [], question_type: 'free-form', answer: ''};
fetchHandler = async () => ({ok: true, json: async () => ({entry: first})});
await selectEntry(first);
elements['answer-input'].value = '編集中';
fetchHandler = async () => ({ok: true, json: async () => ({entry: {...first, content: '外部本文',
  state: CHANGE === 'moved' ? 'adopted' : 'hold', answered: CHANGE === 'answered', answer: '他の回答'}})});
await reloadOpenDetailFromExternalChange();
process.stdout.write(JSON.stringify({filename: currentEntry.filename, input: elements['answer-input'].value,
  open: elements['detail-dialog'].open, hidden: elements['answer-panel'].hidden,
  disabled: elements['save-next-answer-button'].disabled, skip: elements['skip-answer-button'].disabled}));
""".replace("CHANGE", repr(change))
    )
    assert result == {"filename": "a.md", "input": "編集中", "open": True, "hidden": False, "disabled": True, "skip": False}


@pytest.mark.parametrize("mode", ["waiting", "normal", "search"])
@pytest.mark.parametrize("typed", ["", "編集中"])
def test_external_deletion_keeps_waiting_detail_until_explicit_skip(mode: str, typed: str) -> None:
    """判断待ちは未入力でも削除された現在項目を保持し、通常/検索は従来どおり閉じる。"""
    result = _run_node_ui(
        """
setDisplayMode(MODE);
const first = {kind: 'uwi', state: 'hold', filename: 'a.md', important: false, answered: false,
  content: '質問の原文', frontmatter_entries: [], question_type: 'free-form', answer: ''};
const next = {...first, filename: 'z.md', content: '次の質問'};
fetchHandler = async () => ({ok: true, json: async () => ({entry: first})});
await selectEntry(first);
elements['answer-input'].value = TYPED;
fetchHandler = async () => ({ok: false, status: 404, json: async () => ({error: '消失'})});
await reloadOpenDetailFromExternalChange();
const before = {filename: currentEntry?.filename || null, content: currentEntry?.content || null,
  input: elements['answer-input'].value, open: elements['detail-dialog'].open,
  saveDisabled: elements['save-next-answer-button'].disabled, skipDisabled: elements['skip-answer-button'].disabled};
const beforeSkipRequests = fetchCalls.length;
if (MODE === 'waiting') {
  await saveAnswer({next: true});
  fetchHandler = async url => ({ok: true, json: async () => url.endsWith('/z.md')
    ? {entry: next} : {entries: [next], warnings: [],
      pagination: {page: 1, page_size: 100, page_count: 1, total_count: 1}}});
  await skipWaitingAnswer();
}
process.stdout.write(JSON.stringify({before, beforeSkipRequests,
  mutations: fetchCalls.filter(call => call.options.method === 'POST' || call.options.method === 'PUT').length,
  after: {filename: currentEntry?.filename || null, input: elements['answer-input'].value,
    open: elements['detail-dialog'].open}}));
""".replace("MODE", repr(mode)).replace("TYPED", repr(typed))
    )
    assert result["beforeSkipRequests"] == 2
    assert result["mutations"] == 0
    if mode == "waiting":
        assert result["before"] == {
            "filename": "a.md",
            "content": "質問の原文",
            "input": typed,
            "open": True,
            "saveDisabled": True,
            "skipDisabled": False,
        }
        assert result["after"] == {"filename": "z.md", "input": "", "open": True}
    else:
        assert result["before"]["filename"] is None
        assert result["before"]["open"] is False
        assert result["after"]["filename"] is None
        assert result["after"]["open"] is False
