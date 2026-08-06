"""`atk serve`のテスト。"""

# pylint: disable=protected-access

import asyncio
import binascii
import contextlib
import json
import logging
import pathlib
import re
import signal
import struct
import subprocess
import threading
import types
import typing
import zlib

import _atk_mq_common as common
import _atk_mq_repo as feedback_repo
import _atk_serve as serve
import _atk_serve_app as serve_app
import _atk_serve_assets as assets
import _atk_serve_config as config
import _atk_serve_state as state
import filelock
import pytest
import watchdog.events


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
    assert combined.count("innerHTML") == 1
    assert "detailContent.innerHTML = entry.content_html" in assets.JS
    assert "insertAdjacentHTML" not in combined
    assert "/api/entries" in combined
    assert "/api/events" in combined
    assert "__BASE_PATH_HTML__" in assets.HTML
    assert "__BASE_PATH_JS__" in assets.JS
    assert "textContent" in assets.JS
    assert ".value" in assets.JS
    assert f'<meta name="theme-color" content="{assets.THEME_COLOR}">' in assets.HTML
    assert 'rel="icon" type="image/svg+xml" href="__BASE_PATH_HTML__/favicon.svg"' in assets.HTML
    assert 'rel="manifest" href="__BASE_PATH_HTML__/manifest.webmanifest" crossorigin="use-credentials"' in assets.HTML


def test_assets_format_dates_in_jst_and_size_editors() -> None:
    """UTC日時をJSTで表示し、編集欄の最小高を画面幅別に確保する。"""
    rendered = _run_node_ui(
        """
process.stdout.write(JSON.stringify({
  datetime: formatUpdatedAt('2026-01-01T00:00:00Z'),
  date: formatUpdatedAt('2026-01-01T00:00:00Z', 'date'),
  time: formatUpdatedAt('2026-01-01T00:00:00Z', 'time')
}));
"""
    )
    assert rendered == {"datetime": "2026/1/1 9:00:00", "date": "2026/1/1", "time": "9:00:00"}
    assert "min-height: 24rem" in assets.CSS
    assert "min-height: 16rem" in assets.CSS
    # 狭幅表示の最小高は編集欄だけを対象とする。
    # ボタン・入力欄・セレクトを含む共通規則へ置くと、それらの高さまで広がる。
    narrow = re.search(r"@media \(max-width: 700px\) \{(.*?)\n\}", assets.CSS, re.DOTALL)
    assert narrow is not None
    min_height_rules = re.findall(r"([^{}]*)\{[^{}]*min-height: 16rem[^{}]*\}", narrow.group(1))
    assert [selector.split() for selector in min_height_rules] == [["textarea"]]


def test_header_layout_and_additional_filters_structure() -> None:
    """ヘッダーを横並び、追加条件をセクション、投入元が空フィルターを検証する。"""
    header = re.search(r'<header class="app-header">(.*?)</header>', assets.HTML, re.DOTALL)
    assert header is not None
    assert re.search(
        r'<div class="header-title">\s*<h1>フィードバック管理</h1>\s*'
        r'<span id="connection-status"[^>]*>接続中</span>\s*</div>\s*'
        r'<div class="header-actions">',
        header.group(1),
    )
    assert '<section class="additional-filters" aria-labelledby="additional-filters-heading">' in assets.HTML
    assert '<h3 id="additional-filters-heading">追加条件</h3>' in assets.HTML
    assert '<label class="checkbox-field" for="source-empty-filter">' in assets.HTML
    assert '<input id="source-empty-filter" type="checkbox">' in assets.HTML
    assert "投入元が空" in assets.HTML
    assert 'details class="additional-filters"' not in assets.HTML

    header_title_rule = re.search(r"\.header-title\s*\{([^}]*)\}", assets.CSS)
    assert header_title_rule is not None
    assert "display: flex;" in header_title_rule.group(1)
    assert "align-items: center;" in header_title_rule.group(1)

    assert ".header-actions button {" in assets.CSS
    assert "padding: var(--space-1) var(--space-2);" in assets.CSS
    assert ".checkbox-field {" in assets.CSS
    assert "grid-column: 2;" in assets.CSS

    grid_rule = re.search(r"\.entry-columns,\s*\.entry-select\s*\{([^}]*)\}", assets.CSS)
    assert grid_rule is not None
    assert "display: grid;" in grid_rule.group(1)
    assert "grid-template-columns:" in grid_rule.group(1)
    assert re.findall(r"minmax\([^)]+\)", grid_rule.group(1)) == [
        "minmax(12rem, 1.6fr)",
        "minmax(10rem, 1fr)",
        "minmax(11rem, 1.2fr)",
        "minmax(7rem, 0.7fr)",
        "minmax(16rem, 2fr)",
    ]

    filename_rule = re.search(r"\.entry-cell\.filename-cell\s*\{([^}]*)\}", assets.CSS)
    assert filename_rule is not None
    assert "white-space: nowrap;" in filename_rule.group(1)
    assert "overflow: hidden;" in filename_rule.group(1)
    assert "text-overflow: ellipsis;" in filename_rule.group(1)

    target_repo_rule = re.search(r"\.entry-cell\.target-repo-cell\s*\{([^}]*)\}", assets.CSS)
    assert target_repo_rule is not None
    assert "white-space: nowrap;" in target_repo_rule.group(1)
    assert "overflow: hidden;" in target_repo_rule.group(1)
    assert "text-overflow: ellipsis;" in target_repo_rule.group(1)

    responsive = assets.CSS.partition("@media (max-width: 1280px) {")[2].partition("@media (max-width: 700px) {")[0]
    assert ".entry-columns {\n    display: none;\n  }" in responsive
    assert "grid-template-columns: minmax(7rem, 40%) minmax(0, 1fr);" in responsive
    mobile = assets.CSS.partition("@media (max-width: 700px) {")[2]
    assert ".checkbox-field {\n    grid-column: auto;\n  }" in mobile


def test_all_dialogs_have_accessible_names() -> None:
    """追加、削除、未保存確認を含む全dialogを固有見出しで命名する。"""
    expected = {
        "detail-dialog": "detail-heading",
        "create-dialog": "create-dialog-heading",
        "delete-dialog": "delete-dialog-heading",
        "unsaved-dialog": "unsaved-dialog-heading",
    }
    for dialog_id, heading_id in expected.items():
        dialog = re.search(rf'<dialog id="{dialog_id}"([^>]*)>', assets.HTML)
        assert dialog is not None
        assert f'aria-labelledby="{heading_id}"' in dialog.group(1)
        assert f'id="{heading_id}"' in assets.HTML


