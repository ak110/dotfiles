"""serveのassetsに関するui_searchの試験。"""

# pylint: disable=protected-access

from agent_toolkit._atk.serve import assets
from agent_toolkit._testing.serve_support import _run_node_ui, stale_search_script


def test_assets_render_single_list_warnings() -> None:
    """一覧警告、種別不明と件数通知を検証する。"""
    result = _run_node_ui(
        """
entries = [
  {kind: 'uwi', state: 'inbox', filename: 'u.md', answered: false, summary: '未回答', target_repo: 'x/u'},
  {kind: 'unknown', state: 'inbox', filename: 'x.md', answered: null, summary: '不明', target_repo: 'x/u'},
  {kind: 'awi', state: 'inbox', filename: 'f.md', answered: null, plan: true, summary: '本文',
   target_repo: 'github.com/example/a-very-long-repository-name',
   updated_at: '2026-08-07T10:11:00+00:00'}
];
renderEntries([{state: 'inbox', filename: 'bad.md', reason: 'UTF-8として読み取れません'}], true);
const announced = elements['result-status'].textContent;
const warning = elements['list-warning'].textContent;
  const awiCells = elements['entry-list'].children[2].children[0].children;
  const kindState = awiCells[2].children.map(child => child.textContent);
const targetCell = awiCells[1];
  const summary = awiCells[3].textContent;
elements['result-status'].textContent = '変更しない';
renderEntries([], false);
process.stdout.write(JSON.stringify({
  keys: elements['entry-list'].children.map(item => item.children[0].dataset.key),
  unanswered: elements['entry-list'].children[0].children[0].dataset.unansweredUwi,
  unknownKind: elements['entry-list'].children[1].children[0].dataset.kind,
  count: elements['entry-count'].textContent,
  warning,
  announced,
  kindState,
  targetLabel: targetCell.textContent,
  targetAria: targetCell.attributes['aria-label'],
  rowAria: elements['entry-list'].children[2].children[0].attributes['aria-label'],
  summary,
  sseStatus: elements['result-status'].textContent
}));
"""
    )
    assert result == {
        "keys": ["u.md", "x.md", "f.md"],
        "unanswered": "true",
        "unknownKind": "unknown",
        "count": "3件（未回答UWI 1件）",
        "warning": "［WI / node-test / /notes / inbox/bad.md］一覧から除外したファイル: UTF-8として読み取れません",
        "announced": "3件を表示",
        "kindState": ["awi", "inbox", "plan"],
        "targetLabel": "github.co…itory-name",
        "targetAria": "target-repo: github.com/example/a-very-long-repository-name",
        "rowAria": "f.md、github.com/example/a-very-long-repository-name、awi、inbox、plan、本文",
        "summary": "本文",
        "sseStatus": "変更しない",
    }


def test_assets_show_next_action_for_list_warnings_and_api_errors() -> None:
    """一覧警告は実際の保存先と次の操作を表示し、APIの失敗は理由の後へ次の操作を続けて各操作へ渡す。"""
    result = _run_node_ui(
        """
renderEntries([{
  state: 'unknown', filename: 'bad.md', path: '/notes/2026/10/bad.md',
  reason: 'UTF-8として読み取れません', next_action: '/notes/2026/10/bad.mdをUTF-8として保存し直す'
}], false);
const warning = elements['list-warning'].textContent;
// 全文は折り畳みの中に置き、見出しで警告があることと件数を示す。開閉状態は再描画で変えない。
elements['list-warning-details'].open = true;
renderEntries([
  {state: 'unknown', filename: 'a.md', reason: '理由'},
  {state: 'unknown', filename: 'b.md', reason: '理由'}
], false);
const folded = {
  summary: elements['list-warning-summary'].textContent,
  boxHidden: elements['list-warning-box'].hidden,
  open: elements['list-warning-details'].open,
  inside: elements['list-warning-details'].children.includes(elements['list-warning'])
};
renderEntries([], false);
const cleared = {boxHidden: elements['list-warning-box'].hidden, rows: elements['list-warning'].children.length};
const failures = [
  {error: 'frontmatterを解析できません: a.md', next_action: '形式を直して再投入する'},
  {error: '理由だけの応答'},
  {error: '句点で終わる理由。', next_action: '入力を直す'}
];
const messages = [];
for (const payload of failures) {
  fetchHandler = async () => ({ok: false, status: 400, statusText: 'Bad Request', json: async () => payload});
  try {
    await api('/api/entries/batch', {method: 'POST', body: '{}'});
  } catch (error) {
    messages.push(error.message);
  }
}
process.stdout.write(JSON.stringify({warning, folded, cleared, messages}));
"""
    )
    assert result == {
        "folded": {
            "summary": "一覧から除外したファイルの警告: 2件（開くと理由と次の操作を表示）",
            "boxHidden": False,
            "open": True,
            "inside": True,
        },
        "cleared": {"boxHidden": True, "rows": 0},
        "warning": (
            "［WI / node-test / /notes/2026/10/bad.md］一覧から除外したファイル: UTF-8として読み取れません。"
            "次の操作: /notes/2026/10/bad.mdをUTF-8として保存し直す"
        ),
        "messages": [
            "［WI / node-test / /notes / 一括登録］frontmatterを解析できません: a.md。次の操作: 形式を直して再投入する",
            "［WI / node-test / /notes / 一括登録］理由だけの応答",
            "［WI / node-test / /notes / 一括登録］句点で終わる理由。次の操作: 入力を直す",
        ],
    }


