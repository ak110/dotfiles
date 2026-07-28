"""`atk serve`のテスト。"""

# pylint: disable=protected-access

import asyncio
import contextlib
import json
import logging
import pathlib
import re
import signal
import subprocess
import threading
import types
import typing

import _atk_mq_common as common
import _atk_mq_repo as feedback_repo
import _atk_serve as serve
import _atk_serve_app as serve_app
import _atk_serve_assets as assets
import _atk_serve_config as config
import _atk_serve_state as state
import filelock
import pytest


def test_config_precedence_and_platform_ports(tmp_path: pathlib.Path) -> None:
    """設定優先順位とOS別ポートを検証する。"""
    path = tmp_path / "serve.toml"
    path.write_text('host = "toml-host"\nport = 3000\n', encoding="utf-8")
    env = {
        "AGENT_TOOLKIT_SERVE_CONFIG": str(path),
        "AGENT_TOOLKIT_SERVE_HOST": "0.0.0.0",
        "AGENT_TOOLKIT_SERVE_PORT": "4000",
    }
    assert config.resolve_config(environ=env) == config.ServeConfig("0.0.0.0", 4000)
    assert config.resolve_config(host="cli-host", port=5000, environ=env) == config.ServeConfig("cli-host", 5000)
    assert config.default_port("linux") == 28766
    assert config.default_port("win32") == 28876