def test_detail_view_uses_responsive_two_column_layout() -> None:
    """詳細閲覧を横長画面では2列、狭い画面では1列に配置する。"""
    assert '<section id="detail-view" class="detail-view" hidden>' in assets.HTML
    assert '<div class="detail-summary">' in assets.HTML
    assert '<div class="detail-body">' in assets.HTML
    assert ".detail-view {" in assets.CSS
    assert "grid-template-columns: minmax(18rem, 0.35fr) minmax(0, 1fr);" in assets.CSS
    assert ".detail-view[hidden] {" in assets.CSS
    assert ".detail-summary,\n.detail-body {\n  min-width: 0;\n}" in assets.CSS
    assert ".detail-body > h3 {\n  margin-top: 0;\n}" in assets.CSS
    assert ".detail-actions {\n  grid-column: 1 / -1;\n}" in assets.CSS
    frontmatter_pre_rule = re.search(r"\.frontmatter pre\s*\{([^}]*)\}", assets.CSS)
    assert frontmatter_pre_rule is not None
    assert [line.strip() for line in frontmatter_pre_rule.group(1).splitlines() if line.strip()] == [
        "white-space: pre-wrap;",
        "overflow-wrap: anywhere;",
    ]
    mobile = assets.CSS.partition("@media (max-width: 700px) {")[2]
    assert ".detail-view {\n    grid-template-columns: 1fr;\n    gap: var(--space-3);\n  }" in mobile
    assert ".detail-actions {\n    grid-column: auto;\n  }" in mobile


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
  close() {{
    this.open = false;
    if (this.listeners.close) this.listeners.close({{currentTarget: this}});
  }}
  focus() {{ globalThis.focused = this.dataset.key || this.id; }}
  reset() {{ globalThis.formReset = true; }}
}}
const ids = [
  'connection-status', 'refresh-button', 'create-button', 'global-error',
  'search-input', 'kind-filter', 'state-filter', 'answer-filter', 'target-filter',
  'category-filter', 'source-filter', 'source-empty-filter', 'entry-heading', 'entry-count',
  'loading-indicator', 'entry-list', 'other-entry-group', 'other-entry-heading',
  'other-entry-count', 'other-entry-list', 'empty-state', 'empty-create-button', 'detail-dialog',
  'detail-close-button',
  'detail-view', 'detail-filename', 'detail-state', 'detail-metadata',
  'detail-content', 'readonly-notice', 'detail-actions', 'edit-button', 'answer-button',
  'delete-button', 'edit-panel', 'edit-content', 'edit-content-error',
  'cancel-edit-button', 'save-entry-button', 'answer-panel', 'answer-input',
  'answer-input-error', 'save-answer-button', 'cancel-answer-button', 'create-dialog', 'create-form',
  'create-kind', 'create-content', 'create-content-error', 'create-target',
  'create-target-error', 'create-source', 'tbd-fields', 'create-scope',
  'create-question-type', 'choice-fields', 'create-choices',
  'create-choices-error', 'cancel-create-button', 'delete-dialog', 'delete-form',
  'delete-target', 'delete-state', 'force-delete-row', 'repo-options',
  'force-delete-confirmation', 'delete-error', 'cancel-delete-button', 'unsaved-dialog',
  'discard-changes-button', 'continue-editing-button', 'toast'
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
    entry["content_html"] = "&lt;p&gt;整形済み&lt;/p&gt;"
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
  detail: elements['detail-content'].innerHTML,
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
    assert rendered["detail"] == "&lt;p&gt;整形済み&lt;/p&gt;"
    assert rendered["detailState"] == dangerous
    assert rendered["editValue"] == dangerous
    assert rendered["deleteTarget"] == dangerous
    assert rendered["deleteState"] == "未処理"
    assert rendered["toast"] == dangerous
    assert rendered["globalError"] == dangerous
    assert rendered["inlineError"] == dangerous
    assert rendered["childCounts"] == [0, 0, 0, 0, 0]


def test_render_entry_displays_all_comparison_columns() -> None:
    """一覧行へ5列（ファイル名・対象リポジトリ・状態集約・更新日時・要約）を表示し、詳細にはカテゴリと投入元を含める。"""
    columns = re.search(r'<div class="entry-columns"[^>]*>(.*?)</div>', assets.HTML, re.DOTALL)
    assert columns is not None
    assert re.findall(r"<span>(.*?)</span>", columns.group(1)) == [
        "ファイル名",
        "対象リポジトリ",
        "種別・状態・回答状況",
        "更新日時",
        "要約",
    ]
    rendered = _run_node_ui(
        """
const entry = {
  target_repo: 'example/repo',
  kind: 'tbd',
  state: 'processing',
  answered: false,
  category: 'ui',
  source: 'session-review',
  updated_at: '2025-07-30T12:34:56Z',
  summary: '要約本文',
  filename: 'entry.md',
  content: '本文',
  content_html: '<p>本文</p>'
};
const row = renderEntry(entry);
const cells = row.children[0].children;
const timeCell = cells[3];
const timeElements = timeCell.children[0] ? timeCell.children[0].children : [];
displayEntry(entry);
const metadata = elements['detail-metadata'].children;
const timeFallbacks = [null, 'not-a-date'].map(updatedAt => {
  const fallbackRow = renderEntry({...entry, updated_at: updatedAt});
  const time = fallbackRow.children[0].children[3].children[0];
  return {
    dateTime: time.dateTime,
    accessibleName: time.attributes['aria-label'],
    lines: time.children.map(child => child.textContent)
  };
});
process.stdout.write(JSON.stringify({
  labels: cells.map(cell => cell.dataset.label),
  classes: cells.map(cell => cell.className),
  displayedValues: cells.map(cell => cell.textContent.trim().split(/\\s+/).slice(0, 3).join(' ')),
  accessibleName: row.children[0].attributes['aria-label'],
  hasTimeElement: timeCell.children[0] ? timeCell.children[0].tagName : null,
  timeDateTimeAttr: timeCell.children[0] ? timeCell.children[0].dateTime : null,
  timeHasDateAndTime: timeElements.length === 2,
  metadataLabels: metadata.filter((_, index) => index % 2 === 0).map(node => node.textContent),
  metadataValues: metadata.filter((_, index) => index % 2 === 1).map(node => node.textContent),
  timeFallbacks
}));
"""
    )
    assert rendered["labels"] == ["ファイル名", "対象リポジトリ", "種別・状態・回答状況", "更新日時", "要約"]
    assert rendered["displayedValues"][0] == "entry.md"
    assert rendered["displayedValues"][1] == "example/repo"
    assert rendered["classes"][1] == "entry-cell target-repo-cell"
    # 状態集約セルは複数のバッジを含む
    assert "確認事項" in rendered["accessibleName"]
    assert "処理中" in rendered["accessibleName"]
    assert "未回答" in rendered["accessibleName"]
    # カテゴリと投入元は行に表示されず、詳細メタデータに含まれる
    assert "カテゴリ" not in rendered["labels"]
    assert "投入元" not in rendered["labels"]
    assert "example/repo" in rendered["accessibleName"]
    assert "要約本文" in rendered["accessibleName"]
    assert rendered["hasTimeElement"] == "TIME"
    assert rendered["timeDateTimeAttr"] == "2025-07-30T12:34:56Z"
    assert rendered["timeHasDateAndTime"] is True
    category_index = rendered["metadataLabels"].index("カテゴリ")
    source_index = rendered["metadataLabels"].index("投入元")
    assert rendered["metadataValues"][category_index] == "ui"
    assert rendered["metadataValues"][source_index] == "session-review"
    assert rendered["timeFallbacks"] == [
        {"dateTime": "", "accessibleName": "更新日時なし", "lines": ["更新日時なし", ""]},
        {"dateTime": "not-a-date", "accessibleName": "not-a-date", "lines": ["not-a-date", ""]},
    ]


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
        "/api/entries/adopt",
        "/api/entries/commit",
        "/api/entries/reject",
    )
    combined = assets.HTML + assets.JS
    assert all(value not in combined for value in forbidden)
    result = _run_node_ui(
        """
bindEvents();
const renderContent = entry => ({...entry, content_html: `<p>${entry.content}</p>`});
let detail = renderContent({
  kind: 'tbd', state: 'inbox', filename: 'entry.md', answered: false,
  target_repo: 'example/repo', source: 'web', category: null,
  summary: '確認', updated_at: '2026-01-01T00:00:00+00:00', content: '取得時本文'
});
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
    detail = renderContent({...detail, content: JSON.parse(options.body).content});
    body = {changed: true};
  } else if (url.endsWith('/api/entries/answer')) {
    const answer = JSON.parse(options.body).answer;
    detail = renderContent({...detail, answered: true, content: `${detail.content}\\n${answer}`});
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
await selectEntry(detail, elements['entry-list'].children[0].children[0]);
enterEdit();
elements['edit-content'].value = '編集本文';
await saveEntry();
const editResult = {
  detail: elements['detail-content'].innerHTML,
  toast: elements['toast'].textContent
};
await selectEntry(detail, elements['entry-list'].children[0].children[0]);
enterAnswer();
elements['answer-input'].value = '回答本文';
await saveAnswer();
const answerResult = {
  detail: elements['detail-content'].innerHTML,
  answered: detail.answered,
  toast: elements['toast'].textContent
};
await selectEntry(detail, elements['entry-list'].children[0].children[0]);
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
    dialogOpen: elements['detail-dialog'].open,
    selected: currentEntry,
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
        "first": "entry.md",
        "toast": "項目を追加しました。",
    }
    assert result["editResult"] == {"detail": "<p>編集本文</p>", "toast": "本文を保存しました。"}
    assert result["answerResult"] == {
        "detail": "<p>編集本文\n回答本文</p>",
        "answered": True,
        "toast": "回答を保存しました。",
    }
    assert result["deleteResult"] == {
        "count": "0件",
        "listLength": 0,
        "dialogOpen": False,
        "selected": None,
        "toast": "項目を削除しました。",
    }


def test_assets_present_search_filters_count_and_empty_state() -> None:
    """検索、フィルター、一覧分割、並び順、件数、読込中、空状態を検証する。"""
    assert '<h2 id="other-entry-heading">その他一覧</h2>' in assets.HTML
    result = _run_node_ui(
        """
bindEvents();
entries = [
  {kind: 'feedback', state: 'inbox', filename: 'old.md', summary: '対象外',
   answered: null, target_repo: 'other/repo', category: 'x', source: 'cli', updated_at: '2025-01-01'},
  {kind: 'tbd', state: 'processing', filename: 'new.md', summary: '探す文字',
   answered: false, target_repo: 'example/repo', category: 'category-token', source: 'source-token', updated_at: '2026-01-01'},
  {kind: 'tbd', state: 'inbox', filename: 'answered.md', summary: '回答済み',
   answered: true, target_repo: 'example/repo', category: null, source: 'web', updated_at: '2025-07-01'},
  {kind: 'tbd', state: 'adopted', filename: 'finished.md', summary: '完了状態',
   answered: false, target_repo: 'example/repo', category: null, source: 'web', updated_at: '2025-08-01'},
  {kind: 'feedback', state: 'inbox', filename: 'empty-source.md', summary: '投入元なし',
   answered: null, target_repo: 'example/repo', category: null, source: null, updated_at: '2025-06-01'}
];
applyClientFilters();
const splitState = {
  heading: elements['entry-heading'].textContent,
  count: elements['entry-count'].textContent,
  primaryKeys: elements['entry-list'].children.map(item => item.children[0].dataset.key),
  otherHidden: elements['other-entry-group'].hidden,
  otherCount: elements['other-entry-count'].textContent,
  otherKeys: elements['other-entry-list'].children.map(item => item.children[0].dataset.key)
};
elements['search-input'].value = '回答済み';
applyClientFilters();
const unsplitState = {
  heading: elements['entry-heading'].textContent,
  count: elements['entry-count'].textContent,
  primaryKeys: elements['entry-list'].children.map(item => item.children[0].dataset.key),
  otherHidden: elements['other-entry-group'].hidden,
  otherCount: elements['other-entry-count'].textContent,
  otherKeys: elements['other-entry-list'].children.map(item => item.children[0].dataset.key)
};
elements['search-input'].value = '探す';
elements['target-filter'].value = 'example/repo';
elements['category-filter'].value = 'category-token';
elements['source-filter'].value = 'source-token';
applyClientFilters();
const populated = {
  count: elements['entry-count'].textContent,
  first: elements['entry-list'].children[0].children[0].dataset.key,
  emptyHidden: elements['empty-state'].hidden,
  query: listQuery()
};
elements['target-filter'].value = '';
elements['category-filter'].value = '';
elements['source-filter'].value = '';
elements['search-input'].value = 'category-token';
applyClientFilters();
const categorySearchKeys = elements['entry-list'].children.map(item => item.children[0].dataset.key);
elements['search-input'].value = 'source-token';
applyClientFilters();
const sourceSearchKeys = elements['entry-list'].children.map(item => item.children[0].dataset.key);
setLoading(true);
const loadingState = {
  visible: !elements['loading-indicator'].hidden,
  primaryBusy: elements['entry-list'].attributes['aria-busy'],
  otherBusy: elements['other-entry-list'].attributes['aria-busy']
};
elements['search-input'].value = '一致しない';
setLoading(false);
const loadedState = {
  primaryBusy: elements['entry-list'].attributes['aria-busy'],
  otherBusy: elements['other-entry-list'].attributes['aria-busy']
};
applyClientFilters();
elements['source-filter'].value = 'web';
elements['source-empty-filter'].checked = true;
elements['source-empty-filter'].listeners.change({currentTarget: elements['source-empty-filter']});
const emptySourceQuery = listQuery();
const sourceFilterDisabledAfterCheck = elements['source-filter'].disabled;
elements['source-empty-filter'].checked = false;
elements['source-empty-filter'].listeners.change({currentTarget: elements['source-empty-filter']});
const emptySourceState = {
  sourceFilterValue: elements['source-filter'].value,
  sourceFilterDisabledAfterCheck,
  sourceFilterEnabledAfterUncheck: !elements['source-filter'].disabled,
  query: emptySourceQuery
};
process.stdout.write(JSON.stringify({
  splitState,
  unsplitState,
  populated,
  loadingState,
  loadedState,
  categorySearchKeys,
  sourceSearchKeys,
  emptyVisible: !elements['empty-state'].hidden,
  emptyCount: elements['entry-count'].textContent,
  emptySourceState
}));
"""
    )
    assert result["splitState"] == {
        "heading": "未完了TBD",
        "count": "1件",
        "primaryKeys": ["new.md"],
        "otherHidden": False,
        "otherCount": "4件",
        "otherKeys": ["old.md", "answered.md", "finished.md", "empty-source.md"],
    }
    assert result["unsplitState"] == {
        "heading": "未完了TBD",
        "count": "1件",
        "primaryKeys": ["new.md"],
        "otherHidden": False,
        "otherCount": "4件",
        "otherKeys": ["old.md", "answered.md", "finished.md", "empty-source.md"],
    }
    assert result["populated"]["count"] == "1件"
    assert result["populated"]["first"] == "new.md"
    assert result["populated"]["emptyHidden"] is True
    assert "target_repo=example%2Frepo" in result["populated"]["query"]
    assert "category=category-token" in result["populated"]["query"]
    assert "source=source-token" in result["populated"]["query"]
    assert result["categorySearchKeys"] == ["new.md"]
    assert result["sourceSearchKeys"] == ["new.md"]
    assert result["loadingState"] == {"visible": True, "primaryBusy": "true", "otherBusy": "true"}
    assert result["loadedState"] == {"primaryBusy": "false", "otherBusy": "false"}
    assert result["emptyVisible"] is False
    assert result["emptyCount"] == "1件"
    assert result["emptySourceState"]["sourceFilterValue"] == ""
    assert result["emptySourceState"]["sourceFilterDisabledAfterCheck"] is True
    assert result["emptySourceState"]["sourceFilterEnabledAfterUncheck"] is True
    assert "source_empty=true" in result["emptySourceState"]["query"]
    assert "source=" not in result["emptySourceState"]["query"]
    assert "q=%E4%B8%80%E8%87%B4%E3%81%97%E3%81%AA%E3%81%84" in result["emptySourceState"]["query"]


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
    content: '本文', content_html: '<p>本文</p>', updated_at: '2026-01-01'
  });
  result[state] = {
    actions: !elements['detail-actions'].hidden,
    edit: !elements['edit-button'].hidden,
    remove: !elements['delete-button'].hidden,
    readonly: !elements['readonly-notice'].hidden,
    answer: !elements['answer-button'].hidden
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
    """SSE更新と遅延した詳細応答から編集中の表示・入力値・競合基準を保護する。"""
    assert "loadEntries({fromSse: true});" in assets.JS
    assert "events.addEventListener('changed', () => {" in assets.JS
    result = _run_node_ui(
        """
const listed = {
  kind: 'tbd', state: 'inbox', filename: 'entry.md', answered: false,
  summary: '更新後一覧', updated_at: '2026-02-01'
};
let resolveDetail;
let deferDetail = false;
fetchHandler = async (url, options) => {
  if (!options.method || options.method === 'GET') {
    if (deferDetail && url.includes('/api/entries/inbox/')) {
      return new Promise(resolve => { resolveDetail = resolve; });
    }
    const body = url.includes('/api/entries/inbox/')
      ? {entry: {...listed, content: 'SSE再描画本文', content_html: '<p>SSE再描画本文</p>'}}
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
detailOriginKey = entryKey(currentEntry);
editing = false;
elements['detail-dialog'].open = true;
await loadEntries({fromSse: true});
const redrawnDetail = elements['detail-content'].innerHTML;
const detailRequests = fetchCalls.filter(call => call.url.includes('/api/entries/inbox/')).length;
currentEntry = {...listed, content: '取得時本文', content_html: '<p>取得時本文</p>'};
displayEntry(currentEntry);
deferDetail = true;
const pendingDetail = renderDetail(listed);
enterEdit();
elements['edit-content'].value = '利用者の本文';
elements['answer-input'].value = '利用者の回答';
resolveDetail({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entry: {...listed, content: '遅延した詳細本文', content_html: '<p>遅延した詳細本文</p>'}})
});
await pendingDetail;
const delayedResponse = {
  currentContent: currentEntry.content,
  displayedContent: elements['detail-content'].innerHTML,
  error: elements['global-error'].textContent
};
deferDetail = false;
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
  delayedResponse,
  error: elements['global-error'].textContent
}));
"""
    )
    assert result["firstUrl"].startswith("/atk/api/entries?")
    assert result["redrawnDetail"] == "<p>SSE再描画本文</p>"
    assert result["detailRequests"] == 1
    assert result["selected"] == "entry.md"
    assert result["content"] == "利用者の本文"
    assert result["answer"] == "利用者の回答"
    assert result["editBaseline"] == result["answerBaseline"] == "取得時本文"
    assert result["delayedResponse"] == {
        "currentContent": "取得時本文",
        "displayedContent": "<p>取得時本文</p>",
        "error": "外部で項目が更新されました。編集中の入力を保持しています。保存前に詳細を再読込してください。",
    }
    assert "入力内容を保持" in result["error"]
    assert "/api/sync" not in result["firstUrl"]


def test_list_reload_tracks_selection_across_state_transition() -> None:
    """状態移動後も同じファイル名の選択を維持し、最新状態の詳細を取得する。"""
    result = _run_node_ui(
        """
