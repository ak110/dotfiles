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


def test_web_transition_warns_and_records_unverified_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Web採否はcloneを探索せず、警告後に指定revisionを記録する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "feedback.md").write_text(
        "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n本文\n",
        encoding="utf-8",
    )
    mutations = serve_app.feedback_mutations
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)

    result = serve_app.Operations(tmp_path).transition("adopt", ["feedback.md"], commit="abcdef1")

    assert result == ["feedback.md"]
    assert "- 対応commit: abcdef1" in (tmp_path / "adopted/feedback.md").read_text(encoding="utf-8")
    assert "github.com/example/foo" in capsys.readouterr().err


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
    assert "entry.body_html ?? entry.content_html ?? ''" in assets.JS
    assert "eventSource.addEventListener('changed'" in assets.JS
    assert "entries-changed" not in assets.JS
    assert "insertAdjacentHTML" not in combined
    assert "/api/entries" in combined
    assert "/api/events" in combined
    assert "__BASE_PATH_HTML__" in assets.HTML
    assert "__BASE_PATH_JS__" in assets.JS
    assert f'<meta name="theme-color" content="{assets.THEME_COLOR}">' in assets.HTML
    assert 'rel="icon" type="image/svg+xml" href="__BASE_PATH_HTML__/favicon.svg"' in assets.HTML
    assert 'rel="manifest" href="__BASE_PATH_HTML__/manifest.webmanifest" crossorigin="use-credentials"' in assets.HTML


def test_assets_use_single_cli_ordered_list_and_current_terms() -> None:
    """単一一覧のCLI準拠列順、件数、識別子表示を固定する。"""
    assert assets.HTML.count('<ul id="entry-list"') == 1
    assert "other-entry-list" not in assets.HTML
    columns = re.search(r'<div class="entry-columns"[^>]*>(.*?)</div>', assets.HTML, re.DOTALL)
    assert columns is not None
    assert re.findall(r"<span>(.*?)</span>", columns.group(1)) == [
        "ファイル名",
        "対象リポジトリ",
        "種別・状態",
        "要約",
    ]
    assert "未回答TBD 0件" in assets.HTML
    assert "種別・状態・回答状況" not in assets.HTML
    assert ">確認事項<" not in assets.HTML
    assert assets.HTML.count(">tbd<") == 2
    assert ">今すぐ同期<" in assets.HTML
    assert 'placeholder="本文・ファイル名・対象・投入元を検索"' in assets.HTML
    assert "dataset.unansweredTbd" in assets.JS
    assert "種別不明" in assets.JS

    grid = re.search(r"\.entry-columns, \.entry-select \{(.*?)\n\}", assets.CSS, re.DOTALL)
    assert grid is not None
    widths = re.findall(r"minmax\(([^)]+)\)", grid.group(1))
    assert widths == [
        "12rem, 1.4fr",
        "10rem, 1.35fr",
        "8rem, 1fr",
        "12rem, 2fr",
    ]


def test_assets_use_shared_dialog_shell_without_cancel_ui() -> None:
    """3ダイアログへ共通シェルと唯一の終了操作を適用する。"""
    assert assets.HTML.count("<dialog ") == 3
    assert assets.HTML.count('class="dialog-shell') == 3
    assert 'class="dialog-shell detail-dialog"' in assets.HTML
    for dialog_id, heading_id, close_id in [
        ("detail-dialog", "detail-heading", "detail-close-button"),
        ("create-dialog", "create-dialog-heading", "create-close-button"),
        ("delete-dialog", "delete-dialog-heading", "delete-close-button"),
    ]:
        dialog = re.search(rf'<dialog id="{dialog_id}"([^>]*)>(.*?)</dialog>', assets.HTML, re.DOTALL)
        assert dialog is not None
        assert f'aria-labelledby="{heading_id}"' in dialog.group(1)
        assert 'class="dialog-header"' in dialog.group(2)
        assert 'class="dialog-body"' in dialog.group(2)
        assert 'class="dialog-footer' in dialog.group(2)
        assert f'id="{close_id}"' in dialog.group(2)
        assert 'aria-label="閉じる"' in dialog.group(2)
    assert "unsaved-dialog" not in assets.HTML
    assert "cancel-" not in assets.HTML
    assert "requestDiscard" not in assets.JS
    assert "pendingDiscardAction" not in assets.JS
    assert "中止</button>" not in assets.HTML
    assert "width: 2.75rem;" in assets.CSS
    assert "height: 2.75rem;" in assets.CSS
    assert "overflow-y: auto;" in assets.CSS
    assert "dialog.dialog-shell {" in assets.CSS
    dialog_rule = re.search(r"dialog\.dialog-shell \{(.*?)\n\}", assets.CSS, re.DOTALL)
    assert dialog_rule is not None
    assert "overflow: hidden;" in dialog_rule.group(1)


def test_assets_style_markdown_and_inputs_by_purpose() -> None:
    """本文、コード、用途別入力、モバイル操作の表示契約を固定する。"""
    assert ".markdown-body :not(pre) > code {" in assets.CSS
    pre_rule = re.search(r"\.markdown-body pre \{(.*?)\n\}", assets.CSS, re.DOTALL)
    assert pre_rule is not None
    assert "white-space: pre-wrap;" in pre_rule.group(1)
    assert "overflow-wrap: anywhere;" in pre_rule.group(1)
    assert ".markdown-body pre code { padding: 0; background: transparent; }" in assets.CSS
    for selector in ("#edit-content", "#answer-input", "#create-content", "#create-choices"):
        rule = re.search(rf"{re.escape(selector)} \{{([^}}]+)\}}", assets.CSS)
        assert rule is not None
        assert "clamp(" in rule.group(1)
    mobile = assets.CSS.partition("@media (max-width: 700px) {")[2]
    assert ".app-header { align-items: flex-start; flex-wrap: wrap; }" in mobile
    assert ".dialog-footer button { width: auto; }" in mobile
    assert "button,\n  input,\n  select,\n  textarea" not in mobile


def test_assets_define_all_operation_lifecycles_and_message_regions() -> None:
    """5更新操作を共通pending処理へ接続し、結果領域を操作場所ごとに持つ。"""
    assert "async function runPending(" in assets.JS
    for key in ("'sync'", "'save'", "'answer'", "'create'", "'delete'"):
        assert f"runPending({key}" in assets.JS
    assert "payload" in assets.JS.partition("async function runPending")[0] or "payload" in assets.JS
    assert ".filter(control => !control.classList.contains('dialog-close'))" in assets.JS
    assert "pendingOperations.has(key)" in assets.JS
    assert "finally {" in assets.JS
    for dialog_name in ("detail", "create", "delete"):
        assert f'id="{dialog_name}-alert"' in assets.HTML
        assert f'id="{dialog_name}-status"' in assets.HTML
    assert 'id="result-status"' in assets.HTML
    assert 'id="list-warning"' in assets.HTML
    assert "showError('')" not in assets.JS