def test_unknown_config_key_logs_warning(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """未知の設定キーを警告ログで通知し、既知キーの解決は継続する。"""
    path = tmp_path / "serve.toml"
    path.write_text('host = "toml-host"\nunknown = 1\n', encoding="utf-8")
    env = {"AGENT_TOOLKIT_SERVE_CONFIG": str(path)}
    with caplog.at_level("WARNING"):
        resolved = config.resolve_config(environ=env, platform="linux")
    assert resolved == config.ServeConfig("toml-host", 28766)
    assert "unknown" in caplog.text


@pytest.mark.parametrize("host", ["", "  ", 1])
def test_invalid_host(host: object) -> None:
    """空又は非文字列hostを拒否する。"""
    with pytest.raises(ValueError):
        config.resolve_config(host=host)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("port", [True, 0, 65536])
def test_invalid_port(port: object) -> None:
    """bool又は範囲外portを拒否する。"""
    with pytest.raises(ValueError):
        config.resolve_config(port=port)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def test_assets_are_self_contained() -> None:
    """UI資産の自己完結性とサブパス置換点を検証する。"""
    combined = assets.HTML + assets.CSS + assets.JS
    assert "https://" not in combined
    assert "http://" not in combined
    assert "innerHTML" not in combined
    assert "insertAdjacentHTML" not in combined
    assert "/api/entries" in combined
    assert "/api/events" in combined
    assert "__BASE_PATH_HTML__" in assets.HTML
    assert "__BASE_PATH_JS__" in assets.JS
    assert "textContent" in assets.JS
    assert ".value" in assets.JS


def _run_node_ui(scenario: str) -> dict[str, typing.Any]:
    """UI関数を最小DOM上で実行し、シナリオのJSON結果を返す。"""
    source = assets.JS.replace("__BASE_PATH_JS__", '"/atk"').partition("bindEvents();")[0]
    executable = (
        source
        + "\n(async () => {\n"
        + scenario
        + "\n})().catch(error => { process.stderr.write(String(error.stack || error)); process.exitCode = 1; });"
    )
    script = f"""
class Element {{
  constructor(id = '', tagName = 'DIV') {{
    this.id = id;
    this.tagName = tagName;
    this.children = [];
    this.dataset = {{}};
    this.attributes = {{}};
    this.listeners = {{}};
    this.textContent = '';
    this.value = '';
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.open = false;
    this.dateTime = '';
  }}
  append(...children) {{ this.children.push(...children); }}
  replaceChildren(...children) {{ this.children = children; }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  addEventListener(name, handler) {{ this.listeners[name] = handler; }}
  showModal() {{ this.open = true; }}
  close() {{ this.open = false; }}
  focus() {{ globalThis.focused = this.id; }}
  reset() {{ globalThis.formReset = true; }}
}}
const ids = [
  'connection-status', 'refresh-button', 'create-button', 'global-error',
  'search-input', 'kind-filter', 'state-filter', 'answer-filter', 'target-filter',
  'category-filter', 'source-filter', 'entry-count', 'loading-indicator',
  'entry-list', 'empty-state', 'empty-create-button', 'detail-placeholder',
  'detail-view', 'detail-filename', 'detail-state', 'detail-metadata',
  'detail-content', 'readonly-notice', 'detail-actions', 'edit-button',
  'delete-button', 'edit-panel', 'edit-content', 'edit-content-error',
  'cancel-edit-button', 'save-entry-button', 'answer-panel', 'answer-input',
  'answer-input-error', 'save-answer-button', 'create-dialog', 'create-form',
  'create-kind', 'create-content', 'create-content-error', 'create-target',
  'create-target-error', 'create-source', 'tbd-fields', 'create-scope',
  'create-question-type', 'choice-fields', 'create-choices',
  'create-choices-error', 'cancel-create-button', 'delete-dialog', 'delete-form',
  'delete-target', 'delete-state', 'force-delete-row',
  'force-delete-confirmation', 'delete-error', 'cancel-delete-button', 'toast'
];
const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
elements['kind-filter'].value = 'all';
elements['state-filter'].value = 'active';
elements['answer-filter'].value = 'all';
elements['create-kind'].value = 'feedback';
elements['create-question-type'].value = 'free-form';
const documentListeners = {{}};
globalThis.document = {{
  activeElement: null,
  getElementById(id) {{ return elements[id] || null; }},
  createElement(tagName) {{ return new Element('', tagName.toUpperCase()); }},
  addEventListener(name, handler) {{ documentListeners[name] = handler; }}
}};
globalThis.setTimeout = () => 1;
globalThis.clearTimeout = () => undefined;
const fetchCalls = [];
let fetchHandler = async () => ({{ok: true, status: 200, statusText: 'OK', json: async () => ({{entries: []}})}});
globalThis.fetch = async (url, options = {{}}) => {{
  fetchCalls.push({{url, options}});
  return fetchHandler(url, options);
}};
eval({json.dumps(executable)});
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    return typing.cast(dict[str, typing.Any], json.loads(completed.stdout))


def test_assets_render_api_entry_values_as_dom_text() -> None:
    """API由来の値を全表示経路のテキストとフォーム値へ安全に反映する。"""
    dangerous = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    entry: dict[str, object] = {
        field: dangerous
        for field in (
            "kind",
            "filename",
            "state",
            "target_repo",
            "source",
            "category",
            "summary",
            "updated_at",
        )
    }
    entry["answered"] = False
    entry["content"] = dangerous
    rendered = _run_node_ui(
        f"""
const entry = {json.dumps(entry)};
const row = renderEntry(entry);
displayEntry(entry);
const texts = [];
const visit = node => {{
  if (node.textContent) texts.push(node.textContent);
  node.children.forEach(visit);
}};
visit(row);
visit(elements['detail-metadata']);
currentEntry = {{...entry, state: 'inbox'}};
openDeleteDialog();
showToast({json.dumps(dangerous)});
fetchHandler = async () => ({{
  ok: false,
  status: 400,
  statusText: 'Bad Request',
  json: async () => ({{error: {json.dumps(dangerous)}}})
}});
try {{
  await api('/api/failure');
}} catch (error) {{
  showError(error);
}}
setFieldError('edit-content', {json.dumps(dangerous)});
process.stdout.write(JSON.stringify({{
  tags: row.children.map(child => child.tagName),
  texts,
  detail: elements['detail-content'].textContent,
  detailState: elements['detail-state'].textContent,
  editValue: elements['edit-content'].value,
  deleteTarget: elements['delete-target'].textContent,
  deleteState: elements['delete-state'].textContent,
  toast: elements['toast'].textContent,
  globalError: elements['global-error'].textContent,
  inlineError: elements['edit-content-error'].textContent,
  childCounts: [
    elements['delete-target'],
    elements['delete-state'],
    elements['toast'],
    elements['global-error'],
    elements['edit-content-error']
  ].map(element => element.children.length)
}}));
"""
    )
    assert rendered["tags"] == ["BUTTON"]
    assert dangerous in rendered["texts"]
    assert rendered["detail"] == dangerous
    assert rendered["detailState"] == dangerous
    assert rendered["editValue"] == dangerous
    assert rendered["deleteTarget"] == dangerous
    assert rendered["deleteState"] == "未処理"
    assert rendered["toast"] == dangerous
    assert rendered["globalError"] == dangerous
    assert rendered["inlineError"] == dangerous
    assert rendered["childCounts"] == [0, 0, 0, 0, 0]


def test_assets_focus_on_user_entry_workflows() -> None:
    """追加、一覧、詳細、編集、回答、削除の利用者操作を検証する。"""
    forbidden = (
        "start-processing",
        "data-action",
        "batch-note",
        "batch-category",
        "batch-commit",
        'id="commit"',
        'id="toggle"',
        'id="select-all"',
        "/api/enable",
        "/api/disable",
        "/api/entries/adopt",
        "/api/entries/commit",
        "/api/entries/reject",
    )
    combined = assets.HTML + assets.JS
    assert all(value not in combined for value in forbidden)
    result = _run_node_ui(
        """
let detail = {
  kind: 'tbd', state: 'inbox', filename: 'entry.md', answered: false,
  target_repo: 'example/repo', source: 'web', category: null,
  summary: '確認', updated_at: '2026-01-01T00:00:00+00:00', content: '取得時本文'
};
let removed = false;
fetchHandler = async (url, options) => {
  let body = {};
  if (url.endsWith('/api/entries/inbox/entry.md') && (!options.method || options.method === 'GET')) {
    body = {entry: detail};
  } else if (!options.method || options.method === 'GET') {
    body = {entries: removed ? [] : [detail]};
  } else if (options.method === 'POST' && url.endsWith('/api/entries')) {
    body = {filenames: ['created.md']};
  } else if (options.method === 'PUT') {
    detail = {...detail, content: JSON.parse(options.body).content};
    body = {changed: true};
  } else if (url.endsWith('/api/entries/answer')) {
    const answer = JSON.parse(options.body).answer;
    detail = {...detail, answered: true, content: `${detail.content}\\n${answer}`};
    body = {changed: true};
  } else if (url.endsWith('/api/entries/remove')) {
    removed = true;
    body = {filenames: ['entry.md']};
  } else {
    body = {changed: true, filenames: ['entry.md']};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => body};
};
elements['create-content'].value = '新規本文';
elements['create-target'].value = 'example/repo';
await createEntry({preventDefault() {}});
const createResult = {
  count: elements['entry-count'].textContent,
  first: elements['entry-list'].children[0].children[0].dataset.key,
  toast: elements['toast'].textContent
};
await renderDetail(detail);
enterEdit();
elements['edit-content'].value = '編集本文';
await saveEntry();
const editResult = {
  detail: elements['detail-content'].textContent,
  toast: elements['toast'].textContent
};
enterEdit();
elements['answer-input'].value = '回答本文';
await saveAnswer();
const answerResult = {
  detail: elements['detail-content'].textContent,
  answered: currentEntry.answered,
  toast: elements['toast'].textContent
};
openDeleteDialog();
await removeEntry({preventDefault() {}});
process.stdout.write(JSON.stringify({
  calls: fetchCalls.map(call => ({
    url: call.url,
    method: call.options.method || 'GET',
    body: call.options.body ? JSON.parse(call.options.body) : null
  })),
  createResult,
  editResult,
  answerResult,
  deleteResult: {
    count: elements['entry-count'].textContent,
    listLength: elements['entry-list'].children.length,
    placeholder: !elements['detail-placeholder'].hidden,
    detailHidden: elements['detail-view'].hidden,
    toast: elements['toast'].textContent
  }
}));
"""
    )
    calls = result["calls"]
    assert any(call["url"] == "/atk/api/entries" and call["method"] == "POST" for call in calls)
    assert any(call["url"].startswith("/atk/api/entries?") and call["method"] == "GET" for call in calls)
    assert any(call["url"] == "/atk/api/entries/inbox/entry.md" and call["method"] == "GET" for call in calls)
    edit_call = next(call for call in calls if call["method"] == "PUT")
    assert edit_call["body"] == {"content": "編集本文", "expected_content": "取得時本文"}
    answer_call = next(call for call in calls if call["url"] == "/atk/api/entries/answer")
    assert answer_call["body"] == {
        "filename": "entry.md",
        "answer": "回答本文",
        "expected_content": "編集本文",
    }
    remove_call = next(call for call in calls if call["url"] == "/atk/api/entries/remove")
    assert remove_call["body"] == {"filenames": ["entry.md"]}
    assert result["createResult"] == {
        "count": "1件",
        "first": "inbox/entry.md",
        "toast": "項目を追加しました。",
    }
    assert result["editResult"] == {"detail": "編集本文", "toast": "本文を保存しました。"}
    assert result["answerResult"] == {
        "detail": "編集本文\n回答本文",
        "answered": True,
        "toast": "回答を保存しました。",
    }
    assert result["deleteResult"] == {
        "count": "0件",
        "listLength": 0,
        "placeholder": True,
        "detailHidden": True,
        "toast": "項目を削除しました。",
    }


def test_assets_present_search_filters_count_and_empty_state() -> None:
    """検索、フィルター、並び順、件数、読込中、空状態を検証する。"""
    result = _run_node_ui(
        """
entries = [
  {kind: 'feedback', state: 'inbox', filename: 'old.md', summary: '対象外',
   target_repo: 'other/repo', category: 'x', source: 'cli', updated_at: '2025-01-01'},
  {kind: 'tbd', state: 'processing', filename: 'new.md', summary: '探す文字',
   target_repo: 'example/repo', category: 'ui', source: 'web', updated_at: '2026-01-01'}
];
applyClientFilters();
const sortedKeys = elements['entry-list'].children.map(item => item.children[0].dataset.key);
elements['search-input'].value = '探す';
elements['target-filter'].value = 'example/repo';
elements['category-filter'].value = 'ui';
elements['source-filter'].value = 'web';
applyClientFilters();
const populated = {
  count: elements['entry-count'].textContent,
  first: elements['entry-list'].children[0].children[0].dataset.key,
  emptyHidden: elements['empty-state'].hidden,
  query: listQuery()
};
setLoading(true);
const loadingState = {
  visible: !elements['loading-indicator'].hidden,
  busy: elements['entry-list'].attributes['aria-busy']
};
elements['search-input'].value = '一致しない';
setLoading(false);
applyClientFilters();
process.stdout.write(JSON.stringify({
  sortedKeys,
  populated,
  loadingState,
  emptyVisible: !elements['empty-state'].hidden,
  emptyCount: elements['entry-count'].textContent
}));
"""
    )
    assert result["sortedKeys"] == ["processing/new.md", "inbox/old.md"]
    assert result["populated"]["count"] == "1件"
    assert result["populated"]["first"] == "processing/new.md"
    assert result["populated"]["emptyHidden"] is True
    assert "target_repo=example%2Frepo" in result["populated"]["query"]
    assert "category=ui" in result["populated"]["query"]
    assert "source=web" in result["populated"]["query"]
    assert result["loadingState"] == {"visible": True, "busy": "true"}
    assert result["emptyVisible"] is True
    assert result["emptyCount"] == "0件"


def test_assets_use_consistent_readable_typography() -> None:
    """本文と補助文字の最小文字サイズを検証する。"""
    assert "--font-size-body: 1rem" in assets.CSS
    assert "--font-size-secondary: 0.875rem" in assets.CSS
    sizes = re.findall(r"font-size:\s*([0-9.]+)rem", assets.CSS)
    assert sizes
    assert all(float(size) >= 0.875 for size in sizes)


def test_assets_use_japanese_labels_for_entry_states() -> None:
    """種別、状態、回答状況を日本語ラベルへ変換する。"""
    result = _run_node_ui(
        """
process.stdout.write(JSON.stringify({
  kinds: [labelFor('kind', 'feedback'), labelFor('kind', 'tbd')],
  states: ['inbox', 'processing', 'adopted', 'rejected'].map(value => labelFor('state', value)),
  answers: [true, false, null].map(value => labelFor('answered', value))
}));
"""
    )
    assert result == {
        "kinds": ["フィードバック", "確認事項"],
        "states": ["未処理", "処理中", "採用済み", "不採用"],
        "answers": ["回答済み", "未回答", "対象外"],
    }


def test_processing_removal_requires_explicit_force_confirmation() -> None:
    """processing削除だけに明示確認とforce送信を要求する。"""
    result = _run_node_ui(
        """
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entries: [], filenames: ['entry.md']})
});
const processing = {kind: 'feedback', state: 'processing', filename: 'entry.md', content: '本文'};
currentEntry = processing;
openDeleteDialog();
await removeEntry({preventDefault() {}});
const beforeConfirmation = fetchCalls.length;
elements['force-delete-confirmation'].checked = true;
await removeEntry({preventDefault() {}});
const forcedBody = JSON.parse(fetchCalls.find(call => call.options.method === 'POST').options.body);
currentEntry = {kind: 'feedback', state: 'inbox', filename: 'inbox.md', content: '本文'};
openDeleteDialog();
await removeEntry({preventDefault() {}});
const postCalls = fetchCalls.filter(call => call.options.method === 'POST');
process.stdout.write(JSON.stringify({
  forceVisible: !elements['force-delete-row'].hidden,
  beforeConfirmation,
  forcedBody,
  inboxBody: JSON.parse(postCalls[1].options.body)
}));
"""
    )
    assert result["beforeConfirmation"] == 0
    assert result["forcedBody"] == {"filenames": ["entry.md"], "force": True}
    assert result["inboxBody"] == {"filenames": ["inbox.md"]}


def test_finished_entries_are_read_only() -> None:
    """終了状態では編集、回答、削除を隠し、対応中状態で表示する。"""
    result = _run_node_ui(
        """
