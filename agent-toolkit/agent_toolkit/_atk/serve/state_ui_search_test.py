"""serveのstateに関するui_searchの試験。"""

# pylint: disable=protected-access

from agent_toolkit._testing.serve_support import _run_node_ui


def test_assets_preserve_selected_target_repo_when_absent_from_choices() -> None:
    """候補から消えた対象リポジトリも選択肢と選択状態を保持する。"""
    result = _run_node_ui(
        """
fetchHandler = async (url) => {
  if (url.endsWith('/api/repos?status=all')) {
    const repos = ['github.com/x/alpha'];
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos})};
  }
  throw new Error('想定外のURL: ' + url);
};
byId('target-filter').value = 'github.com/x/removed';
await loadTargetRepos();
process.stdout.write(JSON.stringify({
  values: byId('target-filter').children.map(option => option.value),
  selected: byId('target-filter').value,
}));
"""
    )
    assert result["values"] == ["", "github.com/x/alpha", "github.com/x/removed"]
    assert result["selected"] == "github.com/x/removed"


def test_assets_search_failure_keeps_initial_empty_result() -> None:
    """検索が失敗しても初回の空一覧とエラー表示を維持する。"""
    result = _run_node_ui(
        """
bindEvents();
await loadEntries({announce: true});
setDisplayMode('search');
elements['search-input'].value = '失敗する検索';
fetchHandler = async url => {
  if (url.includes('status=active')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
  }
  return {ok: false, status: 503, statusText: 'Unavailable', json: async () => ({error: '検索に失敗'})};
};
await loadEntries({announce: true});
const shown = {
  message: elements['operation-notice-message'].textContent,
  hidden: elements['operation-notice'].hidden
};
elements['operation-notice-close-button'].focus();
elements['operation-notice-close-button'].listeners.click();
process.stdout.write(JSON.stringify({
  rows: entries.map(entry => entry.filename),
  status: elements['result-status'].textContent,
  shown,
  cleared: {
    message: elements['operation-notice-message'].textContent,
    hidden: elements['operation-notice'].hidden,
    focused
  }
}));
"""
    )
    assert result == {
        "rows": [],
        "status": "一致する項目はありません",
        "shown": {"message": "［WI / node-test / /notes / 一覧取得］検索に失敗", "hidden": False},
        "cleared": {
            "message": "［WI / node-test / /notes / 一覧取得］検索に失敗",
            "hidden": True,
            "focused": "refresh-button",
        },
    }


def test_assets_keep_user_filter_load_when_same_state_sse_supersedes_repo_request() -> None:
    """エンドユーザーの候補要求中に届いたSSEを保留し、一覧通知を維持する。"""
    result = _run_node_ui(
        """
const repoResolvers = [];
const listUrls = [];
fetchHandler = async url => {
  if (url.includes('/api/repos?')) return new Promise(resolve => { repoResolvers.push(resolve); });
  if (url.includes('/api/entries?')) {
    listUrls.push(url);
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({
        entries: [{kind: 'awi', state: 'inbox', filename: 'filtered.md', summary: 'filtered'}],
        warnings: []
      })
    };
  }
  throw new Error('想定外のURL: ' + url);
};
elements['kind-filter'].value = 'awi';
elements['result-status'].textContent = '変更前の通知';
const user = handleFilterChange({reloadRepos: true});
await Promise.resolve();
const sse = reloadFromExternalChange();
await Promise.resolve();
const repoCountDuringUser = repoResolvers.length;
repoResolvers[0]({
  ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})
});
await Promise.all([user, sse]);
repoResolvers[1]({
  ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})
});
await new Promise(resolve => setImmediate(resolve));
process.stdout.write(JSON.stringify({
  repoCountDuringUser,
  listUrls,
  status: elements['result-status'].textContent,
  rows: entries.map(entry => entry.filename)
}));
"""
    )
    assert result == {
        "repoCountDuringUser": 1,
        "listUrls": [
            "/atk/api/entries?type=awi&status=all&answered=all&period=all&page=1",
            "/atk/api/entries?type=all&status=active&answered=all",
            "/atk/api/entries?type=all&status=active&answered=all",
            "/atk/api/entries?type=awi&status=all&answered=all&period=all&page=1",
        ],
        "status": "1件を表示",
        "rows": ["filtered.md"],
    }