def _run_node_ui(scenario: str) -> dict[str, typing.Any]:
    """UI関数を最小DOM上で実行し、シナリオのJSON結果を返す。"""
    source = assets.JS.replace("__BASE_PATH_JS__", '"/atk"').replace(
        "\nbindEvents();\ninitializeApp();\n",
        "\n",
    )
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
    this.innerHTML = '';
    this.className = '';
    this.type = '';
    this.isConnected = true;
    this.classList = {{
      add: (...names) => {{
        const values = new Set(this.className.split(/\\s+/).filter(Boolean));
        names.forEach(name => values.add(name));
        this.className = Array.from(values).join(' ');
      }},
      remove: (...names) => {{
        const values = this.className.split(/\\s+/).filter(name => name && !names.includes(name));
        this.className = values.join(' ');
      }},
      contains: name => this.className.split(/\\s+/).includes(name)
    }};
  }}
  setConnected(value) {{
    this.isConnected = value;
    this.children.forEach(child => {{ if (typeof child.setConnected === 'function') child.setConnected(value); }});
  }}
  append(...children) {{
    children.forEach(child => {{ if (typeof child.setConnected === 'function') child.setConnected(true); }});
    this.children.push(...children);
  }}
  replaceChildren(...children) {{
    this.children.forEach(child => {{ if (typeof child.setConnected === 'function') child.setConnected(false); }});
    children.forEach(child => {{ if (typeof child.setConnected === 'function') child.setConnected(true); }});
    this.children = children;
  }}
  setAttribute(name, value) {{ this.attributes[name] = String(value); }}
  getAttribute(name) {{ return this.attributes[name] ?? null; }}
  removeAttribute(name) {{ delete this.attributes[name]; }}
  addEventListener(name, handler) {{ this.listeners[name] = handler; }}
  querySelectorAll() {{ return globalThis.controlGroups[this.id] || []; }}
  showModal() {{ this.open = true; }}
  close() {{ this.open = false; }}
  focus() {{ document.activeElement = this; globalThis.focused = this.dataset.key || this.id; }}
}}
const ids = [
  'connection-status', 'sync-result', 'refresh-button', 'notification-button', 'create-button', 'global-error',
  'clear-filters-button', 'search-input', 'kind-filter', 'state-filter', 'answer-filter',
  'target-filter', 'source-filter', 'source-empty-filter', 'entry-count',
  'result-status', 'list-warning', 'loading-indicator', 'entry-list', 'empty-state',
  'empty-state-message', 'empty-clear-button', 'empty-all-states-button', 'empty-create-button',
  'detail-dialog', 'detail-shell', 'detail-dialog-body', 'detail-close-button', 'detail-alert',
  'detail-status', 'detail-view', 'detail-filename', 'detail-state', 'detail-metadata',
  'detail-content', 'readonly-notice', 'edit-button', 'answer-button', 'delete-button',
  'edit-panel', 'edit-content', 'edit-content-error', 'save-entry-button', 'answer-panel',
  'answer-choices', 'answer-input', 'answer-input-error', 'save-answer-button',
  'create-dialog', 'create-form', 'create-close-button', 'create-alert', 'create-status',
  'create-kind', 'create-content', 'create-content-error', 'create-target',
  'create-target-error', 'create-source', 'tbd-fields', 'create-scope',
  'create-question-type', 'choice-fields', 'create-choices', 'create-choices-error',
  'create-submit-button', 'delete-dialog', 'delete-form', 'delete-close-button',
  'delete-alert', 'delete-status', 'delete-target', 'delete-state', 'delete-target-repo',
  'delete-summary', 'force-delete-row', 'force-delete-confirmation', 'delete-error',
  'delete-submit-button', 'repo-options', 'toast'
];
const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
elements['kind-filter'].value = 'all';
elements['state-filter'].value = 'active';
elements['answer-filter'].value = 'all';
elements['create-kind'].value = 'feedback';
elements['create-question-type'].value = 'free-form';
globalThis.controlGroups = {{
  'detail-shell': [
    elements['detail-close-button'], elements['edit-button'], elements['answer-button'],
    elements['delete-button'], elements['edit-content'], elements['save-entry-button'],
    elements['answer-input'], elements['save-answer-button']
  ],
  'create-form': [
    elements['create-close-button'], elements['create-kind'], elements['create-content'],
    elements['create-target'], elements['create-source'], elements['create-scope'],
    elements['create-question-type'], elements['create-choices'], elements['create-submit-button']
  ],
  'delete-form': [
    elements['delete-close-button'], elements['force-delete-confirmation'], elements['delete-submit-button']
  ],
  'app-header': [elements['refresh-button'], elements['create-button']]
}};
elements['detail-close-button'].className = 'dialog-close';
elements['create-close-button'].className = 'dialog-close';
elements['delete-close-button'].className = 'dialog-close';
const appHeader = new Element('app-header');
globalThis.controlGroups['app-header'] = [elements['refresh-button'], elements['create-button']];
globalThis.document = {{
  activeElement: null,
  getElementById(id) {{ return elements[id] || null; }},
  createElement(tagName) {{ return new Element('', tagName.toUpperCase()); }},
  createTextNode(text) {{ const node = new Element('', '#TEXT'); node.textContent = text; return node; }},
  querySelector(selector) {{ return selector === '.app-header' ? appHeader : null; }},
  querySelectorAll(selector) {{
    if (selector !== '.entry-select') return [];
    return elements['entry-list'].children.flatMap(item => item.children);
  }}
}};
globalThis.controlGroups['app-header'] = [elements['refresh-button'], elements['create-button']];
globalThis.setTimeout = () => 1;
globalThis.clearTimeout = () => undefined;
globalThis.EventSource = class {{
  constructor(url) {{ this.url = url; this.listeners = {{}}; }}
  addEventListener(name, handler) {{ this.listeners[name] = handler; }}
}};
const fetchCalls = [];
let fetchHandler = async () => ({{ok: true, status: 200, statusText: 'OK', json: async () => ({{entries: [], warnings: []}})}});
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


def test_assets_execute_error_paths_across_binding_and_initialization_code() -> None:
    """末尾を含むJS全体で外部更新と初期化の失敗メッセージを表示する。"""
    result = _run_node_ui(
        """
fetchHandler = async url => {
  if (url.includes('/api/repos?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
refreshKnownTbds = async () => { throw new Error('外部更新失敗'); };
await reloadFromExternalChange();
await Promise.resolve();
const reloadError = elements['global-error'].textContent;
elements['global-error'].textContent = '';
refreshKnownTbds = async () => { throw new Error('初期化失敗'); };
initializeApp();
await initialization;
process.stdout.write(JSON.stringify({reloadError, initializationError: elements['global-error'].textContent}));
"""
    )
    assert result == {"reloadError": "外部更新失敗", "initializationError": "初期化失敗"}


def test_assets_render_single_list_warnings_and_filter_dependencies() -> None:
    """一覧警告、種別不明、件数通知、成立しないフィルター組合せの解除を検証する。"""
    result = _run_node_ui(
        """
entries = [
  {kind: 'tbd', state: 'inbox', filename: 'u.md', answered: false, summary: '未回答', target_repo: 'x/u'},
  {kind: 'unknown', state: 'inbox', filename: 'x.md', answered: null, summary: '不明', target_repo: 'x/u'},
  {kind: 'feedback', state: 'inbox', filename: 'f.md', answered: null, summary: '本文', target_repo: 'x/f',
   updated_at: '2026-08-07T10:11:00+00:00'}
];
renderList([{filename: 'bad.md', reason: 'UTF-8として読み取れません'}], true);
const announced = elements['result-status'].textContent;
const warning = elements['list-warning'].textContent;
const feedbackCells = elements['entry-list'].children[2].children[0].children;
const kindState = feedbackCells[2].children.map(child => child.textContent);
const summary = feedbackCells[3].textContent;
elements['kind-filter'].value = 'feedback';
elements['answer-filter'].value = 'no';
elements['source-filter'].value = 'web';
elements['source-empty-filter'].checked = true;
syncFilterDependencies();
elements['result-status'].textContent = '変更しない';
renderList([], false);
process.stdout.write(JSON.stringify({
  keys: elements['entry-list'].children.map(item => item.children[0].dataset.key),
  unanswered: elements['entry-list'].children[0].children[0].dataset.unansweredTbd,
  unknownKind: elements['entry-list'].children[1].children[0].dataset.kind,
  count: elements['entry-count'].textContent,
  warning,
  announced,
  kindState,
  summary,
  sseStatus: elements['result-status'].textContent,
  answerValue: elements['answer-filter'].value,
  answerDisabled: elements['answer-filter'].disabled,
  sourceValue: elements['source-filter'].value,
  sourceDisabled: elements['source-filter'].disabled
}));
"""
    )
    assert result == {
        "keys": ["inbox/u.md", "inbox/x.md", "inbox/f.md"],
        "unanswered": "true",
        "unknownKind": "unknown",
        "count": "3件（未回答TBD 1件）",
        "warning": "一覧から除外したファイル: bad.md（UTF-8として読み取れません）",
        "announced": "3件を表示",
        "kindState": ["feedback", "inbox"],
        "summary": "本文",
        "sseStatus": "変更しない",
        "answerValue": "all",
        "answerDisabled": True,
        "sourceValue": "",
        "sourceDisabled": True,
    }


def test_assets_render_three_contextual_empty_states() -> None:
    """条件適用中、対応中0件、全件0件を別の回復操作へ案内する。"""
    result = _run_node_ui(
        """
entries = [];
renderEmptyState();
const active = {
  message: elements['empty-state-message'].textContent,
  allStates: !elements['empty-all-states-button'].hidden
};
elements['search-input'].value = 'none';
renderEmptyState();
const filtered = {
  message: elements['empty-state-message'].textContent,
  clear: !elements['empty-clear-button'].hidden
};
elements['search-input'].value = '';
elements['state-filter'].value = 'all';
renderEmptyState();
const all = {
  message: elements['empty-state-message'].textContent,
  create: !elements['empty-create-button'].hidden
};
process.stdout.write(JSON.stringify({active, filtered, all}));
"""
    )
    assert result == {
        "active": {"message": "対応中の項目はありません。", "allStates": True},
        "filtered": {"message": "条件に一致する項目はありません。", "clear": True},
        "all": {"message": "項目はまだありません。", "create": True},
    }


def test_assets_prefill_answer_change_and_keep_question_visible() -> None:
    """回答変更時は既存回答と質問本文を残し、候補を入力補助にする。"""
    result = _run_node_ui(
        """
const origin = new Element('origin', 'BUTTON');
const entry = {
  kind: 'tbd', state: 'inbox', filename: 'question.md', answered: true, answer: '既存回答',
  summary: '質問', target_repo: 'example/repo', content: 'raw',
  body_html: '<h2>質問</h2><p>本文</p>', question_type: 'choice', choices: ['A', 'B']
};
displayEntry(entry);
const answerLabel = elements['answer-button'].textContent;
openDialog(elements['detail-dialog'], origin, elements['detail-dialog-body']);
enterAnswer();
const prefilled = elements['answer-input'].value;
const candidates = elements['answer-choices'].children.map(button => button.textContent);
elements['answer-choices'].children[0].listeners.click();
const afterCandidate = elements['answer-input'].value;
elements['answer-input'].value += 'を補足';
const answerVisible = !elements['answer-panel'].hidden;
closeDetailDialog();
process.stdout.write(JSON.stringify({
  detailVisible: !elements['detail-view'].hidden,
  answerVisible,
  answerLabel,
  prefilled,
  candidates,
  afterCandidate,
  edited: elements['answer-input'].value,
  focused
}));
"""
    )
    assert result == {
        "detailVisible": True,
        "answerVisible": True,
        "answerLabel": "回答を変更",
        "prefilled": "既存回答",
        "candidates": ["A", "B"],
        "afterCandidate": "A",
        "edited": "Aを補足",
        "focused": "origin",
    }