const inbox = {
  kind: 'tbd', state: 'inbox', filename: 'entry.md', answered: true,
  summary: '移動前一覧', updated_at: '2026-02-01',
  content: '移動前本文', content_html: '<p>移動前本文</p>'
};
const processing = {
  ...inbox, state: 'processing', summary: '移動後一覧',
  updated_at: '2026-02-02', content: '移動後本文', content_html: '<p>移動後本文</p>'
};
let listEntries = [processing];
fetchHandler = async url => {
  const body = url.includes('/api/entries/processing/')
    ? {entry: processing}
    : {entries: listEntries};
  return {ok: true, status: 200, statusText: 'OK', json: async () => body};
};
currentEntry = inbox;
detailOriginKey = entryKey(inbox);
elements['detail-dialog'].open = true;
await loadEntries({fromSse: true});
const afterTransition = {
  dialogOpen: elements['detail-dialog'].open,
  selectedFilename: currentEntry.filename,
  selectedState: currentEntry.state,
  detailState: elements['detail-state'].textContent,
  detailContent: elements['detail-content'].innerHTML,
  detailUrls: fetchCalls
    .map(call => call.url)
    .filter(url => !url.includes('/api/entries?'))
};
listEntries = [];
await loadEntries({fromSse: true});
process.stdout.write(JSON.stringify({
  afterTransition,
  afterDisappearance: {
    dialogOpen: elements['detail-dialog'].open,
    selected: currentEntry
  }
}));
"""
    )
    assert result["afterTransition"] == {
        "dialogOpen": True,
        "selectedFilename": "entry.md",
        "selectedState": "processing",
        "detailState": "処理中",
        "detailContent": "<p>移動後本文</p>",
        "detailUrls": ["/atk/api/entries/processing/entry.md"],
    }
    assert result["afterDisappearance"]["dialogOpen"] is False


def test_sse_disappearance_confirms_before_discarding_unsaved_input() -> None:
    """SSE一覧から選択項目が消えても未保存本文を保持して確認する。"""
    result = _run_node_ui(
        """
