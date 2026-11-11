"""serveのassetsに関するui_detailの試験。"""

# pylint: disable=protected-access

from agent_toolkit._testing.serve_support import _run_node_ui


def test_assets_render_all_frontmatter_without_repeating_detail_badges() -> None:
    """詳細メタデータは種別・状態を除き、任意の入れ子値を構造付きで表示する。"""
    result = _run_node_ui(
        """
displayEntry({
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  target_repo: 'legacy/repo', source: 'legacy',
  frontmatter_entries: [
    {key: {type: 'str', value: 'type'}, value: 'awi'},
    {key: {type: 'str', value: 'target_repo'}, value: 'example/repo'},
    {key: {type: 'str', value: 'source'}, value: 'web'},
    {key: {type: 'str', value: 'priority'}, value: 'high'},
    {key: {type: 'int', value: 1}, value: 'numeric'},
    {key: {type: 'str', value: '1'}, value: 'textual'},
    {key: {type: 'str', value: 'nested'}, value: {branch: 'main', flags: ['x']}},
    {key: {type: 'str', value: 'values'}, value: ['one', {enabled: true}]},
  ], body_html: '<p>本文</p>'
});
const items = elements['detail-metadata'].children.map(item => ({
  label: item.children[0].textContent,
  value: item.children[1].textContent,
  className: item.className
}));
process.stdout.write(JSON.stringify({items, heading: elements['detail-state'].textContent}));
"""
    )
    assert result["heading"] == "awi / inbox"
    assert [item["label"] for item in result["items"]] == [
        "target_repo",
        "source",
        "priority",
        "int: 1",
        "1",
        "nested",
        "values",
        "updated_at",
    ]
    assert [item["className"] for item in result["items"]] == ["metadata-item"] * 8
    assert result["items"][0]["value"] == "example/repo"
    assert result["items"][1]["value"] == "web"
    assert result["items"][2]["value"] == "high"
    assert result["items"][3]["value"] == "numeric"
    assert result["items"][4]["value"] == "textual"
    assert result["items"][5]["value"] == '{\n  "branch": "main",\n  "flags": [\n    "x"\n  ]\n}'
    assert result["items"][6]["value"] == '[\n  "one",\n  {\n    "enabled": true\n  }\n]'
    assert all("[object Object]" not in item["value"] for item in result["items"])


def test_assets_sse_detail_tracks_state_and_discards_stale_response() -> None:
    """SSEを受けた詳細の再取得は状態の移動を追跡し、別の項目を選んだ後に届いた古い応答を破棄する。"""
    result = _run_node_ui(
        """
const inbox = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  content: '移動前本文', body_html: '<p>移動前本文</p>', frontmatter_entries: []
};
const processing = {
  ...inbox, state: 'processing', content: '移動後本文', body_html: '<p>移動後本文</p>'
};
const second = {
  kind: 'awi', state: 'inbox', filename: 'second.md', answered: null,
  content: '別項目本文', body_html: '<p>別項目本文</p>', frontmatter_entries: []
};
displayEntry(inbox);
detailOriginKey = entryKey(inbox);
elements['detail-dialog'].open = true;
entries = [processing];
fetchHandler = async url => ({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entry: url.endsWith('/entry.md') ? processing : second})
});
await reloadOpenDetailFromExternalChange();
const moved = {
  state: currentEntry.state,
  content: elements['detail-content'].innerHTML,
  key: detailOriginKey
};

let resolveStale;
let markStaleStarted;
const staleStarted = new Promise(resolve => { markStaleStarted = resolve; });
fetchHandler = async url => {
  if (url.endsWith('/entry.md')) return new Promise(resolve => {
    resolveStale = resolve;
    markStaleStarted();
  });
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: second})};
};
const stale = reloadOpenDetailFromExternalChange();
await staleStarted;
await selectEntry(second, new Element('second-origin', 'BUTTON'));
resolveStale({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entry: {...processing, content: '遅延本文', body_html: '<p>遅延本文</p>'}})
});
await stale;
process.stdout.write(JSON.stringify({
  moved,
  final: {filename: currentEntry.filename, content: elements['detail-content'].innerHTML, key: detailOriginKey}
}));
"""
    )
    assert result == {
        "moved": {
            "state": "processing",
            "content": "<p>移動後本文</p>",
            "key": "entry.md",
        },
        "final": {
            "filename": "second.md",
            "content": "<p>別項目本文</p>",
            "key": "second.md",
        },
    }