def test_assets_keep_terminal_entry_read_only_and_show_identifiers() -> None:
    """終端状態では操作を隠し、kind/state識別子をそのまま表示する。"""
    result = _run_node_ui(
        """
displayEntry({
  kind: 'tbd', state: 'adopted', filename: 'done.md', answered: true, answer: '回答',
  summary: '完了', target_repo: 'example/repo', content: 'raw', body_html: '<p>本文</p>',
  question_type: 'free-form', choices: []
});
process.stdout.write(JSON.stringify({
  heading: elements['detail-state'].textContent,
  metadata: elements['detail-metadata'].children.map(child => child.textContent),
  readonly: !elements['readonly-notice'].hidden,
  editHidden: elements['edit-button'].hidden,
  answerHidden: elements['answer-button'].hidden,
  deleteHidden: elements['delete-button'].hidden
}));
"""
    )
    assert result == {
        "heading": "tbd / adopted",
        "metadata": [
            "種別",
            "tbd",
            "状態",
            "adopted",
            "回答状況",
            "回答済み",
            "対象リポジトリ",
            "example/repo",
            "更新日時",
            "—",
        ],
        "readonly": True,
        "editHidden": True,
        "answerHidden": True,
        "deleteHidden": True,
    }


def test_assets_notify_only_new_active_unanswered_tbd_after_permission() -> None:
    """初期集合と既知項目の属性変化を通知せず、新規未回答TBDだけを通知する。"""
    result = _run_node_ui(
        """
const notifications = [];
globalThis.Notification = class {
  static permission = 'default';
  static async requestPermission() { this.permission = 'granted'; return this.permission; }
  constructor(title, options) { notifications.push({title, body: options.body}); }
};
const snapshots = [
  [
    {kind: 'tbd', state: 'inbox', filename: 'base.md', answered: false, target_repo: 'old/repo'},
    {kind: 'tbd', state: 'inbox', filename: 'answered.md', answered: true},
    {kind: 'tbd', state: 'inbox', filename: 'moving.md', answered: false},
    {kind: 'tbd', state: 'adopted', filename: 'reappear.md', answered: true}
  ],
  [
    {kind: 'tbd', state: 'inbox', filename: 'base.md', answered: false, target_repo: 'new/repo'},
    {kind: 'tbd', state: 'inbox', filename: 'answered.md', answered: false},
    {kind: 'tbd', state: 'processing', filename: 'moving.md', answered: false},
    {kind: 'tbd', state: 'inbox', filename: 'new.md', answered: false}
  ],
  [
    {kind: 'tbd', state: 'inbox', filename: 'base.md', answered: false},
    {kind: 'tbd', state: 'inbox', filename: 'answered.md', answered: true},
    {kind: 'tbd', state: 'inbox', filename: 'moving.md', answered: false},
    {kind: 'tbd', state: 'inbox', filename: 'new.md', answered: true},
    {kind: 'tbd', state: 'inbox', filename: 'reappear.md', answered: false}
  ]
];
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK', json: async () => ({entries: snapshots.shift()})
});
syncNotificationButton();
const buttonVisibleBefore = !elements['notification-button'].hidden;
await enableNotifications();
const buttonHiddenAfter = elements['notification-button'].hidden;
await refreshKnownTbds({notify: false});
const afterBaseline = notifications.length;
await refreshKnownTbds({notify: true});
await refreshKnownTbds({notify: true});
process.stdout.write(JSON.stringify({
  buttonVisibleBefore, buttonHiddenAfter, afterBaseline, notifications,
  known: Array.from(knownTbdFilenames).sort()
}));
"""
    )
    assert result == {
        "buttonVisibleBefore": True,
        "buttonHiddenAfter": True,
        "afterBaseline": 0,
        "notifications": [{"title": "新規未回答TBD", "body": "new.md"}],
        "known": ["answered.md", "base.md", "moving.md", "new.md", "reappear.md"],
    }


def test_assets_restore_focus_on_escape_and_focus_delete_close_first() -> None:
    """Escapeで起点へ戻り、破壊確認では非破壊の閉じる操作へ初期フォーカスを置く。"""
    result = _run_node_ui(
        """
const origin = new Element('create-origin', 'BUTTON');
attachDialogCloseHandlers('create-dialog', 'create-close-button', () => closeDialog(elements['create-dialog']));
openDialog(elements['create-dialog'], origin, elements['create-content']);
elements['create-dialog'].listeners.cancel({preventDefault() {}});
const restored = focused;
displayEntry({
  kind: 'feedback', state: 'processing', filename: 'entry.md', answered: null,
  summary: '要約', target_repo: 'example/repo', content: 'raw', body_html: '<p>本文</p>',
  question_type: 'free-form', choices: []
});
elements['detail-dialog'].open = true;
dialogStack.push('detail-dialog');
openDeleteDialog();
process.stdout.write(JSON.stringify({
  restored,
  deleteInitial: focused,
  closeSizeClass: elements['delete-close-button'].className,
  detailStillOpen: elements['detail-dialog'].open
}));
"""
    )
    assert result == {
        "restored": "create-origin",
        "deleteInitial": "delete-close-button",
        "closeSizeClass": "dialog-close",
        "detailStillOpen": True,
    }


def test_assets_restore_detail_focus_after_origin_row_disappears() -> None:
    """詳細の起点行が消えた場合も、残存行又は空状態の操作へフォーカスを戻す。"""
    result = _run_node_ui(
        """
const first = {
  kind: 'feedback', state: 'inbox', filename: 'first.md', answered: null,
  summary: '先頭', content: '本文', body_html: '<p>本文</p>'
};
const second = {
  kind: 'feedback', state: 'inbox', filename: 'second.md', answered: null,
  summary: '次行', content: '本文', body_html: '<p>本文</p>'
};
entries = [first, second];
renderList();
const firstButton = elements['entry-list'].children[0].children[0];
displayEntry(first);
detailOriginKey = entryKey(first);
openDialog(elements['detail-dialog'], firstButton, elements['detail-dialog-body']);
entries = [second];
renderList();
closeDetailDialog();
const remainingRow = focused;

entries = [second];
renderList();
const secondButton = elements['entry-list'].children[0].children[0];
displayEntry(second);
detailOriginKey = entryKey(second);
openDialog(elements['detail-dialog'], secondButton, elements['detail-dialog-body']);
elements['search-input'].value = '一致しない条件';
entries = [];
renderList();
closeDetailDialog();
process.stdout.write(JSON.stringify({remainingRow, emptyState: focused}));
"""
    )
    assert result == {
        "remainingRow": "inbox/second.md",
        "emptyState": "empty-clear-button",
    }


def test_assets_reload_open_detail_from_sse_and_preserve_editing_input() -> None:
    """SSE更新時に閲覧本文を再取得し、編集中は入力を保持して消失時に閉じる。"""
    result = _run_node_ui(
        """
const listed = {
  kind: 'feedback', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '一覧要約', target_repo: 'example/repo'
};
let detailContent = '外部更新後の本文';
let deleted = false;
fetchHandler = async url => {
  if (url.includes('/api/repos?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})};
  }
  if (url.includes('/api/entries?')) {
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({entries: deleted ? [] : [listed], warnings: []})
    };
  }
  if (url.includes('/api/entries/')) {
    if (deleted) {
      return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
    }
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({entry: {...listed, content: detailContent, body_html: `<p>${detailContent}</p>`}})
    };
  }
  throw new Error('想定外のURL: ' + url);
};
const original = {...listed, content: '更新前の本文', body_html: '<p>更新前の本文</p>'};
entries = [original];
renderList();
const origin = elements['entry-list'].children[0].children[0];
displayEntry(original);
detailOriginKey = entryKey(original);
openDialog(elements['detail-dialog'], origin, elements['detail-dialog-body']);
await reloadFromExternalChange();
const viewed = elements['detail-content'].innerHTML;
enterEdit();
elements['edit-content'].value = '利用者の未保存本文';
detailContent = '編集中の外部更新';
await reloadFromExternalChange();
const editing = {
  input: elements['edit-content'].value,
  baseline: currentEntry.content,
  alert: elements['detail-alert'].textContent,
  saveDisabled: elements['save-entry-button'].disabled,
  open: elements['detail-dialog'].open
};
setDetailMode('answer');
elements['answer-input'].value = '利用者の未保存回答';
detailContent = '回答中の外部更新';
await reloadFromExternalChange();
const answering = {
  input: elements['answer-input'].value,
  alert: elements['detail-alert'].textContent,
  saveDisabled: elements['save-answer-button'].disabled,
  open: elements['detail-dialog'].open
};
deleted = true;
await reloadFromExternalChange();
process.stdout.write(JSON.stringify({
  viewed,
  detailRequests: fetchCalls.filter(call => call.url.includes('/api/entries/')).length,
  editing,
  answering,
  afterDelete: {open: elements['detail-dialog'].open, focused}
}));
"""
    )
    assert result["viewed"] == "<p>外部更新後の本文</p>"
    assert result["detailRequests"] >= 3
    assert result["editing"] == {
        "input": "利用者の未保存本文",
        "baseline": "外部更新後の本文",
        "alert": "外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。",
        "saveDisabled": True,
        "open": True,
    }
    assert result["answering"] == {
        "input": "利用者の未保存回答",
        "alert": "外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。",
        "saveDisabled": True,
        "open": True,
    }
    assert result["afterDelete"] == {"open": False, "focused": "empty-all-states-button"}