def test_assets_populate_target_repo_choices() -> None:
    """対象リポジトリの候補をフィルターと新規登録欄へ反映する。"""
    assert '<select id="target-filter">' in assets.HTML
    assert '<datalist id="repo-options"></datalist>' in assets.HTML
    result = _run_node_ui(
        """
fetchHandler = async (url) => {
  if (url.endsWith('/api/repos?status=all')) {
    const repos = ['github.com/x/alpha', 'github.com/x/beta'];
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos})};
  }
  throw new Error('想定外のURL: ' + url);
};
await loadTargetRepos();
process.stdout.write(JSON.stringify({
  filterValues: byId('target-filter').children.map(option => option.value),
  filterLabels: byId('target-filter').children.map(option => option.textContent),
  datalistValues: byId('repo-options').children.map(option => option.value),
}));
"""
    )
    assert result["filterValues"] == ["", "github.com/x/alpha", "github.com/x/beta"]
    assert result["filterLabels"][0] == "all"
    assert result["datalistValues"] == ["github.com/x/alpha", "github.com/x/beta"]


def test_assets_load_entries_when_current_repo_candidate_request_fails() -> None:
    """最新の候補取得が失敗しても、現在の状態による一覧更新は継続する。"""
    result = _run_node_ui(
        """
const listUrls = [];
fetchHandler = async url => {
  if (url.includes('/api/repos?')) throw new Error('候補取得失敗');
  if (url.includes('/api/entries?')) {
    listUrls.push(url);
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({
        entries: [{kind: 'awi', state: 'adopted', filename: 'entry.md', summary: '本文'}],
        warnings: []
      })
    };
  }
  throw new Error('想定外のURL: ' + url);
};
elements['state-filter'].value = 'adopted';
await handleFilterChange({reloadRepos: true});
process.stdout.write(JSON.stringify({
  listUrls,
  rows: entries.map(entry => entry.filename),
  error: elements['operation-notice-message'].textContent
}));
"""
    )
    assert result == {
        "listUrls": ["/atk/api/entries?type=all&status=adopted&answered=all&period=all&page=1"],
        "rows": ["entry.md"],
        "error": "［WI / node-test / /notes / 対象リポジトリ一覧取得］候補取得失敗",
    }


def test_assets_discard_stale_search_response() -> None:
    """後発の一覧要求が完了した後に旧応答が到着しても表示を上書きしない。"""
    result = _run_node_ui(
        stale_search_script(
            "let resolveSearch;",
            "new Promise(resolve => { resolveSearch = resolve; })",
        )
        + """resolveSearch({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entries: [{kind: 'awi', state: 'adopted', filename: 'old.md', summary: 'old'}], warnings: []})
});
await oldRequest;
process.stdout.write(JSON.stringify({
  rows: entries.map(entry => entry.filename),
  state: entries[0]?.state,
  status: elements['result-status'].textContent
}));
"""
    )
    assert result == {
        "rows": ["new.md"],
        "state": "inbox",
        "status": "1件を表示",
    }


def test_assets_search_modes_share_effective_conditions_and_preserve_missing_repo() -> None:
    """通常と検索の一覧・ページ・コピーが実効条件を共有し、候補外の手動選択も保持する。"""
    result = _run_node_ui(
        """
const copied = [];
Object.defineProperty(globalThis, 'navigator', {configurable: true, value: {
  clipboard: {writeText: async text => copied.push(text)}
}});
fetchHandler = async url => ({ok: true, status: 200, json: async () => url.includes('/api/repos?')
  ? {repos: ['other/repo']}
  : {entries: [{kind: 'uwi', state: 'adopted', filename: 'found.md', summary: '検索結果'}],
     pagination: {page: Number(new URL(url, 'http://localhost').searchParams.get('page')) || 1,
       page_count: 2, page_size: 100, total_count: 101}, warnings: []}
});
elements['search-input'].value = 'keyword';
for (const [id, value] of Object.entries({
  'kind-filter': 'uwi', 'state-filter': 'adopted', 'period-filter': '8w', 'answer-filter': 'yes',
  'important-filter': 'yes', 'target-filter': 'missing/repo', 'source-filter': 'human'
})) elements[id].value = value;
await loadEntries();
await copyAllEntries();
const normalUrls = fetchCalls.map(call => call.url);
fetchCalls.length = 0;
await handleFilterChange();
await movePage(1);
await copyAllEntries();
const searchUrls = fetchCalls.map(call => call.url);
await loadTargetRepos();
const retainedTarget = elements['target-filter'].value;
const candidates = elements['target-filter'].children.map(option => option.value);
await handleFilterChange({mode: 'normal'});
const retained = [elements['search-input'].value, elements['state-filter'].value, elements['period-filter'].value];
await clearFilters();
process.stdout.write(JSON.stringify({normalUrls, searchUrls, retainedTarget, candidates, retained,
  mode: displayMode, reset: [elements['state-filter'].value, elements['period-filter'].value, elements['target-filter'].value],
  copied}));
"""
    )
    normal = "/atk/api/entries?type=all&status=active&answered=all&period=2w"
    search = (
        "/atk/api/entries?type=uwi&status=adopted&answered=yes&period=8w&important=yes"
        "&target_repo=missing%2Frepo&source_kind=human&q=keyword"
    )
    assert result == {
        "normalUrls": [normal + "&page=1", normal],
        "searchUrls": [search + "&page=1", search + "&page=2", search],
        "retainedTarget": "missing/repo",
        "candidates": ["", "other/repo", "missing/repo"],
        "retained": ["keyword", "adopted", "8w"],
        "mode": "normal",
        "reset": ["all", "all", ""],
        "copied": ["found.md 検索結果", "found.md 検索結果"],
    }