def test_assets_preserve_external_update_recovery_across_operation_failures() -> None:
    """外部更新後の一般失敗と競合は復旧手順を保ち、単独の権限拒否は再試行できる。"""
    result = _run_node_ui(
        """
async function exercise(kind, status, code, withExternalUpdate) {
  const answering = kind === 'answer';
  let serverEntry = {
    kind: answering ? 'uwi' : 'awi', state: 'inbox',
    filename: answering ? 'question.md' : 'entry.md', answered: answering ? false : null,
    summary: '操作対象', content: '更新前', body_html: '<p>更新前</p>',
    question_type: 'free-form', choices: [], frontmatter_entries: []
  };
  displayEntry(serverEntry);
  detailOriginKey = entryKey(serverEntry);
  openDialog(elements['detail-dialog'], new Element(`${kind}-origin`, 'BUTTON'), elements['detail-dialog-body']);
  if (answering) {
    elements['answer-input'].value = 'ユーザーの回答';
  } else {
    enterEdit();
    elements['edit-content'].value = 'ユーザーの保存本文';
  }
  let resolveMutation;
  let markStarted;
  const started = new Promise(resolve => { markStarted = resolve; });
  fetchHandler = async (url, options) => {
    const isMutation = answering
      ? url.endsWith('/api/entries/answer') && options.method === 'POST'
      : options.method === 'PUT';
    if (isMutation) return new Promise(resolve => {
      resolveMutation = resolve;
      markStarted();
    });
    if (url.includes('/api/entries?')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [serverEntry], warnings: []})};
    }
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: serverEntry})};
  };
  const pending = answering ? saveAnswer() : saveEntry();
  await started;
  if (withExternalUpdate) {
    serverEntry = {...serverEntry, content: '外部更新後', body_html: '<p>外部更新後</p>'};
    await reloadOpenDetailFromExternalChange();
  }
  const payload = {error: status === 403 ? '権限がありません' : '一般失敗'};
  if (code) payload.code = code;
  resolveMutation({ok: false, status, statusText: 'Error', json: async () => payload});
  await pending;
  const button = elements[answering ? 'save-answer-button' : 'save-entry-button'];
  const input = elements[answering ? 'answer-input' : 'edit-content'];
  const outcome = {
    alert: elements['detail-alert'].textContent,
    disabled: button.disabled,
    input: input.value,
    mode: currentDetailMode()
  };
  closeDetailDialog();
  return outcome;
}

const saveGeneral = await exercise('save', 500, null, true);
const answerGeneral = await exercise('answer', 500, null, true);
const saveConflict = await exercise('save', 409, 'edit_conflict', false);
const answerConflict = await exercise('answer', 409, 'edit_conflict', false);
const savePermission = await exercise('save', 403, null, false);
const answerPermission = await exercise('answer', 403, null, false);
process.stdout.write(JSON.stringify({
  saveGeneral, answerGeneral, saveConflict, answerConflict, savePermission, answerPermission
}));
"""
    )
    for name in ("saveGeneral", "answerGeneral", "saveConflict", "answerConflict"):
        assert "詳細を閉じて開き直してから保存してください" in result[name]["alert"]
        assert result[name]["disabled"] is True
        assert result[name]["mode"] == ("view" if name.startswith("answer") else "edit")
    assert "保存できませんでした。 ［WI / node-test / /notes / entry.md］一般失敗" in result["saveGeneral"]["alert"]
    assert "回答できませんでした。 ［WI / node-test / /notes / 回答保存］一般失敗" in result["answerGeneral"]["alert"]
    assert result["saveGeneral"]["input"] == "ユーザーの保存本文"
    assert result["answerGeneral"]["input"] == "ユーザーの回答"
    assert result["savePermission"] == {
        "alert": "entry.mdを保存できませんでした。 ［WI / node-test / /notes / entry.md］権限がありません",
        "disabled": False,
        "input": "ユーザーの保存本文",
        "mode": "edit",
    }
    assert result["answerPermission"] == {
        "alert": "question.mdへ回答できませんでした。 ［WI / node-test / /notes / 回答保存］権限がありません",
        "disabled": False,
        "input": "ユーザーの回答",
        "mode": "view",
    }