def test_assets_sse_detail_tracks_state_and_discards_stale_response() -> None:
    """SSE詳細は状態移動を追跡し、別項目選択後の古い応答を破棄する。"""
    result = _run_node_ui(
        """
const inbox = {
  kind: 'feedback', state: 'inbox', filename: 'entry.md', answered: null,
  content: '移動前本文', body_html: '<p>移動前本文</p>'
};
const processing = {
  ...inbox, state: 'processing', content: '移動後本文', body_html: '<p>移動後本文</p>'
};
const second = {
  kind: 'feedback', state: 'inbox', filename: 'second.md', answered: null,
  content: '別項目本文', body_html: '<p>別項目本文</p>'
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
            "key": "processing/entry.md",
        },
        "final": {
            "filename": "second.md",
            "content": "<p>別項目本文</p>",
            "key": "inbox/second.md",
        },
    }


def test_assets_sse_detail_prefers_exact_identity_and_only_tracks_unique_move() -> None:
    """SSE詳細は複合キーを優先し、404後の一意な移動だけを追跡する。"""
    result = _run_node_ui(
        """
const processing = {
  kind: 'feedback', state: 'processing', filename: 'same.md', answered: null,
  summary: '処理中', content: '処理中本文', body_html: '<p>処理中本文</p>'
};
const inbox = {
  ...processing, state: 'inbox', summary: '未処理', content: '未処理本文', body_html: '<p>未処理本文</p>'
};
const adopted = {
  ...processing, state: 'adopted', summary: '採用済み', content: '採用済み本文', body_html: '<p>採用済み本文</p>'
};
displayEntry(processing);
detailOriginKey = entryKey(processing);
elements['detail-dialog'].open = true;
entries = [inbox, processing];
const exactRequests = [];
fetchHandler = async url => {
  exactRequests.push(url);
  const entry = url.includes('/processing/') ? processing : inbox;
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry})};
};
await reloadOpenDetailFromExternalChange();
const exact = {
  state: currentEntry.state,
  content: elements['detail-content'].innerHTML,
  requests: exactRequests
};

fetchHandler = async url => {
  if (url.includes('/processing/')) {
    return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
  }
  if (url.includes('/inbox/')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: inbox})};
  }
  return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
};
await reloadOpenDetailFromExternalChange();
const uniqueMove = {state: currentEntry.state, key: detailOriginKey, open: elements['detail-dialog'].open};

displayEntry(processing);
detailOriginKey = entryKey(processing);
elements['detail-dialog'].open = true;
fetchHandler = async url => {
  if (url.includes('/processing/')) {
    return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
  }
  const entry = url.includes('/inbox/') ? inbox : adopted;
  if (url.includes('/inbox/') || url.includes('/adopted/')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry})};
  }
  return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
};
await reloadOpenDetailFromExternalChange();
process.stdout.write(JSON.stringify({
  exact,
  uniqueMove,
  ambiguous: {
    detailOpen: elements['detail-dialog'].open,
    currentEntryIsNull: currentEntry === null,
    error: elements['global-error'].textContent
  }
}));
"""
    )
    assert result == {
        "exact": {
            "state": "processing",
            "content": "<p>処理中本文</p>",
            "requests": ["/atk/api/entries/processing/same.md"],
        },
        "uniqueMove": {
            "state": "inbox",
            "key": "inbox/same.md",
            "open": True,
        },
        "ambiguous": {
            "detailOpen": False,
            "currentEntryIsNull": True,
            "error": "same.mdの移動先を一意に特定できません。詳細を開き直してください。",
        },
    }


def test_assets_sse_keeps_edit_target_for_duplicate_filename() -> None:
    """同名項目のSSE後も編集中の複合キーを保存先として維持する。"""
    result = _run_node_ui(
        """
const processing = {
  kind: 'feedback', state: 'processing', filename: 'same.md', answered: null,
  summary: '処理中', content: '処理中本文', body_html: '<p>処理中本文</p>'
};
const inbox = {
  ...processing, state: 'inbox', summary: '未処理', content: '未処理本文', body_html: '<p>未処理本文</p>'
};
displayEntry(processing);
detailOriginKey = entryKey(processing);
elements['detail-dialog'].open = true;
entries = [inbox, processing];
enterEdit();
elements['edit-content'].value = '利用者の保存本文';
const putUrls = [];
fetchHandler = async (url, options) => {
  if (options.method === 'PUT') {
    putUrls.push(url);
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({ok: true})};
  }
  if (url.includes('/api/entries?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [inbox, processing], warnings: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: processing})};
};
await reloadOpenDetailFromExternalChange();
await saveEntry();
process.stdout.write(JSON.stringify({putUrls, state: currentEntry.state}));
"""
    )
    assert result == {
        "putUrls": ["/atk/api/entries/processing/same.md"],
        "state": "processing",
    }


def test_assets_sse_reconciles_owned_delete_dialog() -> None:
    """SSE移動時は削除確認を閉じ、消失時は親子を閉じて一覧へ戻す。"""
    result = _run_node_ui(
        """
const inbox = {
  kind: 'feedback', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '移動前', target_repo: 'example/repo', content: '本文', body_html: '<p>本文</p>'
};
const processing = {...inbox, state: 'processing', summary: '移動後'};
const remaining = {...inbox, filename: 'remaining.md', summary: '残存'};
entries = [inbox, remaining];
renderList();
const origin = elements['entry-list'].children[0].children[0];
displayEntry(inbox);
detailOriginKey = entryKey(inbox);
openDialog(elements['detail-dialog'], origin, elements['detail-dialog-body']);
openDeleteDialog();
let phase = 'moved';
fetchHandler = async url => {
  if (url.includes('/inbox/entry.md')) {
    return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
  }
  if (phase === 'moved' && url.includes('/processing/entry.md')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: processing})};
  }
  return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
};
await reloadOpenDetailFromExternalChange();
const moved = {
  detailOpen: elements['detail-dialog'].open,
  deleteOpen: elements['delete-dialog'].open,
  state: currentEntry.state,
  focused
};
openDeleteDialog();
const reopened = {
  state: elements['delete-state'].textContent,
  forceVisible: !elements['force-delete-row'].hidden
};
entries = [remaining];
renderList();
phase = 'missing';
await reloadOpenDetailFromExternalChange();
process.stdout.write(JSON.stringify({
  moved,
  reopened,
  missing: {
    detailOpen: elements['detail-dialog'].open,
    deleteOpen: elements['delete-dialog'].open,
    focused
  }
}));
"""
    )
    assert result == {
        "moved": {
            "detailOpen": True,
            "deleteOpen": False,
            "state": "processing",
            "focused": "detail-dialog-body",
        },
        "reopened": {"state": "feedback / processing", "forceVisible": True},
        "missing": {
            "detailOpen": False,
            "deleteOpen": False,
            "focused": "inbox/remaining.md",
        },
    }


def test_assets_clear_self_write_sse_alert_after_save_and_answer_success() -> None:
    """保存・回答の応答前にSSEが届いても、成功後は競合警告を残さない。"""
    result = _run_node_ui(
        """
async function runSave() {
  let serverEntry = {
    kind: 'feedback', state: 'inbox', filename: 'entry.md', answered: null,
    summary: '保存対象', content: '更新前', body_html: '<p>更新前</p>'
  };
  displayEntry(serverEntry);
  detailOriginKey = entryKey(serverEntry);
  openDialog(elements['detail-dialog'], new Element('save-origin', 'BUTTON'), elements['detail-dialog-body']);
  enterEdit();
  elements['edit-content'].value = '保存後';
  let resolveMutation;
  let markStarted;
  const started = new Promise(resolve => { markStarted = resolve; });
  fetchHandler = async (url, options) => {
    if (options.method === 'PUT') return new Promise(resolve => {
      resolveMutation = resolve;
      markStarted();
    });
    if (url.includes('/api/entries?')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [serverEntry], warnings: []})};
    }
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: serverEntry})};
  };
  const saving = saveEntry();
  await started;
  serverEntry = {...serverEntry, content: '保存後', body_html: '<p>保存後</p>'};
  await reloadOpenDetailFromExternalChange();
  const during = elements['detail-alert'].textContent;
  resolveMutation({ok: true, status: 200, statusText: 'OK', json: async () => ({ok: true})});
  await saving;
  return {
    during,
    after: elements['detail-alert'].textContent,
    status: elements['detail-status'].textContent,
    mode: currentDetailMode()
  };
}

