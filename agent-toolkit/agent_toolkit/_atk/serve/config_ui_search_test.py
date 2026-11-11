"""serveのconfigに関するui_searchの試験。"""

# pylint: disable=protected-access

from agent_toolkit._testing.serve_support import _run_node_ui


def test_assets_ignore_out_of_order_repo_candidates_for_state_changes() -> None:
    """候補要求が逆順に完了しても、最新の状態・候補・一覧を維持する。"""
    result = _run_node_ui(
        """
let resolveAdopted;
const listUrls = [];
fetchHandler = async url => {
  if (url.endsWith('/api/repos?status=adopted')) {
    return new Promise(resolve => { resolveAdopted = resolve; });
  }
  if (url.endsWith('/api/repos?status=active')) {
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({repos: ['active/repo']})
    };
  }
  if (url.includes('/api/entries?')) {
    listUrls.push(url);
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({
        entries: [{kind: 'awi', state: 'inbox', filename: 'active.md', summary: 'active'}],
        warnings: []
      })
    };
  }
  throw new Error('想定外のURL: ' + url);
};
elements['state-filter'].value = 'adopted';
const stale = handleFilterChange({reloadRepos: true});
await Promise.resolve();
elements['state-filter'].value = 'active';
await handleFilterChange({reloadRepos: true});
resolveAdopted({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({repos: ['adopted/repo']})
});
await stale;
process.stdout.write(JSON.stringify({
  state: elements['state-filter'].value,
  candidates: elements['target-filter'].children.map(option => option.value),
  listUrls,
  rows: entries.map(entry => entry.filename)
}));
"""
    )
    assert result == {
        "state": "active",
        "candidates": ["", "active/repo"],
        "listUrls": [
            "/atk/api/entries?type=all&status=adopted&answered=all&period=all&page=1",
            "/atk/api/entries?type=all&status=active&answered=all&period=all&page=1",
        ],
        "rows": ["active.md"],
    }
