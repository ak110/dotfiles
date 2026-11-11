"""serveのappに関するui_searchの試験。"""

# pylint: disable=protected-access

from agent_toolkit._testing.serve_support import _run_node_ui, stale_search_script


def test_create_success_resets_filters_and_keeps_list() -> None:
    """追加成功後は初期状態の条件へ戻し、返却されたファイルを一覧へ残して詳細を開かない。"""
    result = _run_node_ui(
        """
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-content'].value = '新しい本文';
elements['create-target'].value = 'example/repo';
elements['kind-filter'].value = 'awi';
elements['state-filter'].value = 'all';
elements['search-input'].value = '隠す条件';
const listed = {
  kind: 'awi', state: 'inbox', filename: 'new.md', answered: null,
  summary: '新しい本文', target_repo: 'example/repo', frontmatter_entries: []
};
fetchHandler = async (url, options) => {
  if (url.endsWith('/api/entries') && options.method === 'POST') {
    return {ok: true, status: 201, statusText: 'Created', json: async () => ({filenames: ['new.md']})};
  }
  if (url.endsWith('/api/repos?status=all')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})};
  }
  if (url.includes('/api/entries?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [listed], warnings: []})};
  }
  throw new Error('想定外のURL: ' + url);
};
await createEntry({preventDefault() {}});
process.stdout.write(JSON.stringify({
  kind: elements['kind-filter'].value,
  state: elements['state-filter'].value,
  search: elements['search-input'].value,
  detailOpen: elements['detail-dialog'].open,
  current: currentEntry ? currentEntry.filename : null,
  body: elements['detail-content'].innerHTML,
  listCount: elements['entry-list'].children.length,
  createCalls: fetchCalls.filter(call => call.url.endsWith('/api/entries') && call.options.method === 'POST').length
}));
"""
    )
    assert result == {
        "kind": "all",
        "state": "all",
        "search": "",
        "detailOpen": False,
        "current": None,
        "body": "",
        "listCount": 1,
        "createCalls": 1,
    }


def test_assets_keep_choices_when_repos_request_fails() -> None:
    """候補の取得に失敗しても既存の選択肢と選択値を壊さない。"""
    result = _run_node_ui(
        """
fetchHandler = async () => {
  throw new Error('通信失敗');
};
byId('target-filter').value = 'github.com/x/alpha';
    const before = byId('target-filter').children.map(option => option.value);
await loadTargetRepos();
process.stdout.write(JSON.stringify({
      before,
  selected: byId('target-filter').value,
  values: byId('target-filter').children.map(option => option.value),
}));
"""
    )
    assert result["selected"] == "github.com/x/alpha"
    assert result["values"] == result["before"]


def test_assets_keep_latest_list_when_entry_requests_finish_out_of_order() -> None:
    """一覧要求が逆順に完了しても後発要求の行だけを維持する。"""
    result = _run_node_ui(
        """
const resolvers = [];
fetchHandler = async url => {
  if (!url.includes('/api/entries?')) throw new Error('想定外のURL: ' + url);
  return new Promise(resolve => { resolvers.push(resolve); });
};
const first = loadEntries();
const second = loadEntries();
resolvers[1]({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({
    entries: [{kind: 'awi', state: 'inbox', filename: 'new.md', summary: 'new'}],
    warnings: []
  })
});
await second;
resolvers[0]({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({
    entries: [{kind: 'awi', state: 'inbox', filename: 'old.md', summary: 'old'}],
    warnings: []
  })
});
await first;
process.stdout.write(JSON.stringify({
  rows: entries.map(entry => entry.filename),
  loading: elements['entry-list'].attributes['aria-busy']
}));
"""
    )
    assert result == {
        "rows": ["new.md"],
        "loading": "false",
    }


def test_assets_discard_stale_search_error_without_overwriting_global_error() -> None:
    """失効した検索のエラーが後発要求の操作通知を上書きしない。"""
    result = _run_node_ui(
        stale_search_script(
            "let rejectFallback;",
            "new Promise((_resolve, reject) => { rejectFallback = reject; })",
        )
        + """elements['operation-notice'].textContent = '後発要求のエラー';
rejectFallback(new Error('失効した検索エラー'));
await oldRequest;
process.stdout.write(JSON.stringify({
  rows: entries.map(entry => entry.filename),
  state: entries[0]?.state,
  status: elements['result-status'].textContent,
  error: elements['operation-notice'].textContent
}));
"""
    )
    assert result == {
        "rows": ["new.md"],
        "state": "inbox",
        "status": "1件を表示",
        "error": "後発要求のエラー",
    }


def test_assets_ignore_error_from_stale_repo_candidate_request() -> None:
    """失効した候補要求の失敗で最新候補とエラー領域を上書きしない。"""
    result = _run_node_ui(
        """
let rejectAdopted;
fetchHandler = async url => {
  if (url.endsWith('/api/repos?status=adopted')) {
    return new Promise((_resolve, reject) => { rejectAdopted = reject; });
  }
  return {
    ok: true, status: 200, statusText: 'OK',
    json: async () => ({repos: ['active/repo']})
  };
};
elements['state-filter'].value = 'adopted';
const stale = loadTargetRepos();
await Promise.resolve();
elements['state-filter'].value = 'active';
await loadTargetRepos();
rejectAdopted(new Error('旧要求の失敗'));
await stale;
process.stdout.write(JSON.stringify({
  candidates: elements['target-filter'].children.map(option => option.value),
  error: elements['operation-notice-message'].textContent
}));
"""
    )
    assert result == {
        "candidates": ["", "active/repo"],
        "error": "",
    }