async function runAnswer() {
  let serverEntry = {
    kind: 'tbd', state: 'inbox', filename: 'question.md', answered: false,
    summary: '回答対象', content: '質問', body_html: '<p>質問</p>',
    question_type: 'free-form', choices: []
  };
  displayEntry(serverEntry);
  detailOriginKey = entryKey(serverEntry);
  openDialog(elements['detail-dialog'], new Element('answer-origin', 'BUTTON'), elements['detail-dialog-body']);
  enterAnswer();
  elements['answer-input'].value = '回答';
  let resolveMutation;
  let markStarted;
  const started = new Promise(resolve => { markStarted = resolve; });
  fetchHandler = async (url, options) => {
    if (url.endsWith('/api/entries/answer') && options.method === 'POST') return new Promise(resolve => {
      resolveMutation = resolve;
      markStarted();
    });
    if (url.includes('/api/entries?')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [serverEntry], warnings: []})};
    }
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: serverEntry})};
  };
  const answering = saveAnswer();
  await started;
  serverEntry = {...serverEntry, answered: true, content: '質問と回答', body_html: '<p>質問と回答</p>'};
  await reloadOpenDetailFromExternalChange();
  const during = elements['detail-alert'].textContent;
  resolveMutation({ok: true, status: 200, statusText: 'OK', json: async () => ({ok: true})});
  await answering;
  return {
    during,
    after: elements['detail-alert'].textContent,
    status: elements['detail-status'].textContent,
    mode: currentDetailMode()
  };
}

const saved = await runSave();
const answered = await runAnswer();
process.stdout.write(JSON.stringify({saved, answered}));
"""
    )
    warning = "外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。"
    assert result == {
        "saved": {
            "during": warning,
            "after": "",
            "status": "inbox/entry.mdを保存しました。",
            "mode": "view",
        },
        "answered": {
            "during": warning,
            "after": "",
            "status": "inbox/question.mdへ回答しました。",
            "mode": "view",
        },
    }


def test_assets_preserve_external_update_recovery_across_operation_failures() -> None:
    """外部更新後の一般失敗と競合は復旧手順を保ち、単独の権限拒否は再試行できる。"""
    result = _run_node_ui(
        """