const entry = {
  kind: 'feedback', state: 'inbox', filename: 'entry.md', answered: null,
  content: '取得時本文', content_html: '<p>取得時本文</p>'
};
currentEntry = entry;
detailOriginKey = entryKey(entry);
elements['detail-dialog'].open = true;
enterEdit();
elements['edit-content'].value = '未保存本文';
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK', json: async () => ({entries: []})
});
await loadEntries({fromSse: true});
process.stdout.write(JSON.stringify({
  detailOpen: elements['detail-dialog'].open,
  confirmationOpen: elements['unsaved-dialog'].open,
  editing,
  content: elements['edit-content'].value
}));
"""
    )
    assert result == {
        "detailOpen": True,
        "confirmationOpen": True,
        "editing": True,
        "content": "未保存本文",
    }


def test_detail_discards_out_of_order_response_for_previous_selection() -> None:
    """選択項目を変更した後に完了した古い詳細応答を画面へ反映しない。"""
    result = _run_node_ui(
        """
const first = {kind: 'feedback', state: 'inbox', filename: 'a.md', content: 'A本文', content_html: '<p>A本文</p>'};
const second = {kind: 'feedback', state: 'inbox', filename: 'b.md', content: 'B本文', content_html: '<p>B本文</p>'};
const resolvers = {};
fetchHandler = async url => new Promise(resolve => {
  resolvers[url.endsWith('/a.md') ? 'a' : 'b'] = resolve;
});
const firstRequest = selectEntry(first, new Element('a-origin'));
const secondRequest = selectEntry(second, new Element('b-origin'));
resolvers.b({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entry: second})
});
await secondRequest;
resolvers.a({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entry: first})
});
await firstRequest;
process.stdout.write(JSON.stringify({
  selected: currentEntry.filename,
  content: elements['detail-content'].innerHTML,
  key: detailOriginKey
}));
"""
    )
    assert result == {"selected": "b.md", "content": "<p>B本文</p>", "key": "b.md"}


def test_stale_list_reload_does_not_invalidate_current_detail_request() -> None:
    """一覧再読込中の選択変更後は、古い選択の詳細要求を開始しない。"""
    result = _run_node_ui(
        """
const first = {kind: 'feedback', state: 'inbox', filename: 'a.md', content: 'A本文', content_html: '<p>A本文</p>'};
const second = {kind: 'feedback', state: 'inbox', filename: 'b.md', content: 'B本文', content_html: '<p>B本文</p>'};
currentEntry = first;
elements['detail-dialog'].open = true;
let resolveList;
let resolveSecond;
fetchHandler = async url => {
  if (url.includes('/api/entries?')) return new Promise(resolve => { resolveList = resolve; });
  return new Promise(resolve => { resolveSecond = resolve; });
};
const staleReload = loadEntries();
const secondRequest = selectEntry(second, new Element('b-origin'));
resolveList({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entries: [first, second]})
});
await staleReload;
resolveSecond({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entry: second})
});
await secondRequest;
process.stdout.write(JSON.stringify({
  selected: currentEntry.filename,
  content: elements['detail-content'].innerHTML,
  generation: detailRequestGeneration
}));
"""
    )
    assert result == {"selected": "b.md", "content": "<p>B本文</p>", "generation": 1}


def test_detail_discards_older_error_for_same_selection() -> None:
    """同じ選択項目の古い404応答は最新の詳細表示を閉じず、エラーも表示しない。"""
    result = _run_node_ui(
        """
const entry = {kind: 'feedback', state: 'inbox', filename: 'entry.md', content: '一覧本文', content_html: '<p>一覧本文</p>'};
detailOriginKey = entryKey(entry);
elements['detail-dialog'].open = true;
const resolvers = [];
fetchHandler = async () => new Promise(resolve => { resolvers.push(resolve); });
const older = renderDetail(entry, {closeWhenMissing: true});
const newer = renderDetail(entry, {closeWhenMissing: true});
resolvers[1]({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entry: {...entry, content: '最新本文', content_html: '<p>最新本文</p>'}})
});
await newer;
resolvers[0]({
  ok: false, status: 404, statusText: 'Not Found',
  json: async () => ({error: '見つかりません'})
});
await older;
process.stdout.write(JSON.stringify({
  selected: currentEntry.filename,
  content: elements['detail-content'].innerHTML,
  open: elements['detail-dialog'].open,
  error: elements['global-error'].textContent
}));
"""
    )
    assert result == {
        "selected": "entry.md",
        "content": "<p>最新本文</p>",
        "open": True,
        "error": "",
    }


def test_initial_and_manual_sync_always_fall_back_to_local_entries() -> None:
    """同期の成功、409、Git失敗の各結果後にローカル一覧を取得して利用可能状態へ戻す。"""
    assert "byId('refresh-button').addEventListener('click', synchronizeAndLoad)" in assets.JS
    assert assets.JS.rstrip().endswith("synchronizeAndLoad();")
    result = _run_node_ui(
        """
let syncResult = 'success';
fetchHandler = async (url, options) => {
  if (url.endsWith('/api/sync')) {
    if (syncResult === 'success') {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({synced: true})};
    }
    const conflict = syncResult === 'conflict';
    return {
      ok: false,
      status: conflict ? 409 : 500,
      statusText: conflict ? 'Conflict' : 'Internal Server Error',
      json: async () => ({
        error: conflict ? '別の操作が進行中です' : 'Git同期に失敗しました',
        code: conflict ? 'lock_conflict' : undefined
      })
    };
  }
  return {
    ok: true, status: 200, statusText: 'OK',
    json: async () => ({entries: [{
      kind: 'feedback', state: 'inbox', filename: 'local.md',
      summary: 'ローカル一覧', updated_at: '2026-01-01'
    }]})
  };
};
await Promise.all([synchronizeAndLoad(), synchronizeAndLoad()]);
const parallelAvailable = !loading && elements['entry-count'].textContent === '1件';
syncResult = 'conflict';
await synchronizeAndLoad();
const conflictError = elements['global-error'].textContent;
syncResult = 'git';
await synchronizeAndLoad();
const gitError = elements['global-error'].textContent;
process.stdout.write(JSON.stringify({
  parallelAvailable,
  conflictError,
  gitError,
  syncCalls: fetchCalls.filter(call => call.url.endsWith('/api/sync')).length,
  entryCalls: fetchCalls.filter(call => call.url.includes('/api/entries?')).length,
  available: !loading && elements['entry-list'].children.length === 1
}));
"""
    )
    assert result == {
        "parallelAvailable": True,
        "conflictError": "別の操作が進行中です",
        "gitError": "Git同期に失敗しました",
        "syncCalls": 4,
        "entryCalls": 4,
        "available": True,
    }


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


def test_detail_dialog_opens_and_restores_row_focus_after_close_and_escape() -> None:
    """詳細ダイアログを選択時に開き、閉じる操作とEscape後に起点行へフォーカスを戻す。"""
    result = _run_node_ui(
        """
bindEvents();
const entry = {
  kind: 'feedback', state: 'inbox', filename: 'entry.md', answered: null,
  target_repo: 'example/repo', source: 'web', category: 'ui',
  summary: '要約', updated_at: '2026-01-01', content: '本文', content_html: '<p>本文</p>'
};
entries = [entry];
applyClientFilters();
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK', json: async () => ({entry})
});
await selectEntry(entry, elements['entry-list'].children[0].children[0]);
const opened = elements['detail-dialog'].open;
elements['detail-close-button'].listeners.click();
const closeFocus = globalThis.focused;

await selectEntry(entry, elements['entry-list'].children[0].children[0]);
let prevented = false;
elements['detail-dialog'].listeners.cancel({preventDefault() { prevented = true; }});
if (!prevented) elements['detail-dialog'].close();
const escapeFocus = globalThis.focused;