const result = {};
for (const state of ['inbox', 'processing', 'adopted', 'rejected']) {
  editing = true;
  displayEntry({
    kind: 'tbd', state, filename: `${state}.md`, answered: false,
    content: '本文', updated_at: '2026-01-01'
  });
  result[state] = {
    actions: !elements['detail-actions'].hidden,
    edit: !elements['edit-button'].hidden,
    remove: !elements['delete-button'].hidden,
    readonly: !elements['readonly-notice'].hidden,
    answer: !elements['answer-panel'].hidden
  };
}
process.stdout.write(JSON.stringify(result));
"""
    )
    for active_state in ("inbox", "processing"):
        assert result[active_state] == {
            "actions": True,
            "edit": True,
            "remove": True,
            "readonly": False,
            "answer": True,
        }
    for finished_state in ("adopted", "rejected"):
        assert result[finished_state] == {
            "actions": False,
            "edit": False,
            "remove": False,
            "readonly": True,
            "answer": False,
        }


def test_assets_keep_selection_and_reload_from_sse() -> None:
    """SSE更新時に選択と編集中の入力値・競合基準を保持する。"""
    assert "events.addEventListener('changed', () => loadEntries({fromSse: true}))" in assets.JS
    result = _run_node_ui(
        """
const listed = {
  kind: 'tbd', state: 'inbox', filename: 'entry.md', answered: false,
  summary: '更新後一覧', updated_at: '2026-02-01'
};
fetchHandler = async (url, options) => {
  if (!options.method || options.method === 'GET') {
    const body = url.includes('/api/entries/inbox/')
      ? {entry: {...listed, content: 'SSE再描画本文'}}
      : {entries: [listed]};
    return {ok: true, status: 200, statusText: 'OK', json: async () => body};
  }
  return {
    ok: false, status: 409, statusText: 'Conflict',
    json: async () => ({
      error: '編集中に他プロセスが対象を変更しました',
      code: 'edit_conflict'
    })
  };
};
currentEntry = {...listed, content: '更新前本文'};
editing = false;
await loadEntries({fromSse: true});
const redrawnDetail = elements['detail-content'].textContent;
const detailRequests = fetchCalls.filter(call => call.url.includes('/api/entries/inbox/')).length;
currentEntry = {...listed, content: '取得時本文'};
editing = true;
editBaseline = '取得時本文';
answerBaseline = '取得時本文';
elements['edit-content'].value = '利用者の本文';
elements['answer-input'].value = '利用者の回答';
await loadEntries({fromSse: true});
await saveEntry();
process.stdout.write(JSON.stringify({
  firstUrl: fetchCalls[0].url,
  redrawnDetail,
  detailRequests,
  selected: currentEntry.filename,
  content: elements['edit-content'].value,
  answer: elements['answer-input'].value,
  editBaseline,
  answerBaseline,
  error: elements['global-error'].textContent
}));
"""
    )
    assert result["firstUrl"].startswith("/atk/api/entries?")
    assert result["redrawnDetail"] == "SSE再描画本文"
    assert result["detailRequests"] == 1
    assert result["selected"] == "entry.md"
    assert result["content"] == "利用者の本文"
    assert result["answer"] == "利用者の回答"
    assert result["editBaseline"] == result["answerBaseline"] == "取得時本文"
    assert "入力内容を保持" in result["error"]


def test_edit_and_answer_conflicts_keep_user_input() -> None:
    """編集競合だけを外部更新として案内し、ロック競合は通常エラーにする。"""
    result = _run_node_ui(
        """
