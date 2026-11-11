"""serveのcliに関するui_searchの試験。"""

# pylint: disable=protected-access

from agent_toolkit._testing.serve_support import _run_node_ui


def test_assets_search_keeps_empty_results_with_manual_conditions() -> None:
    """手動条件で0件なら、その条件と警告を保ち、条件外への補助取得を発行しない。"""
    result = _run_node_ui(
        """
elements['search-input'].value = 'terminal';
elements['state-filter'].value = 'active';
elements['period-filter'].value = '4w';
fetchHandler = async url => {
  if (url !== '/atk/api/entries?type=all&status=active&answered=all&period=4w&q=terminal&page=1') {
    throw new Error('条件外の取得: ' + url);
  }
  return {ok: true, status: 200, json: async () => ({entries: [],
    warnings: [{state: 'inbox', filename: 'invalid.md', reason: '読取失敗'}]})};
};
await handleFilterChange();
process.stdout.write(JSON.stringify({rows: entries, urls: fetchCalls.map(call => call.url),
  state: elements['state-filter'].value, period: elements['period-filter'].value,
  warning: elements['list-warning'].textContent, error: elements['operation-notice-message'].textContent,
  status: elements['result-status'].textContent, mode: displayMode}));
"""
    )
    assert not result["rows"]
    assert result["urls"] == ["/atk/api/entries?type=all&status=active&answered=all&period=4w&q=terminal&page=1"]
    assert result["state"] == "active"
    assert result["period"] == "4w"
    assert result["warning"] == "［WI / node-test / /notes / inbox/invalid.md］一覧から除外したファイル: 読取失敗"
    assert not result["error"]
    assert result["status"] == "一致する項目はありません"
    assert result["mode"] == "search"