await selectEntry(entry, elements['entry-list'].children[0].children[0]);
enterEdit();
elements['edit-content'].value = '未保存本文';
let editPrevented = false;
elements['detail-dialog'].listeners.cancel({preventDefault() { editPrevented = true; }});
if (!editPrevented) elements['detail-dialog'].close();
process.stdout.write(JSON.stringify({
  opened,
  closeFocus,
  escapeFocus,
  editPrevented,
  editingOpen: elements['detail-dialog'].open
}));
"""
    )
    assert result == {
        "opened": True,
        "closeFocus": "entry.md",
        "escapeFocus": "entry.md",
        "editPrevented": True,
        "editingOpen": True,
    }


def test_detail_close_click_does_not_force_close_while_editing() -> None:
    """閉じるクリックのMouseEventを強制終了引数として扱わず、編集中の入力を保持する。"""
    result = _run_node_ui(
        """
bindEvents();
currentEntry = {
  kind: 'feedback', state: 'inbox', filename: 'entry.md',
  answered: null, content: '取得時本文'
};
elements['detail-dialog'].open = true;
enterEdit();
elements['edit-content'].value = '未保存本文';
elements['detail-close-button'].listeners.click({type: 'click'});
process.stdout.write(JSON.stringify({
  open: elements['detail-dialog'].open,
  editing,
  content: elements['edit-content'].value
}));
"""
    )
    assert result == {"open": True, "editing": True, "content": "未保存本文"}


def test_body_edit_and_answer_are_exclusive_and_confirm_unsaved_exit() -> None:
    """本文編集と回答を同時表示せず、dirty状態の終了を確認する。"""
    result = _run_node_ui(
        """
bindEvents();
currentEntry = {
  kind: 'tbd', state: 'inbox', filename: 'entry.md', answered: false,
  content: '取得時本文', content_html: '<p>取得時本文</p>'
};
elements['detail-dialog'].open = true;
enterEdit();
const bodyMode = {
  body: !elements['edit-panel'].hidden,
  answer: !elements['answer-panel'].hidden
};
elements['edit-content'].value = '未保存本文';
elements['cancel-edit-button'].listeners.click({type: 'click'});
const confirmation = {
  open: elements['unsaved-dialog'].open,
  editing,
  value: elements['edit-content'].value
};
continueEditing();
const continued = {
  open: elements['unsaved-dialog'].open,
  editing,
  value: elements['edit-content'].value
};
cancelEdit();
discardChanges();
enterAnswer();
const answerMode = {
  body: !elements['edit-panel'].hidden,
  answer: !elements['answer-panel'].hidden
};
elements['answer-input'].value = '未保存回答';
elements['cancel-answer-button'].listeners.click({type: 'click'});
const answerCancelConfirmation = {
  open: elements['unsaved-dialog'].open,
  editing,
  answer: elements['answer-input'].value
};
continueEditing();
elements['answer-input'].value = '   ';
documentListeners.keydown({
  key: 'Escape', ctrlKey: false, metaKey: false, altKey: false,
  preventDefault() {}
});
const closeConfirmation = {
  detailOpen: elements['detail-dialog'].open,
  confirmationOpen: elements['unsaved-dialog'].open,
  answer: elements['answer-input'].value
};
process.stdout.write(JSON.stringify({
  bodyMode, confirmation, continued, answerMode, answerCancelConfirmation, closeConfirmation
}));
"""
    )
    assert result == {
        "bodyMode": {"body": True, "answer": False},
        "confirmation": {"open": True, "editing": True, "value": "未保存本文"},
        "continued": {"open": False, "editing": True, "value": "未保存本文"},
        "answerMode": {"body": False, "answer": True},
        "answerCancelConfirmation": {"open": True, "editing": True, "answer": "未保存回答"},
        "closeConfirmation": {"detailOpen": True, "confirmationOpen": True, "answer": "   "},
    }


def test_stale_list_response_cannot_overwrite_newer_conditions() -> None:
    """古い一覧応答を無視し、全要求完了まで読込状態を維持する。"""
    result = _run_node_ui(
        """