async function exercise(kind, status, code, withExternalUpdate) {
  const answering = kind === 'answer';
  let serverEntry = {
    kind: answering ? 'tbd' : 'feedback', state: 'inbox',
    filename: answering ? 'question.md' : 'entry.md', answered: answering ? false : null,
    summary: '操作対象', content: '更新前', body_html: '<p>更新前</p>',
    question_type: 'free-form', choices: []
  };
  displayEntry(serverEntry);
  detailOriginKey = entryKey(serverEntry);
  openDialog(elements['detail-dialog'], new Element(`${kind}-origin`, 'BUTTON'), elements['detail-dialog-body']);
  if (answering) {
    enterAnswer();
    elements['answer-input'].value = '利用者の回答';
  } else {
    enterEdit();
    elements['edit-content'].value = '利用者の保存本文';
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
        assert result[name]["mode"] == ("answer" if name.startswith("answer") else "edit")
    assert "保存できませんでした。 一般失敗" in result["saveGeneral"]["alert"]
    assert "回答できませんでした。 一般失敗" in result["answerGeneral"]["alert"]
    assert result["saveGeneral"]["input"] == "利用者の保存本文"
    assert result["answerGeneral"]["input"] == "利用者の回答"
    assert result["savePermission"] == {
        "alert": "inbox/entry.mdを保存できませんでした。 権限がありません",
        "disabled": False,
        "input": "利用者の保存本文",
        "mode": "edit",
    }
    assert result["answerPermission"] == {
        "alert": "inbox/question.mdへ回答できませんでした。 権限がありません",
        "disabled": False,
        "input": "利用者の回答",
        "mode": "answer",
    }


def test_assets_delete_conflict_requires_detail_reload() -> None:
    """削除競合後は古い詳細から再試行させず、開き直しを要求する。"""
    result = _run_node_ui(
        """
const entry = {
  kind: 'feedback', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '削除対象', target_repo: 'example/repo', content: '取得時本文', body_html: '<p>取得時本文</p>'
};
displayEntry(entry);
openDialog(elements['detail-dialog'], new Element('origin', 'BUTTON'), elements['detail-dialog-body']);
openDeleteDialog();
fetchHandler = async () => ({
  ok: false, status: 409, statusText: 'Conflict',
  json: async () => ({error: '編集中に他プロセスが対象を変更しました', code: 'edit_conflict'})
});
await deleteEntry({preventDefault() {}});
process.stdout.write(JSON.stringify({
  deleteOpen: elements['delete-dialog'].open,
  detailOpen: elements['detail-dialog'].open,
  deleteDisabled: elements['delete-button'].disabled,
  editDisabled: elements['edit-button'].disabled,
  alert: elements['detail-alert'].textContent
}));
"""
    )
    assert result["deleteOpen"] is False
    assert result["detailOpen"] is True
    assert result["deleteDisabled"] is True
    assert result["editDisabled"] is True
    assert "詳細を閉じて開き直してから削除してください" in result["alert"]


def test_assets_close_delete_confirmation_before_detail_refresh_failure() -> None:
    """一覧反映後に詳細取得が失敗しても、古い削除確認を閉じて親へ復旧手順を示す。"""
    result = _run_node_ui(
        """
async function exercise(updatedEntries, warnings) {
  const original = {
    kind: 'feedback', state: 'inbox', filename: 'entry.md', answered: null,
    summary: '変更前の要約', target_repo: 'example/old', content: '変更前本文', body_html: '<p>変更前本文</p>'
  };
  entries = [original];
  renderList();
  displayEntry(original);
  detailOriginKey = entryKey(original);
  openDialog(elements['detail-dialog'], elements['entry-list'].children[0].children[0], elements['detail-dialog-body']);
  openDeleteDialog();
  fetchHandler = async url => {
    if (url.includes('/api/repos')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
    }
    if (url.includes('/api/entries?')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: updatedEntries, warnings})};
    }
    return {ok: false, status: 500, statusText: 'Error', json: async () => ({error: '詳細取得に失敗'})};
  };
  await reloadFromExternalChange();
  const outcome = {
    deleteOpen: elements['delete-dialog'].open,
    detailOpen: elements['detail-dialog'].open,
    alert: elements['detail-alert'].textContent,
    topmost: topmostDialog()?.id || '',
    focused
  };
  closeDetailDialog();
  return outcome;
}

const updated = {
  kind: 'feedback', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '変更後の要約', target_repo: 'example/new', content: '変更後本文', body_html: '<p>変更後本文</p>'
};
const changed = await exercise([updated], []);
const unreadable = await exercise([], [{filename: 'entry.md', reason: 'UTF-8として読み取れません'}]);
process.stdout.write(JSON.stringify({changed, unreadable}));
"""
    )
    for outcome in result.values():
        assert outcome["deleteOpen"] is False
        assert outcome["detailOpen"] is True
        assert "詳細取得に失敗" in outcome["alert"]
        assert "削除確認を閉じました" in outcome["alert"]
        assert "削除操作をやり直してください" in outcome["alert"]
        assert outcome["topmost"] == "detail-dialog"
        assert outcome["focused"] == "detail-dialog-body"


def test_pending_helper_locks_inputs_rejects_reentry_and_keeps_close_enabled() -> None:
    """要求中は対象操作だけを固定し、二重実行を拒み、finallyで状態を復元する。"""
    result = _run_node_ui(
        """
let release;
let calls = 0;
const waiting = new Promise(resolve => { release = resolve; });
const first = runPending('save', {
  container: elements['detail-shell'],
  button: elements['save-entry-button'],
  busyLabel: '保存中'
}, async () => { calls += 1; return waiting; });
await Promise.resolve();
const during = {
  inputDisabled: elements['edit-content'].disabled,
  submitDisabled: elements['save-entry-button'].disabled,
  closeDisabled: elements['detail-close-button'].disabled,
  label: elements['save-entry-button'].textContent,
  busy: elements['detail-shell'].attributes['aria-busy']
};
const second = await runPending('save', {
  container: elements['detail-shell'],
  button: elements['save-entry-button'],
  busyLabel: '保存中'
}, async () => { calls += 1; });
release('done');
await first;
let failureCaught = false;
try {
  await runPending('create', {
    container: elements['create-form'],
    button: elements['create-submit-button'],
    busyLabel: '追加中'
  }, async () => { throw new Error('失敗'); });
} catch (error) {
  failureCaught = error.message === '失敗';
}
process.stdout.write(JSON.stringify({
  during,
  secondIsUndefined: second === undefined,
  calls,
  failureCaught,
  failureRestored: !elements['create-content'].disabled &&
    elements['create-form'].attributes['aria-busy'] === 'false',
  after: {
    inputDisabled: elements['edit-content'].disabled,
    submitDisabled: elements['save-entry-button'].disabled,
    label: elements['save-entry-button'].textContent,
    busy: elements['detail-shell'].attributes['aria-busy']
  }
}));
"""
    )
    assert result == {
        "during": {
            "inputDisabled": True,
            "submitDisabled": True,
            "closeDisabled": False,
            "label": "保存中",
            "busy": "true",
        },
        "secondIsUndefined": True,
        "calls": 1,
        "failureCaught": True,
        "failureRestored": True,
        "after": {
            "inputDisabled": False,
            "submitDisabled": False,
            "label": "",
            "busy": "false",
        },
    }


def test_operation_result_uses_current_topmost_dialog_or_global_toast() -> None:
    """開始元を閉じた後は現在最上位のdialogへ、dialogなしでは共通通知へ結果を送る。"""
    result = _run_node_ui(
        """
elements['detail-dialog'].open = true;
dialogStack.push('detail-dialog');
elements['delete-dialog'].open = true;
dialogStack.push('delete-dialog');
closeDialog(elements['delete-dialog']);
deliverOperationMessage('削除完了');
const detailMessage = elements['detail-status'].textContent;
closeDialog(elements['detail-dialog']);
deliverOperationMessage('保存完了');
process.stdout.write(JSON.stringify({
  detailMessage,
  toast: elements['toast'].textContent,
  globalError: elements['global-error'].textContent
}));
"""
    )
    assert result == {"detailMessage": "削除完了", "toast": "保存完了", "globalError": ""}


def test_failed_dialog_updates_restore_actionable_focus() -> None:
    """更新失敗後は開いたダイアログ内の再操作可能な要素へフォーカスを戻す。"""
    result = _run_node_ui(
        """
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '失敗'})
});
const feedback = {
  kind: 'feedback', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '要約', target_repo: 'example/repo', content: 'raw', body_html: '<p>本文</p>',
  question_type: 'free-form', choices: []
};
elements['detail-dialog'].open = true;
dialogStack.push('detail-dialog');
displayEntry(feedback);
enterEdit();
elements['edit-content'].value = '更新後';
await saveEntry();
const editFailure = focused;

displayEntry({...feedback, kind: 'tbd', filename: 'question.md', answered: false});
enterAnswer();
elements['answer-input'].value = '回答';
await saveAnswer();
const answerFailure = focused;

elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-content'].value = '新規本文';
elements['create-target'].value = 'example/repo';
await createEntry({preventDefault() {}});
const createFailure = focused;

displayEntry(feedback);
openDeleteDialog();
await deleteEntry({preventDefault() {}});
const deleteFailure = focused;

process.stdout.write(JSON.stringify({editFailure, answerFailure, createFailure, deleteFailure}));
"""
    )
    assert result == {
        "editFailure": "edit-content",
        "answerFailure": "answer-input",
        "createFailure": "create-content",
        "deleteFailure": "delete-close-button",
    }


def test_field_errors_mark_and_focus_first_invalid_control() -> None:
    """入力エラーを関連付け、最初の不正入力へフォーカスする。"""
    result = _run_node_ui(
        """
setFieldError(elements['create-content'], elements['create-content-error'], '本文が必要です');
setFieldError(elements['create-target'], elements['create-target-error'], '対象が必要です');
const first = firstInvalid([elements['create-content'], elements['create-target']]);
process.stdout.write(JSON.stringify({
  first: first.id,
  focused,
  contentInvalid: elements['create-content'].attributes['aria-invalid'],
  targetInvalid: elements['create-target'].attributes['aria-invalid'],
  contentError: elements['create-content-error'].textContent
}));
"""
    )
    assert result == {
        "first": "create-content",
        "focused": "create-content",
        "contentInvalid": "true",
        "targetInvalid": "true",
        "contentError": "本文が必要です",
    }


def test_create_success_resets_filters_and_opens_created_detail() -> None:
    """追加成功後は既定条件へ戻し、返却されたファイルの詳細を開く。"""
    result = _run_node_ui(
        """
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-content'].value = '新しい本文';
elements['create-target'].value = 'example/repo';
elements['kind-filter'].value = 'feedback';
elements['state-filter'].value = 'all';
elements['search-input'].value = '隠す条件';
const listed = {
  kind: 'feedback', state: 'inbox', filename: 'new.md', answered: null,
  summary: '新しい本文', target_repo: 'example/repo'
};
const detailed = {
  ...listed, content: 'raw', body_html: '<p>新しい本文</p>',
  question_type: 'free-form', choices: []
};
fetchHandler = async (url, options) => {
  if (url.endsWith('/api/entries') && options.method === 'POST') {
    return {ok: true, status: 201, statusText: 'Created', json: async () => ({filenames: ['new.md']})};
  }
  if (url.endsWith('/api/repos?status=active')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})};
  }
  if (url.includes('/api/entries?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [listed], warnings: []})};
  }
  if (url.endsWith('/api/entries/inbox/new.md')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: detailed})};
  }
  throw new Error('想定外のURL: ' + url);
};
await createEntry({preventDefault() {}});
process.stdout.write(JSON.stringify({
  kind: elements['kind-filter'].value,
  state: elements['state-filter'].value,
  search: elements['search-input'].value,
  detailOpen: elements['detail-dialog'].open,
  current: currentEntry.filename,
  body: elements['detail-content'].innerHTML,
  createCalls: fetchCalls.filter(call => call.url.endsWith('/api/entries') && call.options.method === 'POST').length
}));
"""
    )
    assert result == {
        "kind": "all",
        "state": "active",
        "search": "",
        "detailOpen": True,
        "current": "new.md",
        "body": "<p>新しい本文</p>",
        "createCalls": 1,
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
    assert await entries_response.get_json() == {"entries": [], "warnings": []}

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
        "summary": "要約本文",
        "updated_at": result[0]["updated_at"],
    }
    detail = operations.detail("inbox", "entry.md")
    content = detail["content"]
    assert isinstance(content, str)
    assert content.endswith("要約本文\n")


def test_detail_returns_existing_tbd_answer(tmp_path: pathlib.Path) -> None:
    """回答済みTBDの詳細は既存回答を編集用に返す。"""
    _write_detail_entry(
        tmp_path,
        "---\ntype: tbd\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問本文\n\n"
        "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n既存回答\n2行目\n",
    )

    detail = serve_app.Operations(tmp_path).detail("inbox", "entry.md")

    assert detail["answer"] == "既存回答\n2行目"


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


def test_detail_disables_only_bare_address_links(tmp_path: pathlib.Path) -> None:
    """詳細表示は裸アドレスをリンク化せず、明示リンクとGFM拡張を維持する。"""
    _write_detail_entry(
        tmp_path,
        "https://example.com www.example.com user@example.com\n\n"
        "[明示リンク](https://example.net) <https://example.org>\n\n"
        "~~取消~~\n\n| 列 |\n| --- |\n| 値 |\n",
    )

    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])

    assert '<a href="https://example.com">' not in rendered
    assert '<a href="http://www.example.com">' not in rendered
    assert '<a href="mailto:user@example.com">' not in rendered
    assert '<a href="https://example.net">明示リンク</a>' in rendered
    assert '<a href="https://example.org">https://example.org</a>' in rendered
    assert "<s>取消</s>" in rendered
    assert "<table>" in rendered