let conflictCode = 'edit_conflict';
fetchHandler = async () => ({
  ok: false, status: 409, statusText: 'Conflict',
  json: async () => ({
    error: conflictCode === 'edit_conflict'
      ? '編集中に他プロセスが対象を変更しました'
      : '別の操作が進行中です',
    code: conflictCode
  })
});
currentEntry = {
  kind: 'tbd', state: 'inbox', filename: 'entry.md', answered: false,
  content: '取得時本文'
};
editing = true;
editBaseline = '取得時本文';
answerBaseline = '取得時本文';
elements['edit-content'].value = '保存したい本文';
elements['answer-input'].value = '保存したい回答';
await saveEntry();
const editError = elements['global-error'].textContent;
await saveAnswer();
const answerError = elements['global-error'].textContent;
conflictCode = 'lock_conflict';
await saveEntry();
process.stdout.write(JSON.stringify({
  content: elements['edit-content'].value,
  answer: elements['answer-input'].value,
  editError,
  answerError,
  lockError: elements['global-error'].textContent
}));
"""
    )
    assert result["content"] == "保存したい本文"
    assert result["answer"] == "保存したい回答"
    assert "再読込" in result["editError"]
    assert "再読込" in result["answerError"]
    assert result["lockError"] == "別の操作が進行中です"
    assert "外部" not in result["lockError"]


def test_assets_keep_keyboard_and_narrow_viewport_support() -> None:
    """保存、検索、Escapeのキー操作と狭幅表示を検証する。"""
    assert "@media (max-width: 700px)" in assets.CSS
    assert "@media (prefers-reduced-motion: reduce)" in assets.CSS
    result = _run_node_ui(
        """