def test_assets_each_filter_enters_search_without_changing_other_manual_values() -> None:
    """各フィルターの変更は検索へ切り替え、type=awiでもansweredを含む手動値を維持する。"""
    result = _run_node_ui(
        """
bindEvents();
const values = {'kind-filter': 'awi', 'state-filter': 'adopted', 'period-filter': '4w',
  'answer-filter': 'yes', 'important-filter': 'no', 'target-filter': 'chosen/repo', 'source-filter': 'human'};
const results = [];
for (const id of Object.keys(values)) {
  for (const [field, value] of Object.entries(values)) elements[field].value = value;
  elements['search-input'].value = 'all';
  setDisplayMode('normal');
  currentPage = 3;
  elements[id].listeners.change();
  await new Promise(resolve => setImmediate(resolve));
  results.push({id, mode: displayMode, page: currentPage,
    values: Object.fromEntries(Object.keys(values).map(field => [field, elements[field].value])),
    answeredDisabled: elements['answer-filter'].disabled,
    conditions: elements['wi-effective-conditions'].textContent,
    query: fetchCalls.filter(call => call.url.includes('/api/entries?')).at(-1).url});
}
process.stdout.write(JSON.stringify({results}));
"""
    )
    values = {
        "kind-filter": "awi",
        "state-filter": "adopted",
        "period-filter": "4w",
        "answer-filter": "yes",
        "important-filter": "no",
        "target-filter": "chosen/repo",
        "source-filter": "human",
    }
    assert [item["id"] for item in result["results"]] == list(values)
    for item in result["results"]:
        assert item["mode"] == "search"
        assert item["page"] == 1
        assert item["values"] == values
        assert item["answeredDisabled"] is False
        assert "検索=all" in item["conditions"]
        assert item["query"] == (
            "/atk/api/entries?type=awi&status=adopted&answered=yes&period=4w&important=no"
            "&target_repo=chosen%2Frepo&source_kind=human&q=all&page=1"
        )


def test_assets_keep_latest_repo_selection_when_stale_sse_candidates_finish() -> None:
    """SSEを契機とした古い候補の応答が後から届いても、最新の対象リポジトリの選択と一覧条件を維持する。"""
    result = _run_node_ui(
        """
let resolveActive;
const listUrls = [];
fetchHandler = async url => {
  if (url.endsWith('/api/repos?status=all')) {
    return new Promise(resolve => { resolveActive = resolve; });
  }
  if (url.endsWith('/api/repos?status=adopted')) {
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({repos: ['adopted/repo']})
    };
  }
  if (url.includes('/api/entries?')) {
    listUrls.push(url);
    const selected = url.includes('target_repo=adopted%2Frepo');
    const matching = {kind: 'awi', state: 'adopted', filename: 'selected.md', summary: 'selected'};
    const other = {kind: 'awi', state: 'adopted', filename: 'other.md', summary: 'other'};
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({entries: selected ? [matching] : [matching, other], warnings: []})
    };
  }
  throw new Error('想定外のURL: ' + url);
};
const staleSse = reloadFromExternalChange();
await Promise.resolve();
elements['state-filter'].value = 'adopted';
await handleFilterChange({reloadRepos: true});
elements['target-filter'].value = 'adopted/repo';
await handleFilterChange();
resolveActive({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({repos: ['active/old']})
});
await staleSse;
process.stdout.write(JSON.stringify({
  state: elements['state-filter'].value,
  candidates: elements['target-filter'].children.map(option => option.value),
  selected: elements['target-filter'].value,
  lastListUrl: listUrls.at(-1),
  rows: entries.map(entry => entry.filename)
}));
"""
    )
    assert result == {
        "state": "adopted",
        "candidates": ["", "adopted/repo"],
        "selected": "adopted/repo",
        "lastListUrl": ("/atk/api/entries?type=all&status=adopted&answered=all&period=all&target_repo=adopted%2Frepo&page=1"),
        "rows": ["selected.md"],
    }