bindEvents();
const resolvers = [];
fetchHandler = async () => new Promise(resolve => { resolvers.push(resolve); });
elements['search-input'].value = '古い';
const older = loadEntries();
elements['search-input'].value = '新しい';
elements['search-input'].listeners.input();
const newer = loadEntries();
resolvers[1]({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entries: [{kind: 'feedback', state: 'inbox', filename: 'new.md'}]})
});
await newer;
const whileOlderPending = {
  keys: elements['entry-list'].children.map(item => item.children[0].dataset.key),
  loading,
  pendingListRequests
};
resolvers[0]({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entries: [{kind: 'feedback', state: 'inbox', filename: 'old.md'}]})
});
await older;
process.stdout.write(JSON.stringify({
  urls: fetchCalls.map(call => call.url),
  whileOlderPending,
  finalKeys: elements['entry-list'].children.map(item => item.children[0].dataset.key),
  loading,
  pendingListRequests,
  error: elements['global-error'].textContent
}));
"""
    )
    assert result["urls"] == [
        "/atk/api/entries?type=all&status=active&answered=all&q=%E5%8F%A4%E3%81%84",
        "/atk/api/entries?type=all&status=active&answered=all&q=%E6%96%B0%E3%81%97%E3%81%84",
    ]
    assert result["whileOlderPending"] == {"keys": ["new.md"], "loading": True, "pendingListRequests": 1}
    assert result["finalKeys"] == ["new.md"]
    assert result["loading"] is False
    assert result["pendingListRequests"] == 0
    assert result["error"] == ""


def test_assets_keep_keyboard_and_narrow_viewport_support() -> None:
    """保存、検索、Escapeのキー操作と狭幅表示を検証する。"""
    assert "@media (max-width: 700px)" in assets.CSS
    assert "@media (prefers-reduced-motion: reduce)" in assets.CSS
    assert ".entry-cell::before" in assets.CSS
    assert "content: attr(data-label)" in assets.CSS
    assert "width: calc(100% - 1rem)" in assets.CSS
    assert 'id="detail-dialog"' in assets.HTML
    assert 'aria-labelledby="detail-heading"' in assets.HTML
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
elements['detail-dialog'].open = true;
documentListeners.keydown({
  key: 'Escape', ctrlKey: false, metaKey: false, altKey: false,
  preventDefault() { preventions.push('detail-escape'); }
});
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
        "preventions": ["save", "command-save", "search", "detail-escape"],
        "focused": "search-input",
        "dialogOpen": False,
        "editing": False,
    }


def test_state_keeps_latest_event(tmp_path: pathlib.Path) -> None:
    """状態管理を構築できることを検証する。"""
    current = state.ServeState(tmp_path)
    assert current.root == tmp_path


@pytest.mark.asyncio
async def test_state_publishes_only_markdown_change_events(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Markdownの変更イベントだけを購読者へ通知する。"""
    events = [
        ("on_opened", watchdog.events.FileOpenedEvent(str(tmp_path / "entry.md")), False),
        ("on_closed_no_write", watchdog.events.FileClosedNoWriteEvent(str(tmp_path / "entry.md")), False),
        ("on_created", watchdog.events.FileCreatedEvent(str(tmp_path / "entry.md")), True),
        ("on_modified", watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md")), True),
        ("on_deleted", watchdog.events.FileDeletedEvent(str(tmp_path / "entry.md")), True),
        ("on_created", watchdog.events.FileCreatedEvent(str(tmp_path / "entry.lock")), False),
        ("on_modified", watchdog.events.FileModifiedEvent(str(tmp_path / "entry.lock")), False),
        ("on_deleted", watchdog.events.FileDeletedEvent(str(tmp_path / "entry.lock")), False),
        (
            "on_moved",
            watchdog.events.FileMovedEvent(
                str(tmp_path / "entry.tmp"),
                str(tmp_path / "entry.md"),
            ),
            True,
        ),
        (
            "on_moved",
            watchdog.events.FileMovedEvent(
                str(tmp_path / "entry.md"),
                str(tmp_path / "entry.tmp"),
            ),
            True,
        ),
        (
            "on_moved",
            watchdog.events.FileMovedEvent(
                str(tmp_path / "entry.lock"),
                str(tmp_path / "entry.lock.bak"),
            ),
            False,
        ),
    ]
    for handler_name, event, expected in events:
        current = state.ServeState(tmp_path, debounce_seconds=0)
        current._loop = asyncio.get_running_loop()
        published = threading.Event()
        monkeypatch.setattr(current, "publish", published.set)
        getattr(current, handler_name)(event)
        await asyncio.sleep(0)
        assert published.is_set() is expected


@pytest.mark.asyncio
async def test_state_publishes_once_after_last_change(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閾値内の連続変更をまとめ、最後の変更後に1回だけ通知する。"""
    current = state.ServeState(tmp_path, debounce_seconds=0.03)
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    event = watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md"))

    current.on_modified(event)
    await asyncio.sleep(0.01)
    current.on_modified(event)
    await asyncio.sleep(0.01)
    current.on_modified(event)

    await asyncio.sleep(0.02)
    assert not published
    await asyncio.sleep(0.03)
    assert published == ["changed"]


class _FakeTimer(threading.Timer):
    """`threading.Timer`の代替。実時間で発火せず、テストが明示的に発火させる。"""

    def __init__(self, interval: float, function: typing.Callable[..., None], args: tuple[typing.Any, ...] = ()) -> None:
        super().__init__(interval, function, args)
        self.cancelled = False

    def start(self) -> None:
        """発火予約の代わりに何もしない。"""

    def cancel(self) -> None:
        """発火予約を取り消したものとして記録する。"""
        self.cancelled = True


@pytest.mark.asyncio
async def test_state_publishes_at_max_wait_deadline_and_restarts_debounce(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閾値未満の間隔で変更が続いても最大待機時間で1回通知し、以降は新しい保留期間を開始する。

    実時間のスケジューリング遅延で結果が変わらないよう、単調時計とタイマーを注入して検証する。
    """
    debounce = 0.05
    max_wait = debounce * state.ServeState._MAX_DEBOUNCE_FACTOR
    clock = [1_000.0]
    timers: list[_FakeTimer] = []

    def _make_timer(interval: float, function: typing.Callable[..., None], args: tuple[typing.Any, ...] = ()) -> _FakeTimer:
        timer = _FakeTimer(interval, function, args)
        timers.append(timer)
        return timer

    current = state.ServeState(
        tmp_path,
        debounce_seconds=debounce,
        monotonic=lambda: clock[0],
        timer_factory=_make_timer,
    )
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    event = watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md"))

    # 閾値未満の間隔で変更を送り続け、期限に到達するまで発行されないことを確認する。
    deadline = clock[0] + max_wait
    while clock[0] < deadline:
        current.on_modified(event)
        # 直前に設定したタイマーの発火時刻が期限を超えないこと（期限が上界であること）。
        assert clock[0] + timers[-1].interval <= deadline
        await asyncio.sleep(0)
        assert not published
        clock[0] += 0.02

    # 期限に到達した変更はタイマーを介さず直ちに発行する。
    pending_before = len(timers)
    current.on_modified(event)
    await asyncio.sleep(0)
    assert published == ["changed"]
    assert len(timers) == pending_before

    # 以降は新しい保留期間となり、静穏期間の経過で再び1回発行する。
    clock[0] += 0.01
    current.on_modified(event)
    await asyncio.sleep(0)
    assert published == ["changed"]
    latest = timers[-1]
    assert latest.interval == debounce
    latest.function(*latest.args)
    await asyncio.sleep(0)
    assert published == ["changed", "changed"]


@pytest.mark.asyncio
async def test_state_ignores_pending_timer_cancelled_by_deadline(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取り消し済みタイマーが発火しても、再設定後の保留通知を重複発行しない。"""
    current = state.ServeState(tmp_path, debounce_seconds=10.0)
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    event = watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md"))

    current.on_modified(event)
    stale = current._pending_notification
    assert stale is not None
    current.on_modified(event)
    # 取り消しと同時に起動した旧タイマーが発火した状況を再現する。
    stale.function(*stale.args)
    await asyncio.sleep(0)

    assert not published


@pytest.mark.asyncio
async def test_state_discards_pending_notification_when_stopped(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """監視停止時は保留中の通知を破棄する。"""
    current = state.ServeState(tmp_path, debounce_seconds=0.02)
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    monkeypatch.setattr(current.observer, "stop", lambda: None)
    monkeypatch.setattr(current.observer, "join", lambda: None)

    current.on_modified(watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md")))
    current.stop()
    await asyncio.sleep(0.04)

    assert not published


def test_all_api_routes_are_registered(tmp_path: pathlib.Path) -> None:
    """計画で定義した全APIルートを登録する。"""
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state)
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    expected = {
        "/favicon.svg",
        "/manifest.webmanifest",
        "/static/icon-192.png",
        "/static/icon-512.png",
        "/api/sync",
        "/api/repos",
        "/api/entries",
        "/api/entries/<state_name>/<filename>",
        "/api/entries/start-processing",
        "/api/entries/adopt",
        "/api/entries/reject",
        "/api/entries/remove",
        "/api/entries/commit",
        "/api/entries/answer",
        "/api/events",
    }
    removed = {"/api/status", "/api/enable", "/api/disable"}
    assert expected <= rules
    assert not removed & rules


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


@pytest.mark.asyncio
async def test_concurrent_sync_requests_share_one_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同時同期要求は1回のpull結果を共有する。"""
    operations = serve_app.Operations(tmp_path)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    def pull(_path: pathlib.Path) -> None:
        nonlocal calls
        calls += 1
        started.set()
        release.wait()

    monkeypatch.setattr(common, "repo_lock", lock)
    monkeypatch.setattr(common, "pull", pull)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    tasks = [asyncio.create_task(app.test_client().post("/api/sync")) for _ in range(4)]
    await asyncio.to_thread(started.wait)
    await asyncio.sleep(0)
    release.set()
    responses = await asyncio.gather(*tasks)
    assert calls == 1
    assert [await response.get_json() for response in responses] == [{"synced": True}] * 4


@pytest.mark.asyncio
async def test_cancelled_sync_request_does_not_cancel_shared_sync(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """開始要求のキャンセル後も共有同期が継続し、別要求へ同じ結果を返す。"""
    operations = serve_app.Operations(tmp_path)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def sync() -> bool:
        nonlocal calls
        calls += 1
        started.set()
        release.wait()
        return True

    monkeypatch.setattr(operations, "sync", sync)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    first = asyncio.create_task(app.test_client().post("/api/sync"))
    await asyncio.to_thread(started.wait)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(app.test_client().post("/api/sync"))
    await asyncio.sleep(0)
    release.set()
    response = await second
    assert calls == 1
    assert await response.get_json() == {"synced": True}


@pytest.mark.asyncio
async def test_read_routes_remain_available_during_entry_move(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態移動と競合した一覧はスナップショット、詳細は404を返し409にしない。"""
    inbox = tmp_path / "inbox"
    adopted = tmp_path / "adopted"
    inbox.mkdir()
    adopted.mkdir()
    entry = inbox / "entry.md"
    entry.write_text("---\ntype: feedback\ntarget_repo: example/repo\n---\n\n本文\n", encoding="utf-8")
    original_entry_type_of = common.entry_type_of

    async def race_request(path: str) -> typing.Any:
        started = threading.Event()
        release = threading.Event()

        def entry_type_of(entry_path: pathlib.Path, text: str) -> str:
            started.set()
            release.wait()
            result = original_entry_type_of(entry_path, text)
            assert result is not None
            return result

        monkeypatch.setattr(common, "entry_type_of", entry_type_of)
        request = asyncio.create_task(app.test_client().get(path))
        await asyncio.to_thread(started.wait)
        entry.rename(adopted / entry.name)
        release.set()
        return await request

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    entries_response = await race_request("/api/entries?status=inbox")
    assert entries_response.status_code == 200
    assert await entries_response.get_json() == {"entries": []}

    entry = adopted / "entry.md"
    entry.rename(inbox / entry.name)
    entry = inbox / "entry.md"
    detail_response = await race_request("/api/entries/inbox/entry.md")
    assert detail_response.status_code == 404


def test_operations_reads_local_entries_and_detail_without_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一覧と詳細はGit同期を開始せずローカル内容を返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    entry = inbox / "entry.md"
    entry.write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\nsource: test\n---\n\n要約本文\n",
        encoding="utf-8",
    )

    def unexpected(*_args: object, **_kwargs: object) -> typing.NoReturn:
        raise AssertionError("読取り処理がGit同期を開始しました")

    monkeypatch.setattr(common, "repo_lock", unexpected)
    monkeypatch.setattr(common, "pull", unexpected)
    operations = serve_app.Operations(tmp_path)
    result = operations.entries({})
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
    detail = operations.detail("inbox", "entry.md")
    content = detail["content"]
    assert isinstance(content, str)
    assert content.endswith("要約本文\n")


def _write_detail_entry(tmp_path: pathlib.Path, text: str) -> None:
    """詳細表示テスト用の入力ファイルを作成する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "entry.md").write_text(text, encoding="utf-8")


def test_detail_renders_frontmatter_as_table(tmp_path: pathlib.Path) -> None:
    """frontmatterは入れ子の長い値も表として描画し、区切り行由来の見出しを生成しない。"""
    _write_detail_entry(
        tmp_path,
        "---\ntarget_repo: github.com/ak110/dotfiles\ntype: feedback\n"
        "plan_file: /tmp/plan.md\ndepends_on:\n  - predecessor.md\n---\n\n本文です。\n",
    )
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert "<table" in rendered
    assert "target_repo" in rendered
    assert "plan_file" in rendered
    assert "predecessor.md" in rendered
    assert "<hr" not in rendered
    assert "<h2" not in rendered
    assert "<p>本文です。</p>" in rendered


def test_detail_escapes_frontmatter_values(tmp_path: pathlib.Path) -> None:
    """frontmatterの値に含まれるHTML特殊文字をエスケープする。"""
    _write_detail_entry(tmp_path, '---\ntype: feedback\nnote: "<script>alert(1)</script>"\n---\n\n本文\n')
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_detail_falls_back_on_broken_frontmatter(tmp_path: pathlib.Path) -> None:
    """frontmatterの解析に失敗した場合は本文全体の整形結果を返す。"""
    _write_detail_entry(tmp_path, "---\nkey: [unclosed\n---\n\n本文\n")
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert "本文" in rendered
    # 表へ振り分けず本文全体をMarkdownとして整形するため、開始区切りが水平線として残る。
    assert '<table class="frontmatter">' not in rendered
    assert "<hr" in rendered


def test_detail_without_frontmatter_is_unchanged(tmp_path: pathlib.Path) -> None:
    """frontmatterを持たない本文は従来のMarkdownとして整形する。"""
    _write_detail_entry(tmp_path, "# 見出し\n\n本文\n")
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert "<h1>見出し</h1>" in rendered
    assert "<p>本文</p>" in rendered


def test_detail_with_empty_frontmatter_renders_body_only(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """空のfrontmatterは空表を生成せず、分離後の本文だけを整形する。"""
    _write_detail_entry(tmp_path, "---\n---\n\n本文\n")
    monkeypatch.setattr(common, "entry_type_of", lambda *_args: "feedback")
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert '<table class="frontmatter">' not in rendered
    # 分離自体は成立するため、開始区切りが水平線として残らない。
    assert "<hr" not in rendered
    assert "<p>本文</p>" in rendered


def test_operations_sort_entries_by_filename_across_states_and_render_markdown(tmp_path: pathlib.Path) -> None:
    """一覧を状態横断のファイル名順で返し、詳細本文を安全なHTMLへ整形する。"""
    inbox = tmp_path / "inbox"
    processing = tmp_path / "processing"
    inbox.mkdir()
    processing.mkdir()
    (inbox / "z-last.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (processing / "a-first.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n"
        "# 見出し\n\n- 項目\n\n```python\nprint('x')\n```\n\n<script>alert(1)</script>\n",
        encoding="utf-8",
    )

    operations = serve_app.Operations(tmp_path)
    assert [item["filename"] for item in operations.entries({"status": "active"})] == ["z-last.md", "a-first.md"]
    detail = operations.detail("processing", "a-first.md")
    rendered = typing.cast(str, detail["content_html"])
    assert "<h1>見出し</h1>" in rendered
    assert "<li>項目</li>" in rendered
    assert '<code class="language-python">' in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


@pytest.mark.parametrize(
    ("frontmatter_source", "expected_repo", "expected_source"),
    [
        (
            "type: feedback\ntarget_repo: example/repo\nsource: test\nplan_file: /tmp/plan.md\n"
            "depends_on:\n  - predecessor.md\n",
            "example/repo",
            "test",
        ),
        ("type: feedback\ntarget_repo: [broken\n", None, None),
    ],
)
def test_operations_frontmatter_parser_handles_nested_dependencies_and_broken_yaml(
    tmp_path: pathlib.Path,
    frontmatter_source: str,
    expected_repo: str | None,
    expected_source: str | None,
) -> None:
    """一覧表示は入れ子メタデータを読み、YAML破損時も一覧全体を継続する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(f"---\n{frontmatter_source}---\n\n要約本文\n", encoding="utf-8")

    result = serve_app.Operations(tmp_path).entries({})

    assert result[0]["target_repo"] == expected_repo
    assert result[0]["source"] == expected_source


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


@pytest.mark.parametrize("query", ["本文途中の固有語", "日本語", "EXAMPLE/REPO", "entry.md"])
def test_operations_query_searches_full_markdown_and_metadata(tmp_path: pathlib.Path, query: str) -> None:
    """`q`は本文途中、Unicode、frontmatter値、ファイル名を大文字小文字を区別せず検索する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n先頭\n\n日本語と本文途中の固有語\n",
        encoding="utf-8",
    )
    (inbox / "other.md").write_text(
        "---\ntype: feedback\ntarget_repo: other/repo\n---\n\n別本文\n",
        encoding="utf-8",
    )

    result = serve_app.Operations(tmp_path).entries({"q": query})

    assert [item["filename"] for item in result] == ["entry.md"]


def test_operations_source_empty_filter_returns_items_with_missing_or_empty_source(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`source_empty=true`は空の投入元だけを返し、値あり・非文字列の項目を除外する。"""
    monkeypatch.setattr(common, "repo_lock", lambda *_a, **_k: contextlib.nullcontext())
    monkeypatch.setattr(common, "pull", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "no-source.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n投入元なし\n",
        encoding="utf-8",
    )
    (inbox / "empty-source.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\nsource: \n---\n\n空投入元\n",
        encoding="utf-8",
    )
    (inbox / "whitespace-source.md").write_text(
        '---\ntype: feedback\ntarget_repo: example/repo\nsource: "   "\n---\n\n空白投入元\n',
        encoding="utf-8",
    )
    (inbox / "with-source.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\nsource: web\n---\n\n投入元あり\n",
        encoding="utf-8",
    )
    (inbox / "list-source.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\nsource: []\n---\n\nリスト形式の投入元\n",
        encoding="utf-8",
    )
    result = serve_app.Operations(tmp_path).entries({"source_empty": "true"})
    filenames = [typing.cast(str, item["filename"]) for item in result]
    filenames.sort()
    assert filenames == ["empty-source.md", "no-source.md", "whitespace-source.md"]


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


def test_operations_sort_entries_with_tbd_and_feedback_groups(tmp_path: pathlib.Path) -> None:
    """一覧はTBD・フィードバックの種別でグループ化し、各グループ内ではファイル名の降順で返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    # ファイル名の昇順でファイルを作成
    (inbox / "a-feedback.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (inbox / "b-tbd.md").write_text(
        "---\ntype: tbd\ntarget_repo: example/repo\n---\n\n質問\n",
        encoding="utf-8",
    )
    (inbox / "c-tbd.md").write_text(
        "---\ntype: tbd\ntarget_repo: example/repo\n---\n\n質問\n",
        encoding="utf-8",
    )
    (inbox / "d-feedback.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )

    operations = serve_app.Operations(tmp_path)
    result = operations.entries({})
    filenames = [item["filename"] for item in result]

    # TBDが前（降順）、フィードバックが後ろ（降順）
    assert filenames == ["c-tbd.md", "b-tbd.md", "d-feedback.md", "a-feedback.md"]
    # 種別の確認
    assert result[0]["kind"] == "tbd"
    assert result[1]["kind"] == "tbd"
    assert result[2]["kind"] == "feedback"
    assert result[3]["kind"] == "feedback"


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
    loop = asyncio.new_event_loop()
    try:
        current.start(loop)
        assert sorted(pathlib.Path(p).name for p in scheduled) == ["adopted", "inbox", "processing", "rejected"]
        assert not (tmp_path / "feedback").exists()
        assert not (tmp_path / "tbd").exists()
    finally:
        loop.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/entries?status=unknown", None),
        ("get", "/api/entries?target_repo=", None),
        ("get", "/api/entries?q=", None),
        ("get", "/api/entries?source_empty=false", None),
        ("get", "/api/entries?source=web&source_empty=true", None),
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
    """Web入力境界が列挙・型・空文字・basename違反・競合パラメーターを400で拒否する。"""
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
    """mutationの有限待機ロック競合をJSON 409応答へ変換する。"""

    def edit(
        _state: str,
        _filename: str,
        _content: str,
        _expected_content: str | None = None,
    ) -> bool:
        raise filelock.Timeout("locked")

    operations = serve_app.Operations(tmp_path)
    monkeypatch.setattr(operations, "edit", edit)
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
async def test_favicon_svg_is_served(tmp_path: pathlib.Path) -> None:
    """`/favicon.svg`がSVGとして配信される。"""
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    response = await app.test_client().get("/favicon.svg")
    body = await response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    assert response.headers["Cache-Control"] == "public, max-age=3600"
    assert body == assets.FAVICON_SVG


@pytest.mark.asyncio
async def test_manifest_declares_svg_icon(tmp_path: pathlib.Path) -> None:
    """manifestのiconsがSVG1件を宣言し、従来のPNGも引き続き配信する。"""
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    manifest_response = await client.get("/manifest.webmanifest")
    assert manifest_response.content_type == "application/manifest+json"
    assert manifest_response.headers["Cache-Control"] == "no-cache"
    manifest = await manifest_response.get_json()
    assert manifest == {
        "name": "フィードバック管理",
        "short_name": "atk serve",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": assets.THEME_COLOR,
        "background_color": assets.THEME_COLOR,
        "icons": [
            {
                "src": "/favicon.svg",
                "sizes": "192x192 512x512 any",
                "type": "image/svg+xml",
                "purpose": "any",
            }
        ],
    }

    for size in (192, 512):
        response = await client.get(f"/static/icon-{size}.png")
        body = await response.get_data()
        assert isinstance(body, bytes)
        assert response.content_type == "image/png"
        assert body.startswith(b"\x89PNG\r\n\x1a\n")
        assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"

        offset = 8
        chunks: list[tuple[bytes, bytes]] = []
        while offset < len(body):
            length = struct.unpack_from(">I", body, offset)[0]
            chunk_type = body[offset + 4 : offset + 8]
            chunk_data = body[offset + 8 : offset + 8 + length]
            stored_crc = struct.unpack_from(">I", body, offset + 8 + length)[0]
            assert binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF == stored_crc
            chunks.append((chunk_type, chunk_data))
            offset += 12 + length
        assert offset == len(body)
        assert [chunk_type for chunk_type, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]

        width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
            ">IIBBBBB",
            chunks[0][1],
        )
        assert (width, height) == (size, size)
        assert (bit_depth, color_type, compression, filtering, interlace) == (8, 6, 0, 0, 0)
        raw = zlib.decompress(b"".join(data for chunk_type, data in chunks if chunk_type == b"IDAT"))
        row_size = 1 + size * 4
        assert len(raw) == size * row_size
        expected_pixel = bytes.fromhex(assets.THEME_COLOR.removeprefix("#")) + b"\xff"
        for row_start in range(0, len(raw), row_size):
            assert raw[row_start] == 0
            assert raw[row_start + 1 : row_start + row_size] == expected_pixel * size


@pytest.mark.asyncio
async def test_index_and_js_reflect_forwarded_prefix(tmp_path: pathlib.Path) -> None:
    """`X-Forwarded-Prefix`をHTML、JS、manifestの各URLへ1回だけ反映する。"""
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    headers = {"X-Forwarded-Prefix": "/atk", "X-Forwarded-Proto": "https"}

    index_response = await client.get("/atk/", headers=headers)
    assert index_response.status_code == 200
    index_body = await index_response.get_data(as_text=True)
    assert 'href="/atk/static/app.css"' in index_body
    assert 'src="/atk/static/app.js"' in index_body
    assert 'href="/atk/favicon.svg"' in index_body
    assert 'href="/atk/manifest.webmanifest" crossorigin="use-credentials"' in index_body
    assert "/atk/atk/" not in index_body

    js_response = await client.get("/atk/static/app.js", headers=headers)
    assert js_response.status_code == 200
    js_body = await js_response.get_data(as_text=True)
    assert 'const BASE_PATH="/atk";' in js_body

    manifest_response = await client.get("/atk/manifest.webmanifest", headers=headers)
    manifest = await manifest_response.get_json()
    assert manifest["start_url"] == "/atk/"
    assert manifest["scope"] == "/atk/"
    assert [icon["src"] for icon in manifest["icons"]] == ["/atk/favicon.svg"]
    assert all("/atk/atk/" not in icon["src"] for icon in manifest["icons"])


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


def _recorder(calls: list[str], label: str, *, result: object) -> typing.Callable[[pathlib.Path], typing.Any]:
    """呼び出しを記録して固定値を返す差し替え関数を返す。"""

    def record(_path: pathlib.Path) -> typing.Any:
        calls.append(label)
        return result

    return record


def _write_repo_entry(root: pathlib.Path, state_name: str, filename: str, target_repo: str) -> None:
    """対象リポジトリを持つエントリを配置する。"""
    directory = root / state_name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(
        f"---\ntarget_repo: {target_repo}\ntype: feedback\n---\n\n本文\n",
        encoding="utf-8",
    )


def test_target_repos_collects_distinct_values_from_active_states(tmp_path: pathlib.Path) -> None:
    """未処理・処理中のエントリから対象リポジトリを重複なく昇順で集める。

    処理済みにしか現れないリポジトリは、一覧の初期フィルター`active`で0件になるため含めない。
    """
    _write_repo_entry(tmp_path, "inbox", "a.md", "github.com/x/beta")
    _write_repo_entry(tmp_path, "processing", "b.md", "github.com/x/alpha")
    _write_repo_entry(tmp_path, "processing", "c.md", "github.com/x/beta")
    _write_repo_entry(tmp_path, "adopted", "d.md", "github.com/x/adopted-only")
    _write_repo_entry(tmp_path, "rejected", "e.md", "github.com/x/rejected-only")
    (tmp_path / "inbox" / "broken.md").write_text("frontmatterなし\n", encoding="utf-8")
    operations = serve_app.Operations(tmp_path)
    assert operations.target_repos() == ["github.com/x/alpha", "github.com/x/beta"]


@pytest.mark.asyncio
async def test_api_repos_returns_target_repos(tmp_path: pathlib.Path) -> None:
    """`GET /api/repos`が対象リポジトリの一覧を返す。"""
    _write_repo_entry(tmp_path, "inbox", "a.md", "github.com/x/alpha")
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state)
    response = await app.test_client().get("/api/repos")
    assert response.status_code == 200
    assert await response.get_json() == {"repos": ["github.com/x/alpha"]}


def test_sync_ignores_rate_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """利用者の明示的な同期はレート制限を経由せず毎回pullする。"""
    calls: list[str] = []

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "repo_lock", lock)
    monkeypatch.setattr(common, "pull", _recorder(calls, "pull", result=None))
    monkeypatch.setattr(common, "pull_if_stale", _recorder(calls, "pull_if_stale", result=True))
    operations = serve_app.Operations(tmp_path)
    assert operations.sync() is True
    assert operations.sync() is True
    assert calls == ["pull", "pull"]


def test_background_sync_respects_rate_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """定期更新はレート制限に従い、ロック競合時は当該周期を見送る。"""
    calls: list[str] = []

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "repo_lock", lock)
    monkeypatch.setattr(common, "pull_if_stale", _recorder(calls, "pull_if_stale", result=False))
    operations = serve_app.Operations(tmp_path)
    assert operations.background_sync() is False
    assert calls == ["pull_if_stale"]

    def conflicting_lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        raise filelock.Timeout("lock")

    monkeypatch.setattr(common, "repo_lock", conflicting_lock)
    assert operations.background_sync() is False
    assert calls == ["pull_if_stale"]


@pytest.mark.asyncio
async def test_background_sync_task_starts_and_stops(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """定期更新タスクが起動時に開始し終了時に停止する。"""
    monkeypatch.setattr(serve_app, "_BACKGROUND_SYNC_INTERVAL_SECONDS", 0.001)
    calls: list[str] = []
    called = asyncio.Event()
    loop = asyncio.get_running_loop()

    class _Operations(serve_app.Operations):
        def background_sync(self) -> bool:
            calls.append("sync")
            loop.call_soon_threadsafe(called.set)
            return True

    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        current_state,
        operations=_Operations(tmp_path),
    )
    async with app.test_app():  # ty: ignore[invalid-context-manager]
        # 実時間の経過ではなく初回呼び出しの成立を待ち、起動を確認する。
        await asyncio.wait_for(called.wait(), timeout=5)

    # 停止の成立は、`after_serving`の完了後に呼び出し回数が増えないことで確認する。
    # 実行中の周期が残りうるため、基準を取る前に処理待ちのタスクを消化させる。
    await asyncio.sleep(0)
    before = len(calls)
    called.clear()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(called.wait(), timeout=0.1)
    assert len(calls) == before


def test_assets_populate_target_repo_choices() -> None:
    """対象リポジトリの候補をフィルターと新規登録欄へ反映する。"""
    assert '<select id="target-filter">' in assets.HTML
    assert '<datalist id="repo-options"></datalist>' in assets.HTML
    result = _run_node_ui(
        """
fetchHandler = async (url) => {
  if (url.endsWith('/api/repos')) {
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
    assert result["filterLabels"][0] == "すべて"
    assert result["datalistValues"] == ["github.com/x/alpha", "github.com/x/beta"]


def test_assets_keep_selected_target_repo_when_absent_from_choices() -> None:
    """候補から消えた選択値も選択肢へ補って選択状態を保つ。"""
    result = _run_node_ui(
        """
fetchHandler = async (url) => {
  if (url.endsWith('/api/repos')) {
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
    assert result["values"] == ["", "github.com/x/removed", "github.com/x/alpha"]
    assert result["selected"] == "github.com/x/removed"


def test_assets_keep_choices_when_repos_request_fails() -> None:
    """候補の取得に失敗しても既存の選択肢と選択値を壊さない。"""
    result = _run_node_ui(
        """
fetchHandler = async () => {
  throw new Error('通信失敗');
};
byId('target-filter').value = 'github.com/x/alpha';
await loadTargetRepos();
process.stdout.write(JSON.stringify({
  selected: byId('target-filter').value,
  values: byId('target-filter').children.map(option => option.value),
}));
"""
    )
    assert result["selected"] == "github.com/x/alpha"
    assert result["values"] == []


def test_assets_do_not_accumulate_target_repo_choices() -> None:
    """候補の再取得で選択肢が累積しない。"""
    result = _run_node_ui(
        """
fetchHandler = async (url) => {
  if (url.endsWith('/api/repos')) {
    const repos = ['github.com/x/alpha', 'github.com/x/beta'];
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos})};
  }
  throw new Error('想定外のURL: ' + url);
};
await loadTargetRepos();
await loadTargetRepos();
process.stdout.write(JSON.stringify({
  filterCount: byId('target-filter').children.length,
  datalistCount: byId('repo-options').children.length,
}));
"""
    )
    assert result["filterCount"] == 3
    assert result["datalistCount"] == 2