bindEvents();
let saved = 0;
saveEntry = async () => { saved += 1; };
editing = true;
const preventions = [];
documentListeners.keydown({
  key: 's', ctrlKey: true, metaKey: false, altKey: false,
  preventDefault() { preventions.push('save'); }
});
documentListeners.keydown({
  key: 's', ctrlKey: false, metaKey: true, altKey: false,
  preventDefault() { preventions.push('command-save'); }
});
editing = false;
document.activeElement = null;
documentListeners.keydown({
  key: '/', ctrlKey: false, metaKey: false, altKey: false,
  preventDefault() { preventions.push('search'); }
});
elements['create-dialog'].open = true;
documentListeners.keydown({key: 'Escape', ctrlKey: false, metaKey: false, altKey: false, preventDefault() {}});
editing = true;
documentListeners.keydown({key: 'Escape', ctrlKey: false, metaKey: false, altKey: false, preventDefault() {}});
process.stdout.write(JSON.stringify({
  saved,
  preventions,
  focused: globalThis.focused,
  dialogOpen: elements['create-dialog'].open,
  editing
}));
"""
    )
    assert result == {
        "saved": 2,
        "preventions": ["save", "command-save", "search"],
        "focused": "search-input",
        "dialogOpen": False,
        "editing": False,
    }


def test_state_keeps_latest_event(tmp_path: pathlib.Path) -> None:
    """状態管理を構築できることを検証する。"""
    current = state.ServeState(tmp_path)
    assert current.root == tmp_path


def test_all_api_routes_are_registered(tmp_path: pathlib.Path) -> None:
    """計画で定義した全APIルートを登録する。"""
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state)
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    expected = {
        "/api/status",
        "/api/entries",
        "/api/entries/<state_name>/<filename>",
        "/api/entries/start-processing",
        "/api/entries/adopt",
        "/api/entries/reject",
        "/api/entries/remove",
        "/api/entries/commit",
        "/api/entries/answer",
        "/api/enable",
        "/api/disable",
        "/api/events",
    }
    assert expected <= rules


@pytest.mark.asyncio
async def test_sse_multiple_subscribers_and_cleanup(tmp_path: pathlib.Path) -> None:
    """複数購読者へ変更通知を配信し、切断後に購読を除去する。"""
    current = state.ServeState(tmp_path)
    first = current.events(heartbeat=1)
    second = current.events(heartbeat=1)
    first_next = asyncio.create_task(anext(first))
    second_next = asyncio.create_task(anext(second))
    await asyncio.sleep(0)
    current.publish()
    assert "event: changed" in await first_next
    assert "event: changed" in await second_next
    await first.aclose()
    await second.aclose()


@pytest.mark.asyncio
async def test_sse_heartbeat(tmp_path: pathlib.Path) -> None:
    """変更が無い期間はheartbeatを送信する。"""
    events = state.ServeState(tmp_path).events(heartbeat=0.001)
    assert await anext(events) == ": heartbeat\n\n"
    await events.aclose()


@pytest.mark.asyncio
async def test_cancelled_request_keeps_worker_slot_until_completion() -> None:
    """要求キャンセル後も同期処理が終わるまでSemaphore枠を解放しない。"""
    workers = serve_app.BoundedWorkers(1)
    first_started = threading.Event()
    first_release = threading.Event()
    second_started = threading.Event()

    def first() -> None:
        first_started.set()
        first_release.wait()

    def second() -> None:
        second_started.set()

    first_task = asyncio.create_task(workers.run(first))
    await asyncio.to_thread(first_started.wait)
    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task
    second_task = asyncio.create_task(workers.run(second))
    await asyncio.sleep(0.01)
    assert not second_started.is_set()
    first_release.set()
    await second_task
    assert second_started.is_set()


def test_entries_hold_lock_through_snapshot(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一覧取得はpullからファイル読取りまで同一ロックを保持する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    entry = inbox / "entry.md"
    entry.write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\nsource: test\n---\n\n要約本文\n",
        encoding="utf-8",
    )
    calls: list[str] = []
    locked = False

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        nonlocal locked
        calls.append("lock")
        locked = True
        try:
            yield
        finally:
            locked = False
            calls.append("unlock")

    def pull(_path: pathlib.Path) -> None:
        assert locked
        calls.append("pull")

    monkeypatch.setattr(common, "repo_lock", lock)
    monkeypatch.setattr(common, "pull", pull)
    result = serve_app.Operations(tmp_path).entries({})
    assert calls == ["lock", "pull", "unlock"]
    assert result[0] | {
        "updated_at": result[0]["updated_at"],
    } == {
        "kind": "feedback",
        "state": "inbox",
        "filename": "entry.md",
        "answered": None,
        "target_repo": "example/repo",
        "source": "test",
        "category": None,
        "summary": "要約本文",
        "updated_at": result[0]["updated_at"],
    }


def test_operations_answered_filter_returns_only_answered_tbds(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`answered=yes`は回答済みTBDのみを返し、未回答TBD・feedbackを除外する。"""
    monkeypatch.setattr(common, "repo_lock", lambda *_a, **_k: contextlib.nullcontext())
    monkeypatch.setattr(common, "pull", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "answered.md").write_text(
        "---\ntype: tbd\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n回答済み\n",
        encoding="utf-8",
    )
    (inbox / "unanswered.md").write_text(
        "---\ntype: tbd\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    (inbox / "feedback.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\n---\n\nフィードバック本文\n",
        encoding="utf-8",
    )
    result = serve_app.Operations(tmp_path).entries({"answered": "yes"})
    assert [item["filename"] for item in result] == ["answered.md"]