def test_detail_with_empty_frontmatter_renders_body_only(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """空のfrontmatterは空表を生成せず、分離後の本文だけを整形する。"""
    _write_detail_entry(tmp_path, "---\n---\n\n本文\n")
    monkeypatch.setattr(common, "entry_type_of", lambda *_args: "feedback")
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert '<table class="frontmatter">' not in rendered
    # 分離自体は成立するため、開始区切りが水平線として残らない。
    assert "<hr" not in rendered
    assert "<p>本文</p>" in rendered


def test_detail_with_frontmatter_only_returns_empty_body_html(tmp_path: pathlib.Path) -> None:
    """frontmatterだけの詳細は本文用HTMLを空文字列として返す。"""
    _write_detail_entry(tmp_path, "---\ntype: feedback\ntarget_repo: example/repo\n---\n")

    detail = serve_app.Operations(tmp_path).detail("inbox", "entry.md")

    assert detail["body_html"] == ""
    assert '<table class="frontmatter">' in typing.cast(str, detail["content_html"])


@pytest.mark.parametrize(
    ("question_source", "expected_type", "expected_choices"),
    [
        ("question_type: choice\nchoices: 最初, 2番目\n", "choice", ["最初", "2番目"]),
        ("question_type: choice\nchoices:\n  - 最初\n  - 2番目\n", "choice", ["最初", "2番目"]),
        ("question_type: yes-no\nchoices: 無視する\n", "yes-no", []),
        ("question_type: broken\nchoices: [最初, '']\n", "free-form", []),
        ("question_type:\n  - choice\nchoices: [最初, 2番目]\n", "free-form", []),
        ("question_type:\n  nested: choice\nchoices: [最初, 2番目]\n", "free-form", []),
    ],
)
def test_detail_returns_body_only_and_normalized_question_metadata(
    tmp_path: pathlib.Path,
    question_source: str,
    expected_type: str,
    expected_choices: list[str],
) -> None:
    """詳細API用データはfrontmatterを除いた本文と正規化済みの回答形式を返す。"""
    _write_detail_entry(
        tmp_path,
        f"---\ntype: tbd\ntarget_repo: example/repo\n{question_source}---\n\n## 質問\n\n質問本文\n",
    )

    detail = serve_app.Operations(tmp_path).detail("inbox", "entry.md")

    assert detail["question_type"] == expected_type
    assert detail["choices"] == expected_choices
    body_html = typing.cast(str, detail["body_html"])
    assert '<table class="frontmatter">' not in body_html
    assert "target_repo" not in body_html
    assert "質問本文" in body_html


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
async def test_answer_and_remove_apis_target_state_and_keep_legacy_resolution(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態指定時は表示対象へ作用し、省略時はprocessing優先を維持する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, serve_app.feedback_mutations, serve_app.tbd_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
    inbox = tmp_path / "inbox"
    processing = tmp_path / "processing"
    inbox.mkdir()
    processing.mkdir()
    tbd_content = (
        "---\ntype: tbd\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    feedback_content = "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n本文\n"
    for filename in ("answer-state.md", "answer-legacy.md"):
        (inbox / filename).write_text(tbd_content, encoding="utf-8")
        (processing / filename).write_text(tbd_content, encoding="utf-8")
    for filename in ("remove-state.md", "remove-legacy.md", "remove-protected.md"):
        (inbox / filename).write_text(feedback_content, encoding="utf-8")
        (processing / filename).write_text(feedback_content, encoding="utf-8")

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    state_answer = await client.post(
        "/api/entries/answer",
        json={
            "filename": "answer-state.md",
            "state": "inbox",
            "answer": "未処理側への回答",
            "expected_content": tbd_content,
        },
    )
    assert state_answer.status_code == 200
    assert (inbox / "answer-state.md").read_text(encoding="utf-8").endswith("未処理側への回答\n")
    assert (processing / "answer-state.md").read_text(encoding="utf-8") == tbd_content

    legacy_answer = await client.post(
        "/api/entries/answer",
        json={"filename": "answer-legacy.md", "answer": "従来経路の回答"},
    )
    assert legacy_answer.status_code == 200
    assert (inbox / "answer-legacy.md").read_text(encoding="utf-8") == tbd_content
    assert (processing / "answer-legacy.md").read_text(encoding="utf-8").endswith("従来経路の回答\n")

    state_remove = await client.post(
        "/api/entries/remove",
        json={
            "filenames": ["remove-state.md"],
            "state": "inbox",
            "expected_content": feedback_content,
            "force": False,
        },
    )
    assert state_remove.status_code == 200
    assert not (inbox / "remove-state.md").exists()
    assert (processing / "remove-state.md").is_file()

    protected = await client.post(
        "/api/entries/remove",
        json={"filenames": ["remove-protected.md"], "force": False},
    )
    assert protected.status_code == 400
    assert await protected.get_json() == {"error": "指定したエントリを操作できません"}

    legacy_remove = await client.post(
        "/api/entries/remove",
        json={"filenames": ["remove-legacy.md"], "force": True},
    )
    assert legacy_remove.status_code == 200
    assert (inbox / "remove-legacy.md").is_file()
    assert not (processing / "remove-legacy.md").exists()


@pytest.mark.asyncio
async def test_remove_api_rejects_changed_and_unreadable_expected_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内容変更と読取り不能を409で保護し、空の確認時本文も受理する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, serve_app.feedback_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = "---\ntype: feedback\n---\n\n確認時本文\n"
    changed = inbox / "changed.md"
    changed.write_text(original.replace("確認時", "外部更新後"), encoding="utf-8")
    unreadable = inbox / "unreadable.md"
    unreadable.write_bytes(b"\xff")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    for filename in (changed.name, unreadable.name):
        response = await client.post(
            "/api/entries/remove",
            json={
                "filenames": [filename],
                "state": "inbox",
                "expected_content": original,
                "force": False,
            },
        )
        assert response.status_code == 409
        assert await response.get_json() == {
            "code": "edit_conflict",
            "error": "編集中に他プロセスが対象を変更しました",
        }
        assert (inbox / filename).exists()

    empty = inbox / "empty.md"
    empty.write_text("", encoding="utf-8")
    empty_response = await client.post(
        "/api/entries/remove",
        json={
            "filenames": [empty.name],
            "state": "inbox",
            "expected_content": "",
            "force": False,
        },
    )
    assert empty_response.status_code == 200
    assert not empty.exists()


@pytest.mark.asyncio
async def test_remove_api_returns_edit_conflict_before_target_repo_validation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """対象リポジトリ照合より先に非UTF-8化を削除競合へ正規化する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, serve_app.feedback_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    target = inbox / "unreadable.md"
    target.write_bytes(b"\xff")
    original = "---\ntype: feedback\ntarget_repo: github.com/example/repo\n---\n\n取得時本文\n"
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post(
        "/api/entries/remove",
        json={
            "filenames": [target.name],
            "state": "inbox",
            "target_repo": "github.com/example/repo",
            "expected_content": original,
            "force": False,
        },
    )

    assert response.status_code == 409
    assert await response.get_json() == {
        "code": "edit_conflict",
        "error": "編集中に他プロセスが対象を変更しました",
    }
    assert response.content_type == "application/json"
    assert target.read_bytes() == b"\xff"


@pytest.mark.asyncio
async def test_answer_api_returns_edit_conflict_for_unreadable_expected_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回答対象の非UTF-8化を409競合へ正規化し、元のバイト列を保つ。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, serve_app.tbd_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    target = inbox / "question.md"
    target.write_bytes(b"\xff")
    original = (
        "---\ntype: tbd\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post(
        "/api/entries/answer",
        json={
            "filename": target.name,
            "state": "inbox",
            "answer": "回答",
            "expected_content": original,
        },
    )

    assert response.status_code == 409
    assert await response.get_json() == {
        "code": "edit_conflict",
        "error": "編集中に他プロセスが対象を変更しました",
    }
    assert target.read_bytes() == b"\xff"


@pytest.mark.asyncio
async def test_answer_and_remove_apis_return_edit_conflict_when_pull_moves_target(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pullで取得時の状態から移動した回答・削除対象を409競合として保全する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, serve_app.feedback_mutations, serve_app.tbd_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
    inbox = tmp_path / "inbox"
    processing = tmp_path / "processing"
    inbox.mkdir()
    processing.mkdir()
    tbd_content = (
        "---\ntype: tbd\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    feedback_content = "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n本文\n"
    answer_inbox = inbox / "answer.md"
    remove_inbox = inbox / "remove.md"
    answer_inbox.write_text(tbd_content, encoding="utf-8")
    remove_inbox.write_text(feedback_content, encoding="utf-8")

    def move_during_pull(_path: pathlib.Path) -> None:
        if answer_inbox.exists():
            answer_inbox.rename(processing / answer_inbox.name)
        elif remove_inbox.exists():
            remove_inbox.rename(processing / remove_inbox.name)

    monkeypatch.setattr(serve_app.feedback_mutations, "_pull", move_during_pull)
    monkeypatch.setattr(serve_app.tbd_mutations, "_pull", move_during_pull)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    answer_response = await client.post(
        "/api/entries/answer",
        json={
            "filename": answer_inbox.name,
            "state": "inbox",
            "answer": "回答",
            "expected_content": tbd_content,
        },
    )
    remove_response = await client.post(
        "/api/entries/remove",
        json={
            "filenames": [remove_inbox.name],
            "state": "inbox",
            "expected_content": feedback_content,
            "force": False,
        },
    )

    for response in (answer_response, remove_response):
        assert response.status_code == 409
        assert await response.get_json() == {
            "code": "edit_conflict",
            "error": "編集中に他プロセスが対象を変更しました",
        }
    assert (processing / answer_inbox.name).read_text(encoding="utf-8") == tbd_content
    assert (processing / remove_inbox.name).read_text(encoding="utf-8") == feedback_content


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


def test_operations_sort_entries_with_tbd_and_feedback_groups(tmp_path: pathlib.Path) -> None:
    """一覧は未回答TBD・その他TBD・フィードバックの順で各群をファイル名降順に返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    # ファイル名の昇順でファイルを作成
    (inbox / "a-feedback.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (inbox / "z-answered-tbd.md").write_text(
        "---\ntype: tbd\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問\n\n## 回答\n\n回答済み\n",
        encoding="utf-8",
    )
    (inbox / "a-unanswered-tbd.md").write_text(
        "---\ntype: tbd\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    (inbox / "d-feedback.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )

    operations = serve_app.Operations(tmp_path)
    result = operations.entries({})
    filenames = [item["filename"] for item in result]

    # 未回答TBD、回答済みTBD、フィードバックの順になる。
    assert filenames == ["a-unanswered-tbd.md", "z-answered-tbd.md", "d-feedback.md", "a-feedback.md"]
    # 種別の確認
    assert result[0]["kind"] == "tbd"
    assert result[1]["kind"] == "tbd"
    assert result[2]["kind"] == "feedback"
    assert result[3]["kind"] == "feedback"


@pytest.mark.asyncio
async def test_entries_api_keeps_readable_entries_and_reports_unreadable_files(tmp_path: pathlib.Path) -> None:
    """一覧APIは読取り不能な1ファイルを警告へ分離し、残りの一覧を返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "readable.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (inbox / "invalid.md").write_bytes(b"\xff")
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state)

    response = await app.test_client().get("/api/entries?status=inbox")

    assert response.status_code == 200
    payload = await response.get_json()
    assert [item["filename"] for item in payload["entries"]] == ["readable.md"]
    assert payload["warnings"] == [{"filename": "invalid.md", "reason": "UTF-8として読み取れません"}]


def test_entries_reports_os_error_without_treating_unknown_kind_as_warning(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OS読取り失敗だけを警告へ分離し、種別不明のMarkdownは一覧へ残す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "unknown.md").write_text("種別を判定できない本文\n", encoding="utf-8")
    failed_path = inbox / "os-error.md"
    failed_path.touch()
    original_read_text = pathlib.Path.read_text

    def read_text(
        path: pathlib.Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == failed_path:
            raise OSError("読取り失敗")
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)

    entries, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"status": "inbox"})

    assert [(entry["filename"], entry["kind"]) for entry in entries] == [("unknown.md", "unknown")]
    assert warnings == [{"filename": "os-error.md", "reason": "ファイルを読み取れません"}]


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
    assert operations.target_repos("adopted") == ["github.com/x/adopted-only"]


@pytest.mark.asyncio
async def test_api_repos_returns_target_repos(tmp_path: pathlib.Path) -> None:
    """`GET /api/repos`は一覧と同じ状態指定で対象リポジトリの一覧を返す。"""
    _write_repo_entry(tmp_path, "inbox", "a.md", "github.com/x/alpha")
    _write_repo_entry(tmp_path, "adopted", "b.md", "github.com/x/adopted")
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state)
    response = await app.test_client().get("/api/repos")
    assert response.status_code == 200
    assert await response.get_json() == {"repos": ["github.com/x/alpha"]}
    adopted_response = await app.test_client().get("/api/repos?status=adopted")
    assert adopted_response.status_code == 200
    assert await adopted_response.get_json() == {"repos": ["github.com/x/adopted"]}
    invalid_response = await app.test_client().get("/api/repos?status=unknown")
    assert invalid_response.status_code == 400


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
  if (url.endsWith('/api/repos?status=active')) {
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


def test_assets_clear_selected_target_repo_when_absent_from_choices() -> None:
    """候補から消えた対象リポジトリの選択状態を解除する。"""
    result = _run_node_ui(
        """
fetchHandler = async (url) => {
  if (url.endsWith('/api/repos?status=active')) {
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
    assert result["values"] == ["", "github.com/x/alpha"]
    assert result["selected"] == ""


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
        entries: [{kind: 'feedback', state: 'adopted', filename: 'entry.md', summary: '本文'}],
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
  error: elements['global-error'].textContent
}));
"""
    )
    assert result == {
        "listUrls": ["/atk/api/entries?type=all&status=adopted&answered=all"],
        "rows": ["entry.md"],
        "error": "候補取得失敗",
    }


def test_assets_do_not_accumulate_target_repo_choices() -> None:
    """候補の再取得で選択肢が累積しない。"""
    result = _run_node_ui(
        """
fetchHandler = async (url) => {
  if (url.endsWith('/api/repos?status=active')) {
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


def test_assets_ignore_out_of_order_repo_candidates_for_state_changes() -> None:
    """状態変更の旧候補応答を破棄し、旧処理から一覧要求を開始しない。"""
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
        entries: [{kind: 'feedback', state: 'inbox', filename: 'active.md', summary: 'active'}],
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
        "listUrls": ["/atk/api/entries?type=all&status=active&answered=all"],
        "rows": ["active.md"],
    }


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
    entries: [{kind: 'feedback', state: 'inbox', filename: 'new.md', summary: 'new'}],
    warnings: []
  })
});
await second;
resolvers[0]({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({
    entries: [{kind: 'feedback', state: 'inbox', filename: 'old.md', summary: 'old'}],
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


def test_assets_preserve_user_announcement_across_user_and_sse_request_orders() -> None:
    """利用者要求とSSE要求の開始・完了順にかかわらず、件数を一度通知する。"""
    result = _run_node_ui(
        """
async function runCase(startOrder, completionOrder) {
  const resolvers = {};
  let currentKind = '';
  fetchHandler = async url => new Promise(resolve => {
    resolvers[currentKind] = {resolve, url};
  });
  elements['result-status'].textContent = '変更前の通知';
  const requests = {};
  for (const kind of startOrder) {
    currentKind = kind;
    requests[kind] = loadEntries({announce: kind === 'user'});
    await Promise.resolve();
  }
  for (const kind of completionOrder) {
    resolvers[kind].resolve({
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({
        entries: [{kind: 'feedback', state: 'inbox', filename: `${kind}.md`, summary: kind}],
        warnings: []
      })
    });
    await requests[kind];
  }
  return {
    status: elements['result-status'].textContent,
    rows: entries.map(entry => entry.filename),
    urls: Object.fromEntries(Object.entries(resolvers).map(([kind, item]) => [kind, item.url]))
  };
}

const userThenSseUserFirst = await runCase(['user', 'sse'], ['user', 'sse']);
const userThenSseSseFirst = await runCase(['user', 'sse'], ['sse', 'user']);
const sseThenUserSseFirst = await runCase(['sse', 'user'], ['sse', 'user']);
const sseThenUserUserFirst = await runCase(['sse', 'user'], ['user', 'sse']);

elements['result-status'].textContent = 'SSE前の通知';
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entries: [], warnings: []})
});
await loadEntries({announce: false});
process.stdout.write(JSON.stringify({
  cases: [userThenSseUserFirst, userThenSseSseFirst, sseThenUserSseFirst, sseThenUserUserFirst],
  sseOnlyStatus: elements['result-status'].textContent
}));
"""
    )
    assert [case["status"] for case in result["cases"]] == ["1件を表示"] * 4
    assert result["cases"][0]["rows"] == ["sse.md"]
    assert result["cases"][1]["rows"] == ["sse.md"]
    assert result["cases"][2]["rows"] == ["user.md"]
    assert result["cases"][3]["rows"] == ["user.md"]
    assert result["sseOnlyStatus"] == "SSE前の通知"


def test_assets_keep_user_filter_load_when_same_state_sse_supersedes_repo_request() -> None:
    """同一状態の後発SSEが候補要求を失効させても利用者の一覧通知を維持する。"""
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
        entries: [{kind: 'feedback', state: 'inbox', filename: 'filtered.md', summary: 'filtered'}],
        warnings: []
      })
    };
  }
  throw new Error('想定外のURL: ' + url);
};
elements['kind-filter'].value = 'feedback';
elements['result-status'].textContent = '変更前の通知';
const user = handleFilterChange({reloadRepos: true});
await Promise.resolve();
const sse = reloadFromExternalChange();
await Promise.resolve();
repoResolvers[1]({
  ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})
});
await Promise.resolve();
repoResolvers[0]({
  ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})
});
await Promise.all([user, sse]);
process.stdout.write(JSON.stringify({
  listUrls,
  status: elements['result-status'].textContent,
  rows: entries.map(entry => entry.filename)
}));
"""
    )
    assert result == {
        "listUrls": [
            "/atk/api/entries?type=tbd&status=all&answered=all",
            "/atk/api/entries?type=feedback&status=active&answered=all",
            "/atk/api/entries?type=feedback&status=active&answered=all",
        ],
        "status": "1件を表示",
        "rows": ["filtered.md"],
    }


def test_assets_keep_latest_repo_selection_when_stale_sse_candidates_finish() -> None:
    """SSEの旧候補応答後も最新の対象リポジトリと一覧条件を維持する。"""
    result = _run_node_ui(
        """
let resolveActive;
const listUrls = [];
fetchHandler = async url => {
  if (url.endsWith('/api/repos?status=active')) {
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
    const matching = {kind: 'feedback', state: 'adopted', filename: 'selected.md', summary: 'selected'};
    const other = {kind: 'feedback', state: 'adopted', filename: 'other.md', summary: 'other'};
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
        "lastListUrl": ("/atk/api/entries?type=all&status=adopted&answered=all&target_repo=adopted%2Frepo"),
        "rows": ["selected.md"],
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
  error: elements['global-error'].textContent
}));
"""
    )
    assert result == {
        "candidates": ["", "active/repo"],
        "error": "",
    }