@pytest.mark.asyncio
async def test_add_api_rejects_missing_type(tmp_path: pathlib.Path) -> None:
    """`type`欠落は必須キー不足として400を返す。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries",
        json={"messages": ["x"], "target_repo": "example/repo"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_add_api_rejects_tbd_only_scope_on_feedback(tmp_path: pathlib.Path) -> None:
    """TBD専用の`scope`をfeedbackへ指定すると拒否する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries",
        json={"type": "feedback", "messages": ["x"], "target_repo": "example/repo", "scope": "s"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_tbd_reject_transition_succeeds(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TBDエントリも他種別と同様に不採用遷移が成功する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(common, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_app.feedback_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.feedback_mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.feedback_mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: tbd\ntarget_repo: github.com/example/foo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post("/api/entries/reject", json={"filenames": ["entry.md"]})
    assert response.status_code == 200
    assert (tmp_path / "rejected" / "entry.md").is_file()
    assert not (inbox / "entry.md").exists()


@pytest.mark.asyncio
async def test_answer_api_rejects_feedback_entry(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """feedbackエントリへの回答送信は拒否する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.tbd_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.tbd_mutations, "_pull", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries/answer",
        json={"filename": "entry.md", "answer": "回答"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_edit_and_answer_apis_detect_external_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取得後の外部更新を409で保護し、最新値と従来形式の更新を許可する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, feedback_repo, serve_app.feedback_mutations, serve_app.tbd_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(common, "repo_lock", lock)
    monkeypatch.setattr(common, "pull", lambda _path: None)

    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    feedback_path = inbox / "feedback.md"
    initial_feedback = "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n取得時本文\n"
    feedback_path.write_text(initial_feedback, encoding="utf-8")
    tbd_path = inbox / "question.md"
    initial_tbd = (
        "---\ntype: tbd\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    tbd_path.write_text(initial_tbd, encoding="utf-8")

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    feedback_detail = await (await client.get("/api/entries/inbox/feedback.md")).get_json()
    external_feedback = initial_feedback.replace("取得時本文", "外部更新後の本文")
    feedback_path.write_text(external_feedback, encoding="utf-8")
    conflict = await client.put(
        "/api/entries/inbox/feedback.md",
        json={"content": "利用者の本文", "expected_content": feedback_detail["entry"]["content"]},
    )
    assert conflict.status_code == 409
    assert (await conflict.get_json())["code"] == "edit_conflict"
    assert feedback_path.read_text(encoding="utf-8") == external_feedback

    latest_content = external_feedback.replace("外部更新後の本文", "最新基準で更新")
    latest = await client.put(
        "/api/entries/inbox/feedback.md",
        json={"content": latest_content, "expected_content": external_feedback},
    )
    assert latest.status_code == 200
    assert feedback_path.read_text(encoding="utf-8") == latest_content
    legacy_content = latest_content.replace("最新基準で更新", "従来形式で更新")
    legacy = await client.put(
        "/api/entries/inbox/feedback.md",
        json={"content": legacy_content},
    )
    assert legacy.status_code == 200
    assert feedback_path.read_text(encoding="utf-8") == legacy_content

    tbd_detail = await (await client.get("/api/entries/inbox/question.md")).get_json()
    external_tbd = initial_tbd.replace("質問？", "外部更新後の質問？")
    tbd_path.write_text(external_tbd, encoding="utf-8")
    answer_conflict = await client.post(
        "/api/entries/answer",
        json={
            "filename": "question.md",
            "answer": "古い基準からの回答",
            "expected_content": tbd_detail["entry"]["content"],
        },
    )
    assert answer_conflict.status_code == 409
    assert (await answer_conflict.get_json())["code"] == "edit_conflict"
    assert tbd_path.read_text(encoding="utf-8") == external_tbd

    latest_answer = await client.post(
        "/api/entries/answer",
        json={
            "filename": "question.md",
            "answer": "最新基準からの回答",
            "expected_content": external_tbd,
        },
    )
    assert latest_answer.status_code == 200
    assert tbd_path.read_text(encoding="utf-8").endswith("最新基準からの回答\n")
    legacy_answer = await client.post(
        "/api/entries/answer",
        json={"filename": "question.md", "answer": "従来形式からの回答"},
    )
    assert legacy_answer.status_code == 200
    assert tbd_path.read_text(encoding="utf-8").endswith("従来形式からの回答\n")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload", "filename", "initial_content"),
    [
        (
            "put",
            "/api/entries/inbox/feedback.md",
            {"content": "更新本文", "expected_content": None},
            "feedback.md",
            "変更前の本文\n",
        ),
        (
            "put",
            "/api/entries/inbox/feedback.md",
            {"content": "更新本文", "expected_content": ""},
            "feedback.md",
            "変更前の本文\n",
        ),
        (
            "put",
            "/api/entries/inbox/feedback.md",
            {"content": "更新本文", "expected_content": "  "},
            "feedback.md",
            "変更前の本文\n",
        ),
        (
            "post",
            "/api/entries/answer",
            {"filename": "question.md", "answer": "回答", "expected_content": None},
            "question.md",
            "## 質問\n\n質問？\n\n## 回答\n\n",
        ),
        (
            "post",
            "/api/entries/answer",
            {"filename": "question.md", "answer": "回答", "expected_content": ""},
            "question.md",
            "## 質問\n\n質問？\n\n## 回答\n\n",
        ),
        (
            "post",
            "/api/entries/answer",
            {"filename": "question.md", "answer": "回答", "expected_content": "\t"},
            "question.md",
            "## 質問\n\n質問？\n\n## 回答\n\n",
        ),
    ],
)
async def test_edit_and_answer_apis_reject_invalid_specified_expected_content(
    tmp_path: pathlib.Path,
    method: str,
    path: str,
    payload: dict[str, object],
    filename: str,
    initial_content: str,
) -> None:
    """明示した`expected_content`のnull・空値を400で拒否し、対象を変更しない。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    entry = inbox / filename
    entry.write_text(initial_content, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await getattr(app.test_client(), method)(path, json=payload)
    assert response.status_code == 400
    assert entry.read_text(encoding="utf-8") == initial_content


@pytest.mark.asyncio
async def test_unrelated_runtime_error_is_not_classified_as_edit_conflict(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """競合以外のRuntimeErrorを409へ誤分類しない。"""
    operations = serve_app.Operations(tmp_path)

    def fail(
        _state: str,
        _filename: str,
        _content: str,
        _expected_content: str | None = None,
    ) -> bool:
        raise RuntimeError("別の実行時エラー")

    monkeypatch.setattr(operations, "edit", fail)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    response = await app.test_client().put(
        "/api/entries/inbox/entry.md",
        json={"content": "本文"},
    )
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_adopt_api_rejects_category_for_tbd(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TBDエントリへのカテゴリ付き採用は拒否する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.feedback_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.feedback_mutations, "_pull", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: tbd\ntarget_repo: github.com/example/foo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n回答\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries/adopt",
        json={"filenames": ["entry.md"], "category": "some-category"},
    )
    assert response.status_code == 400


def test_serve_state_watches_only_new_four_states(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """状態監視は平坦化後の4状態フォルダのみを対象とし、旧feedback/tbd階層を生成しない。"""
    current = state.ServeState(tmp_path)
    scheduled: list[str] = []
    monkeypatch.setattr(
        current.observer,
        "schedule",
        lambda _handler, path, recursive=False: scheduled.append(path),
    )
    monkeypatch.setattr(current.observer, "start", lambda: None)
    current.start(asyncio.new_event_loop())
    assert sorted(pathlib.Path(p).name for p in scheduled) == ["adopted", "inbox", "processing", "rejected"]
    assert not (tmp_path / "feedback").exists()
    assert not (tmp_path / "tbd").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/entries?status=unknown", None),
        ("get", "/api/entries?target_repo=", None),
        (
            "post",
            "/api/entries",
            {"type": "feedback", "messages": ["x"], "target_repo": "example/repo", "source": ""},
        ),
        (
            "post",
            "/api/entries",
            {
                "type": "tbd",
                "messages": ["x"],
                "target_repo": "example/repo",
                "scope": "s",
                "question_type": "free-form",
                "choices": ["a"],
            },
        ),
        ("post", "/api/entries/adopt", {"filenames": ["x.md"], "note": 1}),
        ("post", "/api/entries/adopt", {"filenames": ["../x.md"]}),
    ],
)
async def test_api_rejects_invalid_inputs(
    tmp_path: pathlib.Path,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    """Web入力境界が列挙・型・空文字・basename違反を400で拒否する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()
    response = await getattr(client, method)(path, json=payload)
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/entries", {"type": "feedback", "messages": ["feedback"]}),
        (
            "/api/entries",
            {"type": "tbd", "messages": ["TBDですか？"], "scope": "test", "question_type": "free-form"},
        ),
    ],
)
async def test_web_add_mutations_require_target_repo(
    tmp_path: pathlib.Path,
    path: str,
    payload: dict[str, object],
) -> None:
    """新規追加系APIは常駐プロセスのカレントディレクトリへ依存しないためtarget_repoを必須とする。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(path, json=payload)
    assert response.status_code == 400
    assert "target_repo" in (await response.get_json())["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/entries", {"type": "feedback", "messages": ["feedback"], "target_repo": None}),
        (
            "/api/entries",
            {
                "type": "tbd",
                "messages": ["TBDですか？"],
                "scope": "test",
                "question_type": "free-form",
                "target_repo": None,
            },
        ),
    ],
)
async def test_web_add_mutations_reject_null_target_repo(
    tmp_path: pathlib.Path,
    path: str,
    payload: dict[str, object],
) -> None:
    """`target_repo`キーが存在しても値が`null`の場合は必須検証を回避できず400になる。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(path, json=payload)
    assert response.status_code == 400
    assert "target_repo" in (await response.get_json())["error"]


@pytest.mark.asyncio
async def test_web_transition_mutations_allow_omitted_target_repo(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態遷移系APIはfilenameで対象を一意に特定できるためtarget_repo省略を許容する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(common, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_app.feedback_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.feedback_mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.feedback_mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post("/api/entries/adopt", json={"filenames": ["entry.md"]})
    assert response.status_code == 200
    assert (tmp_path / "adopted" / "entry.md").is_file()
    assert not (inbox / "entry.md").exists()


@pytest.mark.asyncio
async def test_invalid_json_returns_json_error(tmp_path: pathlib.Path) -> None:
    """構文エラーのJSON本文を一貫したJSON 400応答へ変換する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries",
        data='{"messages":',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert await response.get_json() == {"error": "JSON本文の構文が不正です"}


@pytest.mark.asyncio
async def test_lock_timeout_returns_conflict(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Web操作の有限待機ロック競合をJSON 409応答へ変換する。"""

    def entries(_filters: dict[str, str]) -> list[dict[str, object]]:
        raise filelock.Timeout("locked")

    operations = serve_app.Operations(tmp_path)
    monkeypatch.setattr(operations, "entries", entries)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    response = await app.test_client().get("/api/entries")
    assert response.status_code == 409
    assert await response.get_json() == {
        "error": "別の操作が進行中です",
        "code": "lock_conflict",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload", "expected_target"),
    [
        (
            "/api/entries",
            {
                "type": "feedback",
                "messages": ["feedback"],
                "target_repo": "https://github.com/Example/Specified.git",
            },
            "github.com/example/specified",
        ),
        (
            "/api/entries",
            {
                "type": "tbd",
                "messages": ["TBDですか？"],
                "scope": "test",
                "question_type": "free-form",
                "target_repo": "https://github.com/Example/Specified.git",
            },
            "github.com/example/specified",
        ),
    ],
)
async def test_add_api_resolves_target_repo_into_frontmatter(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, object],
    expected_target: str,
) -> None:
    """追加APIがCLIと同じ解決契約のtarget_repoをfrontmatterへ保存する。"""

    def resolve(value: str | None, *, cwd: pathlib.Path | None = None) -> str:
        del cwd
        if value is None:
            return "github.com/example/current"
        if value == "https://github.com/Example/Specified.git":
            return "github.com/example/specified"
        raise AssertionError(value)

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(feedback_repo, "resolve_repo_id", resolve)
    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(common, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_app.feedback_add, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.feedback_add, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.feedback_add, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_app.tbd_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.tbd_mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.tbd_mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(path, json=payload)
    assert response.status_code == 201
    body = await response.get_json()
    content = (tmp_path / "inbox" / body["filenames"][0]).read_text(encoding="utf-8")
    assert f"target_repo: {expected_target}" in content
    assert "target_repo: \n" not in content


def test_create_app_keeps_resolved_config(tmp_path: pathlib.Path) -> None:
    """解決済み設定と状態をapp.configへ保持する。"""
    resolved = config.ServeConfig("127.0.0.1", 28766)
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, resolved, current_state)
    assert app.config["SERVE_CONFIG"] == resolved
    assert app.config["SERVE_STATE"] is current_state


@pytest.mark.asyncio
async def test_index_and_js_reflect_forwarded_prefix(tmp_path: pathlib.Path) -> None:
    """`X-Forwarded-Prefix`付与時、HTML属性とJSのBASE_PATH定数双方に反映される。"""
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    headers = {"X-Forwarded-Prefix": "/atk", "X-Forwarded-Proto": "https"}

    index_response = await client.get("/atk/", headers=headers)
    assert index_response.status_code == 200
    index_body = await index_response.get_data(as_text=True)
    assert 'href="/atk/static/app.css"' in index_body
    assert 'src="/atk/static/app.js"' in index_body

    js_response = await client.get("/atk/static/app.js", headers=headers)
    assert js_response.status_code == 200
    js_body = await js_response.get_data(as_text=True)
    assert 'const BASE_PATH="/atk";' in js_body


@pytest.mark.asyncio
async def test_index_and_js_without_prefix_use_empty_base(tmp_path: pathlib.Path) -> None:
    """ヘッダー無しでは空文字列扱いとなり、直接アクセス時も従来どおり応答する。"""
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()

    index_body = await (await client.get("/")).get_data(as_text=True)
    assert 'href="/static/app.css"' in index_body
    assert 'src="/static/app.js"' in index_body

    js_body = await (await client.get("/static/app.js")).get_data(as_text=True)
    assert 'const BASE_PATH="";' in js_body


@pytest.mark.asyncio
async def test_safe_base_path_rejects_value_that_proxy_fix_accepts(tmp_path: pathlib.Path) -> None:
    """`ProxyFix`が受理する文字集合でも`_safe_base_path`固有の文字種制限に反する値は空文字列へ縮退する。

    `pytilpack.web.validate_forwarded_prefix`は`:`・`@`等RFC3986のpchar相当を広く許可するのに対し、
    `_safe_base_path`は英数字と`._~/-`のみへ限定しており両者の許容範囲は完全には一致しない。
    `:`を含む値は`ProxyFix`層を通過するため、この値で`_safe_base_path`固有の拒否分岐が
    実際に機能していることを検証する。
    """
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    headers = {"X-Forwarded-Prefix": "/atk:1"}

    index_response = await client.get("/atk:1/", headers=headers)
    assert index_response.status_code == 200
    index_body = await index_response.get_data(as_text=True)
    assert 'href="/static/app.css"' in index_body
    assert "atk:1" not in index_body

    js_body = await (await client.get("/atk:1/static/app.js", headers=headers)).get_data(as_text=True)
    assert 'const BASE_PATH="";' in js_body


@pytest.mark.asyncio
async def test_protocol_relative_prefix_logs_rejection_via_proxy_fix(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """スキーム相対形式のプレフィクスは`pytilpack.quart.ProxyFix`層が拒否しWARNINGを記録する。

    404単独では既存のルート未マッチ応答と区別できないため、`pytilpack.web.validate_forwarded_prefix`が
    記録する`X-Forwarded-Prefixに不正な値が含まれています`という警告ログの有無でProxyFix層の
    拒否経路が実際に実行されたことを検証する。
    """
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    with caplog.at_level("WARNING"):
        response = await client.get("//evil.example/", headers={"X-Forwarded-Prefix": "//evil.example"})
    assert response.status_code == 404
    assert "X-Forwarded-Prefixに不正な値が含まれています" in caplog.text


def test_console_title_builds_command_and_port() -> None:
    """ターミナルタイトルにコマンド名とポートを含める。"""
    assert serve.build_console_title(28766) == "atk serve :28766"


def _stub_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    stopped: list[str],
) -> None:
    """監視スレッドを起動しないServeStateへ差し替える。"""
    current = state.ServeState(tmp_path)
    monkeypatch.setattr(current, "start", lambda loop: None)
    monkeypatch.setattr(current, "stop", lambda: stopped.append("stop"))
    monkeypatch.setattr(serve, "_atk_serve_state", types.SimpleNamespace(ServeState=lambda root: current))


@pytest.mark.asyncio
async def test_serve_shuts_down_on_signal_and_stops_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """シグナル集約でshutdown_triggerが解除され、停止時にstateを止める。"""
    stopped: list[str] = []
    _stub_state(monkeypatch, tmp_path, stopped)
    handlers: dict[int, typing.Callable[[], None]] = {}
    loop = asyncio.get_running_loop()

    def add_signal_handler(sig: int, callback: typing.Callable[[], None]) -> None:
        handlers[sig] = callback

    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    observed: dict[str, object] = {}

    async def fake_serve(app: object, hypercorn_config: typing.Any, *, shutdown_trigger: typing.Any) -> None:
        del app
        observed["bind"] = hypercorn_config.bind[0]
        observed["graceful_timeout"] = hypercorn_config.graceful_timeout
        observed["accesslog"] = hypercorn_config.accesslog
        # シグナル受信を模擬してshutdown_triggerを解除する。
        handlers[signal.SIGTERM]()
        await shutdown_trigger()

    monkeypatch.setattr(serve.hypercorn.asyncio, "serve", fake_serve)
    await serve._serve(tmp_path, config.ServeConfig("127.0.0.1", 28766))

    assert observed["bind"] == "127.0.0.1:28766"
    assert observed["graceful_timeout"] == 1.0
    assert observed["accesslog"] is None
    assert set(handlers) == {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    assert stopped == ["stop"]


@pytest.mark.asyncio
async def test_serve_tolerates_absent_and_unsupported_signals(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """シグナル不在とadd_signal_handler未実装の双方を吸収して起動する。"""
    stopped: list[str] = []
    _stub_state(monkeypatch, tmp_path, stopped)
    monkeypatch.delattr(serve.signal, "SIGHUP", raising=False)
    attempted: list[int] = []

    def add_signal_handler(sig: int, callback: typing.Callable[[], None]) -> None:
        del callback
        attempted.append(sig)
        raise NotImplementedError

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)

    async def fake_serve(app: object, hypercorn_config: object, *, shutdown_trigger: object) -> None:
        del app, hypercorn_config, shutdown_trigger

    monkeypatch.setattr(serve.hypercorn.asyncio, "serve", fake_serve)
    await serve._serve(tmp_path, config.ServeConfig("127.0.0.1", 28766))

    assert attempted == [signal.SIGINT, signal.SIGTERM]
    assert stopped == ["stop"]


def test_run_initializes_logging_and_logs_startup(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """logging初期化と起動ログ出力を実施し、hypercorn.errorの伝搬を止める。"""
    basic_config_calls: list[dict[str, object]] = []
    monkeypatch.setattr(serve.logging, "basicConfig", lambda **kwargs: basic_config_calls.append(kwargs))
    monkeypatch.setattr(serve.common, "ensure_environment", lambda home: tmp_path)
    monkeypatch.setattr(serve.asyncio, "run", lambda coro: coro.close())
    logging.getLogger("hypercorn.error").propagate = True

    with caplog.at_level("INFO", logger=serve.logger.name):
        serve.run(host="127.0.0.1", port=28766, home=tmp_path)

    assert basic_config_calls[0]["force"] is True
    assert basic_config_calls[0]["level"] == logging.INFO
    assert "http://127.0.0.1:28766/" in caplog.text
    assert logging.getLogger("hypercorn.error").propagate is False
