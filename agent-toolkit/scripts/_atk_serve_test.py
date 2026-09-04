"""`atk serve`のテスト。"""

# pylint: disable=protected-access

import asyncio
import binascii
import contextlib
import json
import logging
import math
import os
import pathlib
import re
import signal
import struct
import subprocess
import threading
import types
import typing
import zlib

import _atk_serve as serve
import _atk_serve_app as serve_app
import _atk_serve_assets as assets
import _atk_serve_config as config
import _atk_serve_plans as serve_plans
import _atk_serve_sessions as serve_sessions
import _atk_serve_state as state
import _atk_wi_common as common
import _atk_wi_repo as awi_repo
import _atk_wi_user_comment as user_comment
import filelock
import pytest
import watchdog.events

# UI検証で起動する`node`は、CIの実行環境ではmiseのshimとして提供され、版と信頼設定の解決に
# 実行環境のホーム・設定ディレクトリを参照する。conftestの`_isolated_home`が差し替えた環境を
# そのまま渡すとこの解決が失敗するため、取り込み時点の環境変数を控えてNodeへ渡す。
# 同じ目的のconftestの`host_environ` fixtureは使わない。`node`を起動する`_run_node_ui`は
# module levelのヘルパーであり、fixtureを受け取るには全呼び出し元のテストへ引数を追加する必要がある。
_HOST_ENVIRON = dict(os.environ)


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
    (inbox / "awi.md").write_text(
        "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n本文\n",
        encoding="utf-8",
    )
    mutations = serve_app.awi_mutations
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)

    result = serve_app.Operations(tmp_path).transition("adopt", ["awi.md"], commit="abcdef1")

    assert result == ["awi.md"]
    assert "- 対応commit: abcdef1" in (tmp_path / "adopted/awi.md").read_text(encoding="utf-8")
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


def test_assets_define_dismissible_global_error_region() -> None:
    """共通エラーに本文、アクセシブルな消去操作及び十分な操作領域を持たせる。"""
    assert '<div id="global-error" class="global-error" hidden>' in assets.HTML
    assert '<div id="global-error-message" role="alert"></div>' in assets.HTML
    assert (
        '<button id="global-error-close-button" class="global-error-close" type="button" '
        'aria-label="エラーメッセージを閉じる">×</button>'
    ) in assets.HTML
    global_error = re.search(r"\.global-error \{(.*?)\n\}", assets.CSS, re.DOTALL)
    assert global_error is not None
    assert "display: flex;" in global_error.group(1)
    message = re.search(r"\.global-error-message \{(.*?)\n\}", assets.CSS, re.DOTALL)
    assert message is not None
    assert "overflow-wrap: anywhere;" in message.group(1)
    close = re.search(r"\.global-error-close \{(.*?)\n\}", assets.CSS, re.DOTALL)
    assert close is not None
    assert "width: 2.75rem;" in close.group(1)
    assert "height: 2.75rem;" in close.group(1)
    assert "min-width: 2.75rem;" in close.group(1)
    assert "min-height: 2.75rem;" in close.group(1)


def test_assets_define_pagination_and_dismissible_operation_notice() -> None:
    """一覧ページ移動と操作結果通知へ、キーボード操作可能な領域を持たせる。"""
    assert 'id="pagination"' in assets.HTML
    assert 'id="previous-page-button"' in assets.HTML
    assert 'id="next-page-button"' in assets.HTML
    assert 'id="pagination-status"' in assets.HTML
    assert 'id="operation-notice"' in assets.HTML
    assert 'id="operation-notice-message"' in assets.HTML
    assert (
        '<button id="operation-notice-close-button" class="operation-notice-close" type="button" '
        'aria-label="操作通知を閉じる">×</button>'
    ) in assets.HTML
    assert "parameters.set('page', String(page));" in assets.JS
    assert "new URLSearchParams({q: searchTerm, page: String(currentPage)})" in assets.JS
    assert 'operation-notice[data-error="true"]' in assets.CSS


def test_text_assets_are_bundled_as_plugin_files() -> None:
    """配布対象のscripts配下に実ファイルを同梱し、Python側がその内容を読む。"""
    static_dir = pathlib.Path(assets.__file__).with_name("_atk_serve_static")
    expected = {
        "index.html": assets.HTML,
        "app.css": assets.CSS,
        "app.js": assets.JS,
        "shell.css": assets.SHELL_CSS,
        "shell.js": assets.SHELL_JS,
        "plans.html": assets.PLANS_HTML,
        "plans.css": assets.PLANS_CSS,
        "plans.js": assets.PLANS_JS,
        "sessions.html": assets.SESSIONS_HTML,
        "sessions.css": assets.SESSIONS_CSS,
        "sessions.js": assets.SESSIONS_JS,
        "markdown.css": assets.MARKDOWN_CSS,
    }
    # Mermaidは容量が大きく要求時に読むため、内容の一致検査ではなく実在だけを確認する。
    assert {path.name for path in static_dir.iterdir()} == {*expected, "vendor"}
    assert (static_dir / "vendor" / "mermaid.min.js").is_file()
    for filename, content in expected.items():
        bundled = (static_dir / filename).read_text(encoding="utf-8")
        assert (bundled if filename == "app.js" else bundled.removesuffix("\n")) == content


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
    assert "未回答UWI 0件" in assets.HTML
    assert "種別・状態・回答状況" not in assets.HTML
    assert ">確認事項<" not in assets.HTML
    assert assets.HTML.count(">uwi<") == 2
    assert ">今すぐ同期<" in assets.HTML
    assert 'placeholder="本文・ファイル名・対象・投入元を検索"' in assets.HTML
    source_filter = re.search(r'<select id="source-filter">(.*?)</select>', assets.HTML, re.DOTALL)
    assert source_filter is not None
    assert re.findall(r'<option value="([^"]*)">(.*?)</option>', source_filter.group(1)) == [
        ("", "すべて"),
        ("human", "human"),
        ("agent", "agent"),
    ]
    assert "source-empty-filter" not in assets.HTML
    assert "dataset.unansweredUwi" in assets.JS
    assert "種別不明" in assets.JS

    grid = re.search(r"\.entry-columns, \.entry-select \{(.*?)\n\}", assets.CSS, re.DOTALL)
    assert grid is not None
    widths = re.findall(r"minmax\(([^)]+)\)", grid.group(1))
    assert re.search(r"grid-template-columns:\s*12rem", grid.group(1))
    assert widths == [
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
    # ヘッダーの1列化は`shell.css`が3画面共通で定め、画面固有のCSSは水平方向の余白だけを上書きする。
    assert ".app-header { padding-inline: var(--space-2); }" in mobile
    assert ".dialog-footer button { width: auto; }" in mobile
    assert "button,\n  input,\n  select,\n  textarea" not in mobile
    shell_mobile = assets.SHELL_CSS.partition("@media (max-width: 700px) {")[2]
    assert "grid-template-columns: minmax(0, 1fr);" in shell_mobile


def test_assets_size_dialogs_with_small_viewport_height_unit() -> None:
    """高さを決めるビューポート単位を無印`vh`ではなく`svh`で書く。

    無印`vh`は大ビューポート基準のため、モバイルのブラウザーUI展開時に
    ダイアログの外枠が可視領域を超え、ヘッダーとフッターが隠れる。
    `dvh`ではなく`svh`を採用するのは、スクロールに伴うブラウザーUIの伸縮で
    枠の高さが再レイアウトされることを避け、常に可視な下限側へ収めるためである。
    """
    assert not re.findall(r"\dvh\b", assets.CSS)
    assert "100svh" in assets.CSS


def test_assets_define_all_operation_lifecycles_and_message_regions() -> None:
    """6更新操作を共通pending処理へ接続し、結果領域を操作場所ごとに持つ。"""
    assert "async function runPending(" in assets.JS
    for key in ("'sync'", "'save'", "'answer'", "'user-comment'", "'create'", "'delete'"):
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
    source = assets.JS.replace("__BASE_PATH_JS__", '"/atk"')
    # 画面スクリプトは自身の宣言を即時実行関数で囲むため、シナリオも同じ関数の内側へ置いて
    # 検証対象の関数を直接呼べるようにする。
    closing = "})();\n"
    assert source.endswith(closing)
    executable = (
        source[: -len(closing)]
        + "\n(async () => {\n"
        + scenario
        + "\n})().catch(error => { process.stderr.write(String(error.stack || error)); process.exitCode = 1; });\n"
        + closing
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
  focus() {{
    if (this.disabled) return;
    document.activeElement = this;
    globalThis.focused = this.dataset.key || this.id;
    document.listeners.focusin?.({{target: this}});
  }}
}}
const ids = [
  'connection-status', 'sync-result', 'refresh-button', 'notification-button', 'create-button', 'global-error',
  'global-error-message', 'global-error-close-button',
  'clear-filters-button', 'search-input', 'kind-filter', 'state-filter', 'answer-filter',
  'target-filter', 'source-filter', 'entry-count',
  'result-status', 'list-warning', 'list-fallback-notice', 'loading-indicator', 'entry-list', 'empty-state',
  'empty-state-message', 'empty-clear-button', 'empty-all-states-button', 'empty-create-button',
  'detail-dialog', 'detail-shell', 'detail-dialog-body', 'detail-close-button', 'detail-alert',
  'detail-status', 'detail-view', 'detail-filename', 'detail-state', 'detail-metadata',
  'detail-content', 'readonly-notice', 'edit-button', 'answer-button', 'delete-button',
  'decision-panel', 'decision-note', 'adopt-button', 'reject-button', 'hold-button', 'unhold-button',
  'return-to-inbox-button',
  'edit-panel', 'edit-content', 'edit-content-error', 'save-entry-button', 'answer-panel',
  'answer-choices', 'answer-input', 'answer-input-error', 'save-answer-button', 'user-comment-button',
  'user-comment-panel', 'user-comment-input', 'user-comment-input-error', 'save-user-comment-button',
  'create-dialog', 'create-form', 'create-close-button', 'create-alert', 'create-status',
  'create-kind', 'create-content', 'create-content-label', 'create-content-error',
  'create-repo-fields', 'create-target',
  'create-target-error', 'create-source', 'uwi-fields', 'create-scope',
  'create-question-type', 'choice-fields', 'create-choices', 'create-choices-error',
  'create-submit-button', 'delete-dialog', 'delete-form', 'delete-close-button',
  'delete-alert', 'delete-status', 'delete-target', 'delete-state', 'delete-target-repo',
  'delete-summary', 'force-delete-row', 'force-delete-confirmation', 'delete-error',
  'delete-submit-button', 'repo-options', 'pagination', 'previous-page-button',
  'pagination-status', 'next-page-button', 'operation-notice', 'operation-notice-message',
  'operation-notice-close-button'
];
const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
elements['toast'] = elements['operation-notice-message'];
elements['operation-notice-close-button'].setAttribute('aria-label', '操作通知を閉じる');
elements['kind-filter'].value = 'all';
elements['state-filter'].value = 'active';
elements['answer-filter'].value = 'all';
elements['create-kind'].value = 'awi';
elements['create-question-type'].value = 'free-form';
globalThis.controlGroups = {{
  'detail-shell': [
    elements['detail-close-button'], elements['edit-button'], elements['answer-button'],
    elements['user-comment-button'], elements['delete-button'], elements['adopt-button'], elements['reject-button'],
    elements['hold-button'], elements['unhold-button'], elements['return-to-inbox-button'], elements['decision-note'],
    elements['edit-content'], elements['save-entry-button'],
    elements['answer-input'], elements['save-answer-button'], elements['user-comment-input'],
    elements['save-user-comment-button']
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
  listeners: {{}},
  getElementById(id) {{ return elements[id] || null; }},
  addEventListener(name, handler) {{ this.listeners[name] = handler; }},
  createElement(tagName) {{ return new Element('', tagName.toUpperCase()); }},
  createTextNode(text) {{ const node = new Element('', '#TEXT'); node.textContent = text; return node; }},
  querySelector(selector) {{ return selector === '.app-header' ? appHeader : null; }},
  querySelectorAll(selector) {{
    if (selector !== '.entry-select') return [];
    return elements['entry-list'].children.flatMap(item => item.children);
  }}
}};
globalThis.controlGroups['app-header'] = [elements['refresh-button'], elements['create-button']];
globalThis.window = globalThis;
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
        env=_HOST_ENVIRON,
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
refreshKnownUwis = async () => { throw new Error('外部更新失敗'); };
await reloadFromExternalChange();
await Promise.resolve();
const reloadError = elements['global-error-message'].textContent;
setGlobalError('');
refreshKnownUwis = async () => { throw new Error('初期化失敗'); };
initializeApp();
await initialization;
process.stdout.write(JSON.stringify({reloadError, initializationError: elements['global-error-message'].textContent}));
"""
    )
    assert result == {"reloadError": "外部更新失敗", "initializationError": "初期化失敗"}


def test_assets_global_error_uses_shared_lifecycle_for_all_generators() -> None:
    """共通エラーの消去・再表示と、各生成元の同一表示経路を検証する。"""
    result = _run_node_ui(
        """
bindEvents();
setGlobalError('最初のエラー');
const shown = {
  message: elements['global-error-message'].textContent,
  hidden: elements['global-error'].hidden
};
elements['global-error-close-button'].listeners.click();
const cleared = {
  message: elements['global-error-message'].textContent,
  hidden: elements['global-error'].hidden,
  focused
};
setGlobalError('後続のエラー');
const redisplayed = {
  message: elements['global-error-message'].textContent,
  hidden: elements['global-error'].hidden
};
const failures = [];
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '一覧取得失敗'})
});
await loadEntries();
failures.push(elements['global-error-message'].textContent);
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '対象取得失敗'})
});
await loadTargetRepos();
failures.push(elements['global-error-message'].textContent);
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '詳細取得失敗'})
});
await selectEntry({state: 'inbox', filename: 'detail.md'}, new Element('detail-origin', 'BUTTON'));
failures.push(elements['global-error-message'].textContent);
const ambiguous = {
  kind: 'awi', state: 'processing', filename: 'ambiguous.md', content: '本文', body_html: '<p>本文</p>',
  frontmatter_entries: []
};
displayEntry(ambiguous);
detailOriginKey = entryKey(ambiguous);
elements['detail-dialog'].open = true;
fetchHandler = async url => {
  if (url.includes('/processing/')) {
    return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
  }
  if (url.includes('/inbox/') || url.includes('/adopted/')) {
    const state = url.includes('/inbox/') ? 'inbox' : 'adopted';
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entry: {...ambiguous, state}})};
  }
  return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
};
await reloadOpenDetailFromExternalChange();
failures.push(elements['global-error-message'].textContent);
refreshKnownUwis = async () => { throw new Error('SSE更新失敗'); };
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: [], repos: []})
});
await reloadFromExternalChange();
await Promise.resolve();
failures.push(elements['global-error-message'].textContent);
deliverOperationMessage('ダイアログ外失敗', true);
failures.push(elements['operation-notice-message'].textContent);
refreshKnownUwis = async () => { throw new Error('初期化失敗'); };
initializeApp();
await initialization;
failures.push(elements['global-error-message'].textContent);
process.stdout.write(JSON.stringify({shown, cleared, redisplayed, failures}));
"""
    )
    assert result == {
        "shown": {"message": "最初のエラー", "hidden": False},
        "cleared": {"message": "", "hidden": True, "focused": "refresh-button"},
        "redisplayed": {"message": "後続のエラー", "hidden": False},
        "failures": [
            "一覧取得失敗",
            "対象取得失敗",
            "詳細取得失敗",
            "ambiguous.mdの移動先を一意に特定できません。詳細を開き直してください。",
            "SSE更新失敗",
            "ダイアログ外失敗",
            "初期化失敗",
        ],
    }


def test_assets_global_error_focuses_refresh_after_synchronization() -> None:
    """同期中の共通エラー消去後も、再び操作可能になった同期ボタンへフォーカスを移す。"""
    result = _run_node_ui(
        """
bindEvents();
let releaseSync;
let syncCalls = 0;
const syncBlocked = new Promise(resolve => { releaseSync = resolve; });
fetchHandler = async url => {
  if (url.endsWith('/api/sync')) {
    syncCalls += 1;
    await syncBlocked;
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: [], repos: []})};
};
const synchronization = elements['refresh-button'].listeners.click();
await Promise.resolve();
const disabled = elements['refresh-button'].disabled;
const duplicateSynchronization = elements['refresh-button'].listeners.click();
setGlobalError('同期中の外部更新失敗');
elements['global-error-close-button'].focus();
elements['global-error-close-button'].listeners.click();
const during = {
  hidden: elements['global-error'].hidden,
  focused
};
releaseSync();
await Promise.all([synchronization, duplicateSynchronization]);
process.stdout.write(JSON.stringify({
  disabled,
  syncCalls,
  during,
  after: {
    disabled: elements['refresh-button'].disabled,
    focused,
    hidden: elements['global-error'].hidden
  }
}));
"""
    )
    assert result == {
        "disabled": True,
        "syncCalls": 1,
        "during": {"hidden": True, "focused": "global-error-close-button"},
        "after": {"disabled": False, "focused": "refresh-button", "hidden": True},
    }


def test_assets_global_error_does_not_restore_refresh_after_user_focus_move() -> None:
    """同期中の消去後に利用者が別の入力へ移動した場合は、そのフォーカスを維持する。"""
    result = _run_node_ui(
        """
bindEvents();
let releaseSync;
const syncBlocked = new Promise(resolve => { releaseSync = resolve; });
fetchHandler = async url => {
  if (url.endsWith('/api/sync')) await syncBlocked;
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: [], repos: []})};
};
const synchronization = elements['refresh-button'].listeners.click();
await Promise.resolve();
setGlobalError('同期中の外部更新失敗');
elements['global-error-close-button'].focus();
elements['global-error-close-button'].listeners.click();
elements['search-input'].focus();
releaseSync();
await synchronization;
process.stdout.write(JSON.stringify({focused, hidden: elements['global-error'].hidden}));
"""
    )
    assert result == {"focused": "search-input", "hidden": True}


def test_assets_global_error_does_not_restore_refresh_after_later_error() -> None:
    """同期中の消去後に後続エラーが再表示された場合は閉じる操作のフォーカスを維持する。"""
    result = _run_node_ui(
        """
bindEvents();
let releaseSync;
const syncBlocked = new Promise(resolve => { releaseSync = resolve; });
fetchHandler = async url => {
  if (url.endsWith('/api/sync')) await syncBlocked;
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: [], repos: []})};
};
const synchronization = elements['refresh-button'].listeners.click();
await Promise.resolve();
setGlobalError('同期中の最初のエラー');
elements['global-error-close-button'].focus();
elements['global-error-close-button'].listeners.click();
setGlobalError('同期中の後続エラー');
elements['global-error-close-button'].focus();
releaseSync();
await synchronization;
process.stdout.write(JSON.stringify({
  focused,
  hidden: elements['global-error'].hidden,
  message: elements['global-error-message'].textContent
}));
"""
    )
    assert result == {
        "focused": "global-error-close-button",
        "hidden": False,
        "message": "同期中の後続エラー",
    }


def test_assets_render_single_list_warnings_and_filter_dependencies() -> None:
    """一覧警告、種別不明、件数通知、成立しないフィルター組合せの解除を検証する。"""
    result = _run_node_ui(
        """
entries = [
  {kind: 'uwi', state: 'inbox', filename: 'u.md', answered: false, summary: '未回答', target_repo: 'x/u'},
  {kind: 'unknown', state: 'inbox', filename: 'x.md', answered: null, summary: '不明', target_repo: 'x/u'},
  {kind: 'awi', state: 'inbox', filename: 'f.md', answered: null, plan: true, summary: '本文',
   target_repo: 'github.com/example/a-very-long-repository-name',
   updated_at: '2026-08-07T10:11:00+00:00'}
];
renderList([{filename: 'bad.md', reason: 'UTF-8として読み取れません'}], true);
const announced = elements['result-status'].textContent;
const warning = elements['list-warning'].textContent;
  const awiCells = elements['entry-list'].children[2].children[0].children;
  const kindState = awiCells[2].children.map(child => child.textContent);
const targetCell = awiCells[1];
  const summary = awiCells[3].textContent;
elements['kind-filter'].value = 'awi';
elements['answer-filter'].value = 'no';
elements['source-filter'].value = 'agent';
syncFilterDependencies();
elements['result-status'].textContent = '変更しない';
renderList([], false);
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
        "count": "3件（未回答UWI 1件）",
        "warning": "一覧から除外したファイル: bad.md（UTF-8として読み取れません）",
        "announced": "3件を表示",
        "kindState": ["awi", "inbox", "plan"],
        "targetLabel": "github.co…itory-name",
        "targetAria": "対象リポジトリ: github.com/example/a-very-long-repository-name",
        "rowAria": "f.md、github.com/example/a-very-long-repository-name、awi、inbox、plan、本文",
        "summary": "本文",
        "sseStatus": "変更しない",
        "answerValue": "all",
        "answerDisabled": True,
        "sourceValue": "agent",
        "sourceDisabled": False,
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
  kind: 'uwi', state: 'inbox', filename: 'question.md', answered: true, answer: '既存回答',
  summary: '質問', target_repo: 'example/repo', content: 'raw',
  body_html: '<h2>質問</h2><p>本文</p>', question_type: 'choice', choices: ['A', 'B'],
  frontmatter_entries: []
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


def test_assets_keep_terminal_entry_editing_read_only_and_show_identifiers() -> None:
    """終端状態では編集を隠し、削除とkind/state識別子を表示する。"""
    result = _run_node_ui(
        """
displayEntry({
  kind: 'uwi', state: 'adopted', filename: 'done.md', answered: true, answer: '回答',
  summary: '完了', target_repo: 'example/repo', content: 'raw', body_html: '<p>本文</p>',
  question_type: 'free-form', choices: [], frontmatter_entries: []
});
process.stdout.write(JSON.stringify({
  heading: elements['detail-state'].textContent,
  metadata: elements['detail-metadata'].children.map(child => child.children.map(item => item.textContent).join(':')),
  readonly: !elements['readonly-notice'].hidden,
  editHidden: elements['edit-button'].hidden,
  answerHidden: elements['answer-button'].hidden,
  deleteHidden: elements['delete-button'].hidden
}));
"""
    )
    assert result == {
        "heading": "uwi / adopted",
        "metadata": [
            "回答状況:回答済み",
            "対象リポジトリ:example/repo",
            "更新日時:—",
        ],
        "readonly": True,
        "editHidden": True,
        "answerHidden": True,
        "deleteHidden": False,
    }


def test_assets_offer_hold_and_rejected_actions_and_preserve_implicit_resolution() -> None:
    """保留・不採用の操作表示と、採否APIの明示状態送信境界を検証する。"""
    result = _run_node_ui(
        """
const base = {
  kind: 'awi', filename: 'entry.md', answered: null, summary: '対象', target_repo: 'example/repo',
  content: '本文', body_html: '<p>本文</p>', frontmatter_entries: []
};
const visibility = {};
for (const state of ['hold', 'rejected']) {
  displayEntry({...base, state});
  visibility[state] = {
    readonly: !elements['readonly-notice'].hidden,
    edit: !elements['edit-button'].hidden,
    adopt: !elements['adopt-button'].hidden,
    reject: !elements['reject-button'].hidden,
    remove: !elements['delete-button'].hidden,
    unhold: !elements['unhold-button'].hidden,
    returnToInbox: !elements['return-to-inbox-button'].hidden
  };
}
const sent = {};
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})
});
for (const [action, state] of [
  ['adopt', 'inbox'], ['adopt', 'processing'], ['adopt', 'hold'],
  ['reject', 'inbox'], ['reject', 'processing'], ['reject', 'hold']
]) {
  displayEntry({...base, state});
  fetchCalls.length = 0;
  await transitionDetail(action);
  const call = fetchCalls.find(item => item.url.endsWith(`/api/entries/${action}`));
  sent[`${action}-${state}`] = JSON.parse(call.options.body);
}
process.stdout.write(JSON.stringify({visibility, sent}));
"""
    )
    assert result == {
        "visibility": {
            "hold": {
                "readonly": False,
                "edit": True,
                "adopt": True,
                "reject": True,
                "remove": True,
                "unhold": True,
                "returnToInbox": False,
            },
            "rejected": {
                "readonly": False,
                "edit": False,
                "adopt": False,
                "reject": False,
                "remove": True,
                "unhold": False,
                "returnToInbox": True,
            },
        },
        "sent": {
            "adopt-inbox": {"filenames": ["entry.md"]},
            "adopt-processing": {"filenames": ["entry.md"]},
            "adopt-hold": {"filenames": ["entry.md"], "state": "hold"},
            "reject-inbox": {"filenames": ["entry.md"]},
            "reject-processing": {"filenames": ["entry.md"]},
            "reject-hold": {"filenames": ["entry.md"], "state": "hold"},
        },
    }


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
        "対象リポジトリ",
        "投入元",
        "priority",
        "int: 1",
        "1",
        "nested",
        "values",
        "更新日時",
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


def test_assets_notify_only_new_active_unanswered_uwi_after_permission() -> None:
    """初期集合と既知項目の属性変化を通知せず、新規未回答UWIだけを通知する。"""
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
    {kind: 'uwi', state: 'inbox', filename: 'base.md', answered: false, target_repo: 'old/repo'},
    {kind: 'uwi', state: 'inbox', filename: 'answered.md', answered: true},
    {kind: 'uwi', state: 'inbox', filename: 'moving.md', answered: false},
    {kind: 'uwi', state: 'adopted', filename: 'reappear.md', answered: true}
  ],
  [
    {kind: 'uwi', state: 'inbox', filename: 'base.md', answered: false, target_repo: 'new/repo'},
    {kind: 'uwi', state: 'inbox', filename: 'answered.md', answered: false},
    {kind: 'uwi', state: 'processing', filename: 'moving.md', answered: false},
    {kind: 'uwi', state: 'inbox', filename: 'new.md', answered: false}
  ],
  [
    {kind: 'uwi', state: 'inbox', filename: 'base.md', answered: false},
    {kind: 'uwi', state: 'inbox', filename: 'answered.md', answered: true},
    {kind: 'uwi', state: 'inbox', filename: 'moving.md', answered: false},
    {kind: 'uwi', state: 'inbox', filename: 'new.md', answered: true},
    {kind: 'uwi', state: 'inbox', filename: 'reappear.md', answered: false}
  ]
];
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK', json: async () => ({entries: snapshots.shift()})
});
syncNotificationButton();
const buttonVisibleBefore = !elements['notification-button'].hidden;
await enableNotifications();
const buttonHiddenAfter = elements['notification-button'].hidden;
await refreshKnownUwis({notify: false});
const afterBaseline = notifications.length;
await refreshKnownUwis({notify: true});
await refreshKnownUwis({notify: true});
process.stdout.write(JSON.stringify({
  buttonVisibleBefore, buttonHiddenAfter, afterBaseline, notifications,
  known: Array.from(knownUwiFilenames).sort()
}));
"""
    )
    assert result == {
        "buttonVisibleBefore": True,
        "buttonHiddenAfter": True,
        "afterBaseline": 0,
        "notifications": [{"title": "新規未回答UWI", "body": "new.md"}],
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
  kind: 'awi', state: 'processing', filename: 'entry.md', answered: null,
  summary: '要約', target_repo: 'example/repo', content: 'raw', body_html: '<p>本文</p>',
  question_type: 'free-form', choices: [], frontmatter_entries: []
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
  kind: 'awi', state: 'inbox', filename: 'first.md', answered: null,
  summary: '先頭', content: '本文', body_html: '<p>本文</p>', frontmatter_entries: []
};
const second = {
  kind: 'awi', state: 'inbox', filename: 'second.md', answered: null,
  summary: '次行', content: '本文', body_html: '<p>本文</p>', frontmatter_entries: []
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
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '一覧要約', target_repo: 'example/repo', frontmatter_entries: []
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
  kind: 'awi', state: 'processing', filename: 'same.md', answered: null,
  summary: '処理中', content: '処理中本文', body_html: '<p>処理中本文</p>', frontmatter_entries: []
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
    error: elements['global-error-message'].textContent
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
  kind: 'awi', state: 'processing', filename: 'same.md', answered: null,
  summary: '処理中', content: '処理中本文', body_html: '<p>処理中本文</p>', frontmatter_entries: []
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
const savedState = currentEntry.state;
await saveEntry();
process.stdout.write(JSON.stringify({putUrls, state: savedState, open: elements['detail-dialog'].open}));
"""
    )
    assert result == {
        "putUrls": ["/atk/api/entries/processing/same.md"],
        "state": "processing",
        "open": False,
    }


def test_assets_sse_reconciles_owned_delete_dialog() -> None:
    """SSE移動時は削除確認を閉じ、消失時は親子を閉じて一覧へ戻す。"""
    result = _run_node_ui(
        """
const inbox = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '移動前', target_repo: 'example/repo', content: '本文', body_html: '<p>本文</p>',
  frontmatter_entries: []
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
        "reopened": {"state": "awi / processing", "forceVisible": True},
        "missing": {
            "detailOpen": False,
            "deleteOpen": False,
            "focused": "inbox/remaining.md",
        },
    }


def test_assets_clear_self_write_sse_alert_after_save_and_answer_success() -> None:
    """保存・回答の応答前にSSEが届いても、成功後は競合警告を残さず、回答成功では詳細を閉じる。"""
    result = _run_node_ui(
        """
async function runSave() {
  let serverEntry = {
    kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
    summary: '保存対象', content: '更新前', body_html: '<p>更新前</p>', frontmatter_entries: []
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
    toast: elements['toast'].textContent,
    open: elements['detail-dialog'].open,
    mode: currentDetailMode()
  };
}

async function runAnswer() {
  let serverEntry = {
    kind: 'uwi', state: 'inbox', filename: 'question.md', answered: false,
    summary: '回答対象', content: '質問', body_html: '<p>質問</p>',
    question_type: 'free-form', choices: [], frontmatter_entries: []
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
    toast: elements['toast'].textContent,
    open: elements['detail-dialog'].open,
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
            "status": "",
            "toast": "inbox/entry.mdを保存しました。",
            "open": False,
            "mode": "view",
        },
        "answered": {
            "during": warning,
            "after": "",
            "status": "",
            "toast": "inbox/question.mdへ回答しました。",
            "open": False,
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
    kind: answering ? 'uwi' : 'awi', state: 'inbox',
    filename: answering ? 'question.md' : 'entry.md', answered: answering ? false : null,
    summary: '操作対象', content: '更新前', body_html: '<p>更新前</p>',
    question_type: 'free-form', choices: [], frontmatter_entries: []
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
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '削除対象', target_repo: 'example/repo', content: '取得時本文', body_html: '<p>取得時本文</p>',
  frontmatter_entries: []
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
    kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
    summary: '変更前の要約', target_repo: 'example/old', content: '変更前本文', body_html: '<p>変更前本文</p>',
    frontmatter_entries: []
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
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
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


def test_operation_result_uses_active_dialog_or_page_notification() -> None:
    """操作結果は最上位ダイアログへ送り、ダイアログが無い場合だけページ通知へ送る。"""
    result = _run_node_ui(
        """
const dialogResults = {};
for (const name of ['detail', 'create', 'delete']) {
  elements['operation-notice'].hidden = true;
  elements[`${name}-dialog`].open = true;
  dialogStack.push(`${name}-dialog`);
  deliverOperationMessage(`${name}失敗`, true);
  dialogResults[name] = {
    alert: elements[`${name}-alert`].textContent,
    status: elements[`${name}-status`].textContent,
    pageHidden: elements['operation-notice'].hidden
  };
  closeDialog(elements[`${name}-dialog`]);
}
deliverOperationMessage('保存完了', true);
process.stdout.write(JSON.stringify({
  dialogResults,
  message: elements['operation-notice-message'].textContent,
  error: elements['operation-notice'].dataset.error,
  role: elements['operation-notice'].attributes.role,
  closeLabel: elements['operation-notice-close-button'].attributes['aria-label']
}));
"""
    )
    assert result == {
        "dialogResults": {
            "detail": {"alert": "detail失敗", "status": "", "pageHidden": True},
            "create": {"alert": "create失敗", "status": "", "pageHidden": True},
            "delete": {"alert": "delete失敗", "status": "", "pageHidden": True},
        },
        "message": "保存完了",
        "error": "true",
        "role": "alert",
        "closeLabel": "操作通知を閉じる",
    }


def test_failed_dialog_updates_restore_actionable_focus() -> None:
    """更新失敗後は開いたダイアログ内の再操作可能な要素へフォーカスを戻す。"""
    result = _run_node_ui(
        """
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '失敗'})
});
const awi = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '要約', target_repo: 'example/repo', content: 'raw', body_html: '<p>本文</p>',
  question_type: 'free-form', choices: [], frontmatter_entries: []
};
elements['detail-dialog'].open = true;
dialogStack.push('detail-dialog');
displayEntry(awi);
enterEdit();
elements['edit-content'].value = '更新後';
await saveEntry();
const editFailure = focused;

displayEntry({...awi, kind: 'uwi', filename: 'question.md', answered: false});
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

displayEntry(awi);
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


def test_create_success_resets_filters_and_keeps_list() -> None:
    """追加成功後は既定条件へ戻し、返却されたファイルを一覧へ残して詳細を開かない。"""
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
  if (url.endsWith('/api/repos?status=active')) {
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
        "state": "active",
        "search": "",
        "detailOpen": False,
        "current": None,
        "body": "",
        "listCount": 1,
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
    current = state.ServeState(
        tmp_path,
        debounce_seconds=10.0,
        timer_factory=_FakeTimer,
    )
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
        "/api/entries/batch",
        "/api/entries/<state_name>/<filename>",
        "/api/entries/start-processing",
        "/api/entries/return-to-inbox",
        "/api/entries/hold",
        "/api/entries/unhold",
        "/api/entries/adopt",
        "/api/entries/reject",
        "/api/entries/remove",
        "/api/entries/commit",
        "/api/entries/answer",
        "/api/entries/user-comment",
        "/api/events",
    }
    removed = {"/api/status", "/api/enable", "/api/disable"}
    assert expected <= rules
    assert not removed & rules


def _three_screen_app(tmp_path: pathlib.Path) -> typing.Any:
    """3画面を登録したアプリを、外部へ接続しない依存で生成する。"""
    return serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        plans_context=serve_plans.create_context(root=tmp_path / "plans", hostname="local-host"),
        sessions_context=serve_sessions.create_context(
            hostname="local-host",
            claude_home=tmp_path / "claude",
            codex_home=tmp_path / "codex",
        ),
    )


def test_navigation_offers_three_screens_in_declared_order(tmp_path: pathlib.Path) -> None:
    """3画面のページ経路とナビゲーションの表示順・表記を固定する。"""
    app = _three_screen_app(tmp_path)
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert {"/", "/plans", "/sessions"} <= rules
    for document in (assets.HTML, assets.PLANS_HTML, assets.SESSIONS_HTML):
        navigation = re.search(r'<nav class="app-nav"[^>]*>(.*?)</nav>', document, re.DOTALL)
        assert navigation is not None
        assert re.findall(r">([^<>]+)</a>", navigation.group(1)) == ["WI", "計画ファイル", "セッション"]
        assert re.findall(r'href="__BASE_PATH_HTML__(/[a-z]*)"', navigation.group(1)) == ["/", "/plans", "/sessions"]
    # 現在の画面だけが`aria-current`を持つ。
    assert assets.HTML.count('aria-current="page"') == 1
    assert assets.PLANS_HTML.count('aria-current="page"') == 1
    assert assets.SESSIONS_HTML.count('aria-current="page"') == 1


def test_plan_and_session_api_routes_are_registered(tmp_path: pathlib.Path) -> None:
    """計画ファイル画面とセッション画面のAPI・SSE経路を登録する。"""
    app = _three_screen_app(tmp_path)
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    expected = {
        "/api/plans/files",
        "/api/plans/file",
        "/api/plans/raw",
        "/api/plans/search",
        "/api/plans/host-status",
        "/api/plans/host-info",
        "/api/plans/root-info",
        "/api/plans/root-status",
        "/api/plans/events",
        "/api/sessions/list",
        "/api/sessions/detail",
        "/api/sessions/host-status",
        "/api/sessions/events",
        "/static/shell.css",
        "/static/plans.css",
        "/static/plans.js",
        "/static/sessions.css",
        "/static/sessions.js",
        "/static/markdown.css",
        "/static/pygments.css",
        "/static/vendor/mermaid.min.js",
    }
    assert expected <= rules
    # SSEは画面ごとに分ける。単一経路へまとめると別画面の更新でも再読込することになる。
    assert "/api/events" in rules


@pytest.mark.asyncio
async def test_plan_and_session_pages_apply_base_path(tmp_path: pathlib.Path) -> None:
    """追加した2画面のページがベースパスを反映する。"""
    app = _three_screen_app(tmp_path)
    client = app.test_client()
    for path in ("/atk/plans", "/atk/sessions"):
        response = await client.get(path, headers={"X-Forwarded-Prefix": "/atk"})
        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert 'href="/atk/"' in body
        assert '"base_path": "/atk"' in body
        assert "/atk/atk/" not in body


@pytest.mark.asyncio
async def test_plan_and_session_apis_classify_input_errors(tmp_path: pathlib.Path) -> None:
    """追加した2画面のAPIが既存と同じ応答分類（入力不正400・未検出404）を返す。"""
    app = _three_screen_app(tmp_path)
    client = app.test_client()
    assert (await client.get("/api/plans/file")).status_code == 400
    assert (await client.get("/api/plans/file?path=a.md&host=unknown-host")).status_code == 400
    assert (await client.get("/api/sessions/detail")).status_code == 400
    assert (await client.get("/api/sessions/detail?engine=claude&path=../etc/passwd.jsonl")).status_code == 404
    assert (await client.get("/api/sessions/detail?engine=unknown&path=a.jsonl")).status_code == 404


def test_config_resolves_plans_and_sessions_sources(tmp_path: pathlib.Path) -> None:
    """設定の正本を`serve.toml`へ統合し、両画面の参照元を同じファイルで解決する。"""
    path = tmp_path / "serve.toml"
    path.write_text(
        'host = "toml-host"\n'
        "\n"
        "[plans]\n"
        'root = "/srv/plans"\n'
        'remote_hosts = ["circe", "stheno"]\n'
        "\n"
        "[sessions]\n"
        'claude_home = "/srv/claude"\n'
        'codex_home = "/srv/codex"\n'
        'remote_hosts = ["circe"]\n',
        encoding="utf-8",
    )
    resolved = config.resolve_config(environ={"AGENT_TOOLKIT_SERVE_CONFIG": str(path)}, platform="linux")
    assert resolved.host == "toml-host"
    assert resolved.port == 28766
    assert resolved.plans == config.PlansConfig(root="/srv/plans", remote_hosts=("circe", "stheno"))
    assert resolved.sessions == config.SessionsConfig(
        claude_home="/srv/claude",
        codex_home="/srv/codex",
        remote_hosts=("circe",),
    )


def test_config_defaults_when_sections_are_absent(tmp_path: pathlib.Path) -> None:
    """節を持たない設定では両画面の参照元を既定へ委ねる。"""
    path = tmp_path / "serve.toml"
    path.write_text("port = 3000\n", encoding="utf-8")
    resolved = config.resolve_config(environ={"AGENT_TOOLKIT_SERVE_CONFIG": str(path)}, platform="linux")
    assert resolved.plans == config.PlansConfig()
    assert resolved.sessions == config.SessionsConfig()


def test_config_ignores_legacy_plans_viewer_file(tmp_path: pathlib.Path) -> None:
    """旧`claude-plans-viewer.toml`は読み込まず、`serve.toml`だけを正本とする。"""
    legacy = tmp_path / "claude-plans-viewer.toml"
    legacy.write_text('root = "/legacy"\nremote-hosts = ["legacy-host"]\n', encoding="utf-8")
    path = tmp_path / "serve.toml"
    path.write_text("", encoding="utf-8")
    resolved = config.resolve_config(environ={"AGENT_TOOLKIT_SERVE_CONFIG": str(path)}, platform="linux")
    assert resolved.plans.root is None
    assert not resolved.plans.remote_hosts


def test_config_warns_unknown_keys_in_screen_sections(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """画面別の節の未知キーを警告して無視し、既知キーの解決は継続する。"""
    path = tmp_path / "serve.toml"
    path.write_text('[plans]\nroot = "/srv/plans"\nunknown_key = 1\n', encoding="utf-8")
    with caplog.at_level("WARNING"):
        resolved = config.resolve_config(environ={"AGENT_TOOLKIT_SERVE_CONFIG": str(path)}, platform="linux")
    assert resolved.plans.root == "/srv/plans"
    assert "unknown_key" in caplog.text


@pytest.mark.asyncio
async def test_transition_routes_forward_only_supported_explicit_states(tmp_path: pathlib.Path) -> None:
    """各遷移ルートが共通契約の明示状態だけを処理層へ渡す。"""

    class CaptureOperations(serve_app.Operations):
        def __init__(self, private_notes: pathlib.Path) -> None:
            super().__init__(private_notes)
            self.calls: list[tuple[str, list[str], dict[str, object]]] = []

        def transition(self, action: str, filenames: list[str], **kwargs: object) -> list[str]:
            self.calls.append((action, filenames, kwargs))
            return filenames

    operations = CaptureOperations(tmp_path)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    client = app.test_client()
    for action, state_name in (
        ("start-processing", "hold"),
        ("return-to-inbox", "rejected"),
        ("adopt", "hold"),
        ("reject", "hold"),
        ("remove", "hold"),
    ):
        response = await client.post(
            f"/api/entries/{action}",
            json={"filenames": ["entry.md"], "state": state_name},
        )
        assert response.status_code == 200
        assert operations.calls[-1] == (
            action,
            ["entry.md"],
            {
                "note": None,
                "commit": None,
                "target_repo": None,
                "force": False,
                "state": state_name,
                "expected_content": None,
            },
        )

    implicit = await client.post("/api/entries/adopt", json={"filenames": ["entry.md"]})
    assert implicit.status_code == 200
    assert operations.calls[-1] == (
        "adopt",
        ["entry.md"],
        {"note": None, "commit": None, "target_repo": None, "force": False},
    )

    invalid = await client.post(
        "/api/entries/adopt",
        json={"filenames": ["entry.md"], "state": "inbox"},
    )
    assert invalid.status_code == 400


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
    entry.write_text("---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n", encoding="utf-8")
    original_entry_type_from_metadata = common.entry_type_from_metadata

    async def race_request(path: str) -> typing.Any:
        started = threading.Event()
        release = threading.Event()

        def entry_type_from_metadata(entry_path: pathlib.Path, metadata: typing.Mapping[str, object]) -> str:
            started.set()
            release.wait()
            return original_entry_type_from_metadata(entry_path, metadata)

        monkeypatch.setattr(common, "entry_type_from_metadata", entry_type_from_metadata)
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
        "---\ntype: awi\ntarget_repo: example/repo\nsource: test\n---\n\n要約本文\n",
        encoding="utf-8",
    )

    def unexpected(*_args: object, **_kwargs: object) -> typing.NoReturn:
        raise AssertionError("読取り処理がGit同期を開始しました")

    monkeypatch.setattr(common, "repo_lock", unexpected)
    monkeypatch.setattr(common, "pull", unexpected)
    operations = serve_app.Operations(tmp_path)
    result, warnings = operations.entries_with_warnings({})
    assert not warnings
    assert result[0] | {
        "updated_at": result[0]["updated_at"],
    } == {
        "kind": "awi",
        "state": "inbox",
        "filename": "entry.md",
        "answered": None,
        "plan": False,
        "target_repo": "example/repo",
        "source": "test",
        "summary": "要約本文",
        "updated_at": result[0]["updated_at"],
    }
    detail = operations.detail("inbox", "entry.md")
    content = detail["content"]
    assert isinstance(content, str)
    assert content.endswith("要約本文\n")


def test_operations_read_legacy_type_values_as_current_kinds(tmp_path: pathlib.Path) -> None:
    """`atk wi migrate`の実行前に保存された`type`値を現行の種別として一覧へ返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n要約本文\n",
        encoding="utf-8",
    )
    (inbox / "question.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n質問本文\n",
        encoding="utf-8",
    )

    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({})

    assert not warnings
    assert {str(item["filename"]): item["kind"] for item in result} == {"entry.md": "awi", "question.md": "uwi"}
    assert [item["answered"] for item in result if item["filename"] == "question.md"] == [False]


def test_detail_returns_existing_uwi_answer(tmp_path: pathlib.Path) -> None:
    """回答済みUWIの詳細は既存回答を編集用に返す。"""
    _write_detail_entry(
        tmp_path,
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問本文\n\n"
        "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n既存回答\n2行目\n",
    )

    detail = serve_app.Operations(tmp_path).detail("inbox", "entry.md")

    assert detail["answer"] == "既存回答\n2行目"


def test_detail_preserves_frontmatter_as_strict_json_values(tmp_path: pathlib.Path) -> None:
    """詳細APIはfrontmatterの入れ子値とYAML固有型を厳格JSON互換値へ変換する。"""
    _write_detail_entry(
        tmp_path,
        "---\n"
        "type: awi\n"
        "target_repo: example/repo\n"
        "source: test\n"
        "binary_value: !!binary |\n"
        "  SGVsbG8=\n"
        "set_value: !!set\n"
        "  alpha: null\n"
        "  beta: null\n"
        "ordered: !!omap\n"
        "  - first: 1\n"
        "  - second: 2\n"
        "nested:\n"
        "  label: value\n"
        "  values:\n"
        "    - one\n"
        "    - two\n"
        "queue_schedule:\n"
        "  timestamp: 2026-08-20T12:34:56Z\n"
        "  day: 2026-08-20\n"
        "  nan: .nan\n"
        "  positive: .inf\n"
        "  negative: -.inf\n"
        "---\n\n本文\n",
    )

    detail = serve_app.Operations(tmp_path).detail("inbox", "entry.md")
    entries = typing.cast(list[dict[str, typing.Any]], detail["frontmatter_entries"])
    frontmatter = {item["key"]["value"]: item["value"] for item in entries}

    json.dumps(detail, ensure_ascii=False, allow_nan=False)
    assert frontmatter["binary_value"] == "SGVsbG8="
    assert frontmatter["set_value"] == ["alpha", "beta"]
    assert frontmatter["ordered"] == [["first", "1"], ["second", "2"]]
    assert frontmatter["nested"] == {"label": "value", "values": ["one", "two"]}
    assert frontmatter["queue_schedule"] == {
        "timestamp": "2026-08-20T12:34:56+00:00",
        "day": "2026-08-20",
        "nan": "NaN",
        "positive": "Infinity",
        "negative": "-Infinity",
    }


@pytest.mark.parametrize(
    "text",
    ["本文のみ\n", "---\ninvalid: [unterminated\n---\n本文\n"],
)
def test_detail_returns_empty_frontmatter_when_unavailable(tmp_path: pathlib.Path, text: str) -> None:
    """frontmatterが無い又は解析できない詳細は空の表示用一覧を返す。"""
    _write_detail_entry(tmp_path, text)
    detail = serve_app.Operations(tmp_path).detail("inbox", "entry.md")
    assert not detail["frontmatter_entries"]


@pytest.mark.asyncio
async def test_detail_api_round_trips_frontmatter_as_strict_json(tmp_path: pathlib.Path) -> None:
    """詳細HTTP APIは非有限浮動小数点を含むfrontmatterも標準JSONとして返す。"""
    _write_detail_entry(
        tmp_path,
        "---\ntype: awi\nqueue_schedule:\n  nan: .nan\n  positive: .inf\n---\n\n本文\n",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries/inbox/entry.md")

    assert response.status_code == 200

    def reject_constant(_value: str) -> typing.NoReturn:
        raise ValueError("非標準JSON定数です")

    payload = json.loads(await response.get_data(), parse_constant=reject_constant)
    assert payload["entry"]["frontmatter_entries"] == [
        {"key": {"type": "str", "value": "type"}, "value": "awi"},
        {
            "key": {"type": "str", "value": "queue_schedule"},
            "value": {"nan": "NaN", "positive": "Infinity"},
        },
    ]


@pytest.mark.asyncio
async def test_detail_api_preserves_frontmatter_order_cycles_and_mapping_keys(tmp_path: pathlib.Path) -> None:
    """詳細HTTP APIはfrontmatterの順序、循環参照、非文字列キーを保持して返す。"""
    _write_detail_entry(
        tmp_path,
        "---\n"
        "type: awi\n"
        "z_key: z\n"
        "a_key: a\n"
        "m_key: m\n"
        "queue_schedule:\n"
        "  1: numeric\n"
        '  "1": textual\n'
        "  cyclic: &cyclic\n"
        "    kept: value\n"
        "    self: *cyclic\n"
        "---\n\n本文\n",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries/inbox/entry.md")

    assert response.status_code == 200
    payload = json.loads(await response.get_data())
    frontmatter_entries = payload["entry"]["frontmatter_entries"]
    assert [item["key"]["value"] for item in frontmatter_entries] == [
        "type",
        "z_key",
        "a_key",
        "m_key",
        "queue_schedule",
    ]
    assert frontmatter_entries[:4] == [
        {"key": {"type": "str", "value": "type"}, "value": "awi"},
        {"key": {"type": "str", "value": "z_key"}, "value": "z"},
        {"key": {"type": "str", "value": "a_key"}, "value": "a"},
        {"key": {"type": "str", "value": "m_key"}, "value": "m"},
    ]
    assert frontmatter_entries[4] == {
        "key": {"type": "str", "value": "queue_schedule"},
        "value": {
            "__mapping__": [
                {"key": {"type": "int", "value": 1}, "value": "numeric"},
                {"key": {"type": "str", "value": "1"}, "value": "textual"},
                {
                    "key": {"type": "str", "value": "cyclic"},
                    "value": {"kept": "value", "self": "[Circular]"},
                },
            ]
        },
    }


@pytest.mark.asyncio
async def test_detail_api_preserves_top_level_key_types_and_order(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """詳細HTTP APIは整数形式キーを含むトップレベル項目の型と挿入順を保持する。"""
    _write_detail_entry(
        tmp_path,
        "---\ntype: awi\nz_key: z\na_key: a\n---\n\n本文\n",
    )

    original_parse = serve_app.frontmatter.parse_frontmatter

    def parse_with_integer_key(text: str) -> tuple[dict[typing.Any, typing.Any], str] | None:
        parsed = original_parse(text)
        if parsed is None:
            return None
        metadata, body = parsed
        enriched: dict[typing.Any, typing.Any] = {}
        for key, value in metadata.items():
            enriched[key] = value
            if key == "z_key":
                enriched[1] = "numeric"
                enriched["1"] = "textual"
        return enriched, body

    monkeypatch.setattr(serve_app.frontmatter, "parse_frontmatter", parse_with_integer_key)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries/inbox/entry.md")

    assert response.status_code == 200
    payload = json.loads(await response.get_data())
    assert payload["entry"]["frontmatter_entries"] == [
        {"key": {"type": "str", "value": "type"}, "value": "awi"},
        {"key": {"type": "str", "value": "z_key"}, "value": "z"},
        {"key": {"type": "int", "value": 1}, "value": "numeric"},
        {"key": {"type": "str", "value": "1"}, "value": "textual"},
        {"key": {"type": "str", "value": "a_key"}, "value": "a"},
    ]


def _write_detail_entry(tmp_path: pathlib.Path, text: str) -> None:
    """詳細表示テスト用の入力ファイルを作成する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "entry.md").write_text(text, encoding="utf-8")


def test_detail_renders_frontmatter_as_table(tmp_path: pathlib.Path) -> None:
    """frontmatterは入れ子の長い値も表として描画し、区切り行由来の見出しを生成しない。"""
    _write_detail_entry(
        tmp_path,
        "---\ntarget_repo: github.com/ak110/dotfiles\ntype: awi\n"
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
    _write_detail_entry(tmp_path, '---\ntype: awi\nnote: "<script>alert(1)</script>"\n---\n\n本文\n')
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
    monkeypatch.setattr(common, "entry_type_from_metadata", lambda *_args: "awi")
    rendered = typing.cast(str, serve_app.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert '<table class="frontmatter">' not in rendered
    # 分離自体は成立するため、開始区切りが水平線として残らない。
    assert "<hr" not in rendered
    assert "<p>本文</p>" in rendered


def test_detail_with_frontmatter_only_returns_empty_body_html(tmp_path: pathlib.Path) -> None:
    """frontmatterだけの詳細は本文用HTMLを空文字列として返す。"""
    _write_detail_entry(tmp_path, "---\ntype: awi\ntarget_repo: example/repo\n---\n")

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
        f"---\ntype: uwi\ntarget_repo: example/repo\n{question_source}---\n\n## 質問\n\n質問本文\n",
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
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (processing / "a-first.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n"
        "# 見出し\n\n- 項目\n\n```python\nprint('x')\n```\n\n<script>alert(1)</script>\n",
        encoding="utf-8",
    )

    operations = serve_app.Operations(tmp_path)
    entries, warnings = operations.entries_with_warnings({"status": "active"})
    assert not warnings
    assert [item["filename"] for item in entries] == ["z-last.md", "a-first.md"]
    detail = operations.detail("processing", "a-first.md")
    rendered = typing.cast(str, detail["content_html"])
    assert "<h1>見出し</h1>" in rendered
    assert "<li>項目</li>" in rendered
    assert '<code class="language-python">' in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


def test_render_body_renders_footnote_with_document_anchor() -> None:
    """注記記法は本文と同一文書内の参照リンクとして描画する。"""
    rendered = serve_app._render_body("本文です[^1]。\n\n[^1]: 注記の本文\n")

    assert "注記の本文" in rendered
    assert 'href="#fn1"' in rendered
    assert 'href="%E6%B3%A8%E8%A8%98%E3%81%AE%E6%9C%AC%E6%96%87"' not in rendered


def test_operations_active_includes_hold_entries_of_both_types(tmp_path: pathlib.Path) -> None:
    """一覧APIのactive状態はhold配下のawiとUWIをいずれも含める。"""
    hold = tmp_path / "hold"
    hold.mkdir()
    (hold / "held.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n保留中\n",
        encoding="utf-8",
    )
    (hold / "held-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n保留中UWI\n",
        encoding="utf-8",
    )

    entries, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"status": "active"})

    assert not warnings
    assert sorted(str(item["filename"]) for item in entries) == ["held-uwi.md", "held.md"]
    assert {item["state"] for item in entries} == {"hold"}


@pytest.mark.parametrize(
    ("frontmatter_source", "expected_repo", "expected_source"),
    [
        (
            "type: awi\ntarget_repo: example/repo\nsource: test\nplan_file: /tmp/plan.md\ndepends_on:\n  - predecessor.md\n",
            "example/repo",
            "test",
        ),
        ("type: awi\ntarget_repo: [broken\n", None, None),
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

    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({})
    assert not warnings

    assert result[0]["target_repo"] == expected_repo
    assert result[0]["source"] == expected_source


def test_operations_answered_filter_returns_only_answered_uwis(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`answered=yes`は回答済みUWIのみを返し、未回答UWI・AWIを除外する。"""
    monkeypatch.setattr(common, "repo_lock", lambda *_a, **_k: contextlib.nullcontext())
    monkeypatch.setattr(common, "pull", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "answered.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n回答済み\n",
        encoding="utf-8",
    )
    (inbox / "unanswered.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    (inbox / "awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\nAWI本文\n",
        encoding="utf-8",
    )
    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"answered": "yes"})
    assert not warnings
    assert [item["filename"] for item in result] == ["answered.md"]


@pytest.mark.parametrize("query", ["本文途中の固有語", "日本語", "EXAMPLE/REPO", "entry.md"])
def test_operations_query_searches_full_markdown_and_metadata(tmp_path: pathlib.Path, query: str) -> None:
    """`q`は本文途中、Unicode、frontmatter値、ファイル名を大文字小文字を区別せず検索する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n先頭\n\n日本語と本文途中の固有語\n",
        encoding="utf-8",
    )
    (inbox / "other.md").write_text(
        "---\ntype: awi\ntarget_repo: other/repo\n---\n\n別本文\n",
        encoding="utf-8",
    )

    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"q": query})
    assert not warnings

    assert [item["filename"] for item in result] == ["entry.md"]


def test_operations_parses_each_scanned_entry_once_before_query_filter(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本文検索で除外するエントリも含め、走査したMarkdownを1件1回だけ解析する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    for filename, body in (("match.md", "検索対象"), ("other.md", "別の本文")):
        (inbox / filename).write_text(
            f"---\ntype: awi\ntarget_repo: example/repo\n---\n\n{body}\n",
            encoding="utf-8",
        )
    original_parse = serve_app.frontmatter.parse_frontmatter
    parse_calls = 0

    def counting_parse(text: str) -> tuple[dict[str, typing.Any], str] | None:
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(text)

    monkeypatch.setattr(serve_app.frontmatter, "parse_frontmatter", counting_parse)

    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"q": "検索対象"})

    assert not warnings
    assert [item["filename"] for item in result] == ["match.md"]
    assert parse_calls == 2


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
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n投入元なし\n",
        encoding="utf-8",
    )
    (inbox / "empty-source.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: \n---\n\n空投入元\n",
        encoding="utf-8",
    )
    (inbox / "whitespace-source.md").write_text(
        '---\ntype: awi\ntarget_repo: example/repo\nsource: "   "\n---\n\n空白投入元\n',
        encoding="utf-8",
    )
    (inbox / "with-source.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: web\n---\n\n投入元あり\n",
        encoding="utf-8",
    )
    (inbox / "list-source.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: []\n---\n\nリスト形式の投入元\n",
        encoding="utf-8",
    )
    result, warnings = serve_app.Operations(tmp_path).entries_with_warnings({"source_empty": "true"})
    assert not warnings
    filenames = [typing.cast(str, item["filename"]) for item in result]
    filenames.sort()
    assert filenames == ["empty-source.md", "no-source.md", "whitespace-source.md"]


@pytest.mark.asyncio
async def test_entries_api_filters_source_kind_and_preserves_raw_source_filters(tmp_path: pathlib.Path) -> None:
    """一覧APIは投入元分類を適用し、既存のraw投入元フィルターを維持する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    for filename, source in [
        ("agent.md", "agent"),
        ("alert-monitor.md", "alert-monitor"),
        ("review.md", "session-review"),
        ("plan.md", "plan"),
        ("unknown.md", "web"),
        ("human.md", "human"),
        ("empty.md", None),
    ]:
        metadata = "type: awi\ntarget_repo: example/repo"
        if source is not None:
            metadata += f"\nsource: {source}"
        (inbox / filename).write_text(f"---\n{metadata}\n---\n\n本文\n", encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    agent = await client.get("/api/entries?status=inbox&type=awi&source_kind=agent")
    human = await client.get("/api/entries?status=inbox&type=awi&source_kind=human")
    raw = await client.get("/api/entries?status=inbox&type=awi&source=session-review")
    empty = await client.get("/api/entries?status=inbox&type=awi&source_empty=true")

    assert {item["filename"] for item in (await agent.get_json())["entries"]} == {
        "agent.md",
        "alert-monitor.md",
        "human.md",
        "plan.md",
        "review.md",
        "unknown.md",
    }
    assert {item["filename"] for item in (await human.get_json())["entries"]} == {"empty.md"}
    assert [item["filename"] for item in (await raw.get_json())["entries"]] == ["review.md"]
    assert {item["filename"] for item in (await empty.get_json())["entries"]} == {"empty.md"}


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
async def test_add_api_rejects_uwi_only_scope_on_awi(tmp_path: pathlib.Path) -> None:
    """UWI専用の`scope`をAWIへ指定すると拒否する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries",
        json={"type": "awi", "messages": ["x"], "target_repo": "example/repo", "scope": "s"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_uwi_reject_transition_succeeds(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """UWIエントリも他種別と同様に不採用遷移が成功する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(common, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(common, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.awi_mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: uwi\ntarget_repo: github.com/example/foo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
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
async def test_answer_api_rejects_awi_entry(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AWIエントリへの回答送信は拒否する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.uwi_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.uwi_mutations, "_pull", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
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

    for module in (common, awi_repo, serve_app.awi_mutations, serve_app.uwi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(module, "_push_pending_commits", lambda _path: None, raising=False)
    monkeypatch.setattr(common, "repo_lock", lock)
    monkeypatch.setattr(common, "pull", lambda _path: None)

    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    awi_path = inbox / "awi.md"
    initial_awi = "---\ntype: awi\ntarget_repo: example/repo\n---\n\n取得時本文\n"
    awi_path.write_text(initial_awi, encoding="utf-8")
    uwi_path = inbox / "question.md"
    initial_uwi = (
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    uwi_path.write_text(initial_uwi, encoding="utf-8")

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    awi_detail = await (await client.get("/api/entries/inbox/awi.md")).get_json()
    external_awi = initial_awi.replace("取得時本文", "外部更新後の本文")
    awi_path.write_text(external_awi, encoding="utf-8")
    conflict = await client.put(
        "/api/entries/inbox/awi.md",
        json={"content": "利用者の本文", "expected_content": awi_detail["entry"]["content"]},
    )
    assert conflict.status_code == 409
    assert (await conflict.get_json())["code"] == "edit_conflict"
    assert awi_path.read_text(encoding="utf-8") == external_awi

    latest_content = external_awi.replace("外部更新後の本文", "最新基準で更新")
    latest = await client.put(
        "/api/entries/inbox/awi.md",
        json={"content": latest_content, "expected_content": external_awi},
    )
    assert latest.status_code == 200
    assert awi_path.read_text(encoding="utf-8") == latest_content
    legacy_content = latest_content.replace("最新基準で更新", "従来形式で更新")
    legacy = await client.put(
        "/api/entries/inbox/awi.md",
        json={"content": legacy_content},
    )
    assert legacy.status_code == 200
    assert awi_path.read_text(encoding="utf-8") == legacy_content

    uwi_detail = await (await client.get("/api/entries/inbox/question.md")).get_json()
    external_uwi = initial_uwi.replace("質問？", "外部更新後の質問？")
    uwi_path.write_text(external_uwi, encoding="utf-8")
    answer_conflict = await client.post(
        "/api/entries/answer",
        json={
            "filename": "question.md",
            "answer": "古い基準からの回答",
            "expected_content": uwi_detail["entry"]["content"],
        },
    )
    assert answer_conflict.status_code == 409
    assert (await answer_conflict.get_json())["code"] == "edit_conflict"
    assert uwi_path.read_text(encoding="utf-8") == external_uwi

    latest_answer = await client.post(
        "/api/entries/answer",
        json={
            "filename": "question.md",
            "answer": "最新基準からの回答",
            "expected_content": external_uwi,
        },
    )
    assert latest_answer.status_code == 200
    assert uwi_path.read_text(encoding="utf-8").endswith("最新基準からの回答\n")
    legacy_answer = await client.post(
        "/api/entries/answer",
        json={"filename": "question.md", "answer": "従来形式からの回答"},
    )
    assert legacy_answer.status_code == 200
    assert uwi_path.read_text(encoding="utf-8").endswith("従来形式からの回答\n")


@pytest.mark.asyncio
async def test_answer_and_remove_apis_target_state_and_keep_legacy_resolution(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態指定時は表示対象へ作用し、省略時はprocessing優先を維持する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, serve_app.awi_mutations, serve_app.uwi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(module, "_push_pending_commits", lambda _path: None, raising=False)
    inbox = tmp_path / "inbox"
    processing = tmp_path / "processing"
    inbox.mkdir()
    processing.mkdir()
    uwi_content = (
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    awi_content = "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n"
    for filename in ("answer-state.md", "answer-legacy.md"):
        (inbox / filename).write_text(uwi_content, encoding="utf-8")
        (processing / filename).write_text(uwi_content, encoding="utf-8")
    for filename in ("remove-state.md", "remove-legacy.md", "remove-protected.md"):
        (inbox / filename).write_text(awi_content, encoding="utf-8")
        (processing / filename).write_text(awi_content, encoding="utf-8")

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
            "expected_content": uwi_content,
        },
    )
    assert state_answer.status_code == 200
    assert (inbox / "answer-state.md").read_text(encoding="utf-8").endswith("未処理側への回答\n")
    assert (processing / "answer-state.md").read_text(encoding="utf-8") == uwi_content

    legacy_answer = await client.post(
        "/api/entries/answer",
        json={"filename": "answer-legacy.md", "answer": "従来経路の回答"},
    )
    assert legacy_answer.status_code == 200
    assert (inbox / "answer-legacy.md").read_text(encoding="utf-8") == uwi_content
    assert (processing / "answer-legacy.md").read_text(encoding="utf-8").endswith("従来経路の回答\n")

    state_remove = await client.post(
        "/api/entries/remove",
        json={
            "filenames": ["remove-state.md"],
            "state": "inbox",
            "expected_content": awi_content,
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

    for module in (common, serve_app.awi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(module, "_push_pending_commits", lambda _path: None, raising=False)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = "---\ntype: awi\n---\n\n確認時本文\n"
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

    for module in (common, serve_app.awi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(module, "_push_pending_commits", lambda _path: None, raising=False)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    target = inbox / "unreadable.md"
    target.write_bytes(b"\xff")
    original = "---\ntype: awi\ntarget_repo: github.com/example/repo\n---\n\n取得時本文\n"
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

    for module in (common, serve_app.uwi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(module, "_push_pending_commits", lambda _path: None, raising=False)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    target = inbox / "question.md"
    target.write_bytes(b"\xff")
    original = (
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
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

    for module in (common, serve_app.awi_mutations, serve_app.uwi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(module, "_push_pending_commits", lambda _path: None, raising=False)
    inbox = tmp_path / "inbox"
    processing = tmp_path / "processing"
    inbox.mkdir()
    processing.mkdir()
    uwi_content = (
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    awi_content = "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n"
    answer_inbox = inbox / "answer.md"
    remove_inbox = inbox / "remove.md"
    answer_inbox.write_text(uwi_content, encoding="utf-8")
    remove_inbox.write_text(awi_content, encoding="utf-8")

    def move_during_pull(_path: pathlib.Path) -> None:
        if answer_inbox.exists():
            answer_inbox.rename(processing / answer_inbox.name)
        elif remove_inbox.exists():
            remove_inbox.rename(processing / remove_inbox.name)

    monkeypatch.setattr(serve_app.awi_mutations, "_pull", move_during_pull)
    monkeypatch.setattr(serve_app.uwi_mutations, "_pull", move_during_pull)
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
            "expected_content": uwi_content,
        },
    )
    remove_response = await client.post(
        "/api/entries/remove",
        json={
            "filenames": [remove_inbox.name],
            "state": "inbox",
            "expected_content": awi_content,
            "force": False,
        },
    )

    for response in (answer_response, remove_response):
        assert response.status_code == 409
        assert await response.get_json() == {
            "code": "edit_conflict",
            "error": "編集中に他プロセスが対象を変更しました",
        }
    assert (processing / answer_inbox.name).read_text(encoding="utf-8") == uwi_content
    assert (processing / remove_inbox.name).read_text(encoding="utf-8") == awi_content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload", "filename", "initial_content"),
    [
        (
            "put",
            "/api/entries/inbox/awi.md",
            {"content": "更新本文", "expected_content": None},
            "awi.md",
            "変更前の本文\n",
        ),
        (
            "put",
            "/api/entries/inbox/awi.md",
            {"content": "更新本文", "expected_content": ""},
            "awi.md",
            "変更前の本文\n",
        ),
        (
            "put",
            "/api/entries/inbox/awi.md",
            {"content": "更新本文", "expected_content": "  "},
            "awi.md",
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


def test_operations_sort_entries_with_unanswered_uwi_then_mixed_remaining(tmp_path: pathlib.Path) -> None:
    """一覧は未回答UWIを先頭に置き、残りを種別混在のファイル名降順で返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    # ファイル名の昇順でファイルを作成
    (inbox / "a-awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (inbox / "z-answered-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問\n\n## 回答\n\n回答済み\n",
        encoding="utf-8",
    )
    (inbox / "m-answered-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問\n\n## 回答\n\n回答済み\n",
        encoding="utf-8",
    )
    (inbox / "a-unanswered-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    (inbox / "d-awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (inbox / "z-awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )

    operations = serve_app.Operations(tmp_path)
    result, warnings = operations.entries_with_warnings({})
    assert not warnings
    filenames = [item["filename"] for item in result]

    assert filenames == [
        "a-unanswered-uwi.md",
        "z-awi.md",
        "z-answered-uwi.md",
        "m-answered-uwi.md",
        "d-awi.md",
        "a-awi.md",
    ]
    # 種別の確認
    assert result[0]["kind"] == "uwi"
    assert [item["kind"] for item in result[1:]] == ["awi", "uwi", "uwi", "awi", "awi"]


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 100, 101])
async def test_entries_api_paginates_at_one_hundred_entries(tmp_path: pathlib.Path, count: int) -> None:
    """一覧APIは明示ページだけを100件単位で返し、総数境界と空一覧を正規化する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for index in range(count):
        (inbox / f"entry-{index:03d}.md").write_text(
            "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
            encoding="utf-8",
        )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries?status=inbox&page=1")

    assert response.status_code == 200
    payload = await response.get_json()
    expected_page_count = max(1, math.ceil(count / 100))
    assert len(payload["entries"]) == min(count, 100)
    assert payload["pagination"] == {
        "page": 1,
        "page_size": 100,
        "page_count": expected_page_count,
        "total_count": count,
    }
    if count == 101:
        second = await app.test_client().get("/api/entries?status=inbox&page=2")
        second_payload = await second.get_json()
        assert len(second_payload["entries"]) == 1
        assert second_payload["pagination"]["page"] == 2


@pytest.mark.asyncio
async def test_entries_api_omits_pagination_without_explicit_page_and_clamps_final_page(
    tmp_path: pathlib.Path,
) -> None:
    """ページ省略時の既存payloadを維持し、最終ページを超える指定を末尾へ正規化する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for index in range(101):
        (inbox / f"entry-{index:03d}.md").write_text(
            "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
            encoding="utf-8",
        )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    complete = await client.get("/api/entries?status=inbox")
    clamped = await client.get("/api/entries?status=inbox&page=999")

    complete_payload = await complete.get_json()
    clamped_payload = await clamped.get_json()
    assert set(complete_payload) == {"entries", "warnings"}
    assert len(complete_payload["entries"]) == 101
    assert clamped_payload["pagination"] == {
        "page": 2,
        "page_size": 100,
        "page_count": 2,
        "total_count": 101,
    }
    assert len(clamped_payload["entries"]) == 1
    assert clamped_payload["entries"][0]["filename"] == complete_payload["entries"][-1]["filename"]


@pytest.mark.asyncio
async def test_entries_api_filters_awi_plan_type_and_keeps_warnings_full_scan(tmp_path: pathlib.Path) -> None:
    """awiの通常型・計画型を独立条件で限定し、ページ警告は全走査分を返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "normal.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n通常\n",
        encoding="utf-8",
    )
    (inbox / "plan.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nplan_file: /tmp/plan.md\n---\n\n計画\n",
        encoding="utf-8",
    )
    (inbox / "question.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n質問\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    normal = await client.get("/api/entries?status=inbox&type=awi&plan=normal&page=1")
    planned = await client.get("/api/entries?status=inbox&type=all&plan=plan&page=1")

    assert [item["filename"] for item in (await normal.get_json())["entries"]] == ["normal.md"]
    assert [item["filename"] for item in (await planned.get_json())["entries"]] == ["plan.md"]
    assert (await normal.get_json())["entries"][0]["plan"] is False
    assert (await planned.get_json())["entries"][0]["plan"] is True


@pytest.mark.asyncio
async def test_entries_api_keeps_readable_entries_and_reports_unreadable_files(tmp_path: pathlib.Path) -> None:
    """一覧APIは読取り不能な1ファイルを警告へ分離し、残りの一覧を返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "readable.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
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


def test_serve_state_watches_all_queue_states(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """状態監視は7状態フォルダを対象とし、旧feedback/tbd階層を生成しない。"""
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
        assert sorted(pathlib.Path(p).name for p in scheduled) == [
            "adopted",
            "hold",
            "inbox",
            "processing",
            "rejected",
        ]
        assert not (tmp_path / "awi").exists()
        assert not (tmp_path / "uwi").exists()
    finally:
        loop.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/entries?status=unknown", None),
        ("get", "/api/entries?page=0", None),
        ("get", "/api/entries?page=abc", None),
        ("get", "/api/entries?target_repo=", None),
        ("get", "/api/entries?q=", None),
        ("get", "/api/entries?source_empty=false", None),
        ("get", "/api/entries?source=web&source_empty=true", None),
        ("get", "/api/entries?source_kind=unknown", None),
        ("get", "/api/entries?source_kind=agent&source=web", None),
        ("get", "/api/entries?source_kind=human&source_empty=true", None),
        (
            "post",
            "/api/entries",
            {"type": "awi", "messages": ["x"], "target_repo": "example/repo", "source": ""},
        ),
        (
            "post",
            "/api/entries",
            {
                "type": "uwi",
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
        ("/api/entries", {"type": "awi", "messages": ["awi"]}),
        (
            "/api/entries",
            {"type": "uwi", "messages": ["UWIですか？"], "scope": "test", "question_type": "free-form"},
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
        ("/api/entries", {"type": "awi", "messages": ["awi"], "target_repo": None}),
        (
            "/api/entries",
            {
                "type": "uwi",
                "messages": ["UWIですか？"],
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
    monkeypatch.setattr(common, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.awi_mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
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
                "type": "awi",
                "messages": ["awi"],
                "target_repo": "https://github.com/Example/Specified.git",
            },
            "github.com/example/specified",
        ),
        (
            "/api/entries",
            {
                "type": "uwi",
                "messages": ["UWIですか？"],
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

    monkeypatch.setattr(awi_repo, "resolve_repo_id", resolve)
    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(common, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(common, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_add, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.awi_add, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_add, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_app.uwi_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.uwi_mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.uwi_mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
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
        "name": "WI管理",
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
        f"---\ntarget_repo: {target_repo}\ntype: awi\n---\n\n本文\n",
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


def test_repository_readers_canonicalize_legacy_paths_once_per_operation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一覧フィルターと候補収集は旧パス形を統合し、解決不能値の原値を保持する。"""
    local_repo = tmp_path / "target-repo"
    local_repo.mkdir()
    _write_repo_entry(tmp_path, "inbox", "current.md", "github.com/example/repo")
    _write_repo_entry(tmp_path, "inbox", "legacy-a.md", str(local_repo))
    _write_repo_entry(tmp_path, "inbox", "legacy-b.md", str(local_repo))
    _write_repo_entry(tmp_path, "inbox", "other.md", "github.com/example/other")
    _write_repo_entry(tmp_path, "inbox", "missing.md", str(tmp_path / "missing"))
    _write_repo_entry(tmp_path, "inbox", "unresolved.md", "example/repo")
    resolved_paths: list[str] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        resolved_paths.append(cmd[2])
        return subprocess.CompletedProcess(cmd, 0, stdout="https://github.com/example/repo.git\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    operations = serve_app.Operations(tmp_path)

    url_entries, warnings = operations.entries_with_warnings({"target_repo": "github.com/example/repo"})
    assert [item["filename"] for item in url_entries] == ["legacy-b.md", "legacy-a.md", "current.md"]
    assert not warnings
    assert resolved_paths == [str(local_repo)]

    resolved_paths.clear()
    path_entries, warnings = operations.entries_with_warnings({"target_repo": str(local_repo)})
    assert [item["filename"] for item in path_entries] == ["legacy-b.md", "legacy-a.md", "current.md"]
    assert not warnings
    assert resolved_paths == [str(local_repo)]

    resolved_paths.clear()
    assert operations.target_repos() == [
        str(tmp_path / "missing"),
        "example/repo",
        "github.com/example/other",
        "github.com/example/repo",
    ]
    assert resolved_paths == [str(local_repo)]

    unresolved_entries, warnings = operations.entries_with_warnings({"target_repo": "example/repo"})
    assert [item["filename"] for item in unresolved_entries] == ["unresolved.md"]
    assert not warnings

    unfiltered_entries, warnings = operations.entries_with_warnings({})
    assert not warnings
    assert next(item for item in unfiltered_entries if item["filename"] == "missing.md")["target_repo"] == str(
        tmp_path / "missing"
    )


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
  error: elements['global-error-message'].textContent
}));
"""
    )
    assert result == {
        "listUrls": ["/atk/api/entries?type=all&status=adopted&answered=all&page=1"],
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
        "listUrls": ["/atk/api/entries?type=all&status=active&answered=all&page=1"],
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


def test_assets_search_fallback_obeys_boundaries_and_keeps_filters() -> None:
    """検索結果0件時の補助検索を境界値、通常一致、検索語なしで検証する。"""
    result = _run_node_ui(
        """
const fallbackNotice =
  '状態などの条件では一致しなかったため、検索欄の条件だけで見つかった項目を表示しています。' +
  'フィルターの選択値は変更していません。';
const initialWarnings = [{filename: 'initial.md', reason: '初回警告'}];
const runCase = async (token, count) => {
  fetchCalls.length = 0;
  elements['search-input'].value = token;
  elements['kind-filter'].value = 'all';
  elements['state-filter'].value = 'active';
  elements['answer-filter'].value = 'all';
  elements['target-filter'].value = '';
  elements['source-filter'].value = '';
  const fallbackEntries = Array.from({length: count}, (_, index) => ({
    kind: 'awi', state: 'adopted', filename: `${token}-${index}.md`, summary: token
  }));
  fetchHandler = async url => {
    if (url.includes('status=active')) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: initialWarnings})};
    }
    if (url === `/atk/api/entries?q=${token}&page=1`) {
      return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: fallbackEntries, warnings: []})};
    }
    throw new Error('想定外のURL: ' + url);
  };
  await loadEntries({announce: true});
  return {
    count,
    rows: entries.map(entry => entry.filename),
    notice: elements['list-fallback-notice'].textContent,
    noticeHidden: elements['list-fallback-notice'].hidden,
    status: elements['result-status'].textContent,
    warning: elements['list-warning'].textContent,
    filters: {
      kind: elements['kind-filter'].value,
      state: elements['state-filter'].value,
      answer: elements['answer-filter'].value,
      target: elements['target-filter'].value,
      source: elements['source-filter'].value
    },
    urls: fetchCalls.map(call => call.url)
  };
};
const one = await runCase('one', 1);
const five = await runCase('five', 5);
const none = await runCase('none', 0);
const six = await runCase('six', 6);

fetchCalls.length = 0;
elements['search-input'].value = 'normal';
fetchHandler = async url => {
  if (!url.includes('/api/entries?')) throw new Error('想定外のURL: ' + url);
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({
    entries: [{kind: 'awi', state: 'inbox', filename: 'normal.md', summary: 'normal'}], warnings: []
  })};
};
await loadEntries({announce: true});
const normal = {
  rows: entries.map(entry => entry.filename),
  noticeHidden: elements['list-fallback-notice'].hidden,
  urls: fetchCalls.map(call => call.url)
};

fetchCalls.length = 0;
elements['search-input'].value = '';
fetchHandler = async url => {
  if (!url.includes('/api/entries?')) throw new Error('想定外のURL: ' + url);
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await loadEntries({announce: true});
const emptySearch = {
  rows: entries.map(entry => entry.filename),
  noticeHidden: elements['list-fallback-notice'].hidden,
  urls: fetchCalls.map(call => call.url)
};

fetchCalls.length = 0;
elements['search-input'].value = 'all-filters-only';
elements['kind-filter'].value = 'all';
elements['state-filter'].value = 'all';
elements['answer-filter'].value = 'all';
elements['target-filter'].value = '';
elements['source-filter'].value = '';
fetchHandler = async url => {
  if (url !== '/atk/api/entries?type=all&status=all&answered=all&q=all-filters-only&page=1') {
    throw new Error('想定外のURL: ' + url);
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await loadEntries({announce: true});
const allFilters = {
  rows: entries.map(entry => entry.filename),
  noticeHidden: elements['list-fallback-notice'].hidden,
  urls: fetchCalls.map(call => call.url)
};
process.stdout.write(JSON.stringify({one, five, none, six, normal, emptySearch, allFilters, fallbackNotice}));
"""
    )
    expected_notice = (
        "状態などの条件では一致しなかったため、検索欄の条件だけで見つかった項目を表示しています。"
        "フィルターの選択値は変更していません。"
    )
    assert result["one"]["rows"] == ["one-0.md"]
    assert result["five"]["rows"] == [f"five-{index}.md" for index in range(5)]
    for name in ("one", "five"):
        assert result[name]["notice"] == expected_notice
        assert result[name]["noticeHidden"] is False
        assert result[name]["warning"] == ""
        assert result[name]["urls"] == [
            f"/atk/api/entries?type=all&status=active&answered=all&q={name}&page=1",
            f"/atk/api/entries?q={name}&page=1",
        ]
        assert result[name]["filters"] == {
            "kind": "all",
            "state": "active",
            "answer": "all",
            "target": "",
            "source": "",
        }
    for name in ("none", "six"):
        assert result[name]["rows"] == []
        assert result[name]["notice"] == ""
        assert result[name]["noticeHidden"] is True
        assert result[name]["warning"] == "一覧から除外したファイル: initial.md（初回警告）"
        assert result[name]["status"] == "一致する項目はありません"
        assert result[name]["urls"] == [
            f"/atk/api/entries?type=all&status=active&answered=all&q={name}&page=1",
            f"/atk/api/entries?q={name}&page=1",
        ]
    assert result["normal"] == {
        "rows": ["normal.md"],
        "noticeHidden": True,
        "urls": ["/atk/api/entries?type=all&status=active&answered=all&q=normal&page=1"],
    }
    assert result["emptySearch"] == {
        "rows": [],
        "noticeHidden": True,
        "urls": ["/atk/api/entries?type=all&status=active&answered=all&page=1"],
    }
    assert result["allFilters"] == {
        "rows": [],
        "noticeHidden": True,
        "urls": ["/atk/api/entries?type=all&status=all&answered=all&q=all-filters-only&page=1"],
    }
    assert result["fallbackNotice"] == expected_notice


def test_assets_search_fallback_failure_keeps_initial_empty_result() -> None:
    """補助検索が失敗しても初回の空一覧とエラー表示を維持する。"""
    result = _run_node_ui(
        """
bindEvents();
elements['search-input'].value = '失敗する検索';
fetchHandler = async url => {
  if (url.includes('status=active')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
  }
  return {ok: false, status: 503, statusText: 'Unavailable', json: async () => ({error: '補助検索に失敗'})};
};
await loadEntries({announce: true});
const shown = {
  message: elements['global-error-message'].textContent,
  hidden: elements['global-error'].hidden
};
elements['global-error-close-button'].focus();
elements['global-error-close-button'].listeners.click();
process.stdout.write(JSON.stringify({
  rows: entries.map(entry => entry.filename),
  notice: elements['list-fallback-notice'].textContent,
  status: elements['result-status'].textContent,
  shown,
  cleared: {
    message: elements['global-error-message'].textContent,
    hidden: elements['global-error'].hidden,
    focused
  }
}));
"""
    )
    assert result == {
        "rows": [],
        "notice": "",
        "status": "一致する項目はありません",
        "shown": {"message": "補助検索に失敗", "hidden": False},
        "cleared": {"message": "", "hidden": True, "focused": "refresh-button"},
    }


def test_assets_discard_stale_search_fallback_response() -> None:
    """後発の一覧要求が完了した後に補助応答が到着しても表示を上書きしない。"""
    result = _run_node_ui(
        """
let resolveFallback;
let fallbackStarted;
const fallbackReady = new Promise(resolve => { fallbackStarted = resolve; });
elements['search-input'].value = 'old';
fetchHandler = async url => {
  if (url === '/atk/api/entries?q=old&page=1') {
    fallbackStarted();
    return new Promise(resolve => { resolveFallback = resolve; });
  }
  if (url.includes('q=old')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
  }
  if (url.includes('q=new')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({
      entries: [{kind: 'awi', state: 'inbox', filename: 'new.md', summary: 'new'}], warnings: []
    })};
  }
  throw new Error('想定外のURL: ' + url);
};
const oldRequest = loadEntries({announce: true});
await fallbackReady;
elements['search-input'].value = 'new';
const newRequest = loadEntries({announce: true});
await newRequest;
resolveFallback({
  ok: true, status: 200, statusText: 'OK',
  json: async () => ({entries: [{kind: 'awi', state: 'adopted', filename: 'old.md', summary: 'old'}], warnings: []})
});
await oldRequest;
process.stdout.write(JSON.stringify({
  rows: entries.map(entry => entry.filename),
  state: entries[0]?.state,
  notice: elements['list-fallback-notice'].textContent,
  status: elements['result-status'].textContent
}));
"""
    )
    assert result == {
        "rows": ["new.md"],
        "state": "inbox",
        "notice": "",
        "status": "1件を表示",
    }


def test_assets_discard_stale_search_fallback_error_without_overwriting_global_error() -> None:
    """失効した補助検索のエラーが後発要求のglobal-errorを上書きしない。"""
    result = _run_node_ui(
        """
let rejectFallback;
let fallbackStarted;
const fallbackReady = new Promise(resolve => { fallbackStarted = resolve; });
elements['search-input'].value = 'old';
fetchHandler = async url => {
  if (url === '/atk/api/entries?q=old&page=1') {
    fallbackStarted();
    return new Promise((_resolve, reject) => { rejectFallback = reject; });
  }
  if (url.includes('q=old')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
  }
  if (url.includes('q=new')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({
      entries: [{kind: 'awi', state: 'inbox', filename: 'new.md', summary: 'new'}], warnings: []
    })};
  }
  throw new Error('想定外のURL: ' + url);
};
const oldRequest = loadEntries({announce: true});
await fallbackReady;
elements['search-input'].value = 'new';
const newRequest = loadEntries({announce: true});
await newRequest;
elements['global-error'].textContent = '後発要求のエラー';
rejectFallback(new Error('失効した補助検索エラー'));
await oldRequest;
process.stdout.write(JSON.stringify({
  rows: entries.map(entry => entry.filename),
  state: entries[0]?.state,
  notice: elements['list-fallback-notice'].textContent,
  status: elements['result-status'].textContent,
  error: elements['global-error'].textContent
}));
"""
    )
    assert result == {
        "rows": ["new.md"],
        "state": "inbox",
        "notice": "",
        "status": "1件を表示",
        "error": "後発要求のエラー",
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
        entries: [{kind: 'awi', state: 'inbox', filename: `${kind}.md`, summary: kind}],
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
            "/atk/api/entries?type=uwi&status=all&answered=all",
            "/atk/api/entries?type=awi&status=active&answered=all&page=1",
            "/atk/api/entries?type=awi&status=active&answered=all&page=1",
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
        "lastListUrl": ("/atk/api/entries?type=all&status=adopted&answered=all&target_repo=adopted%2Frepo&page=1"),
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
  error: elements['global-error-message'].textContent
}));
"""
    )
    assert result == {
        "candidates": ["", "active/repo"],
        "error": "",
    }


_BATCH_TEXT = (
    "# awi\n## target_repo: github.com/example/foo\n"
    "### keep.md [inbox]\n---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文\n\n"
)


def _patch_batch_repo_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    """一括取り込み経路のロック・remote同期・commitを無効化する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(serve_app.awi_batch, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.awi_batch, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_batch, "_commit_and_push", lambda *_args, **_kwargs: None)


@pytest.mark.asyncio
async def test_add_api_accepts_omitted_target_repo_with_frontmatter(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本文frontmatterにtarget_repoがあれば対象リポジトリ指定を省略できる。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(common, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(common, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_add, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.awi_add, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.awi_add, "_commit_and_push", lambda *_args, **_kwargs: None)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post(
        "/api/entries",
        json={"type": "awi", "messages": ["---\ntarget_repo: github.com/Example/Repo\n---\n\n本文"]},
    )

    assert response.status_code == 201
    body = await response.get_json()
    content = (tmp_path / "inbox" / body["filenames"][0]).read_text(encoding="utf-8")
    assert "target_repo: github.com/example/repo" in content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["---\ntarget_repo:\n- github.com/example/repo\n---\n\n本文", "本文だけ"],
)
async def test_add_api_rejects_omitted_target_repo_without_frontmatter_value(
    tmp_path: pathlib.Path,
    message: str,
) -> None:
    """frontmatterのtarget_repoが欠落・非文字列の場合は400で拒否する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post("/api/entries", json={"type": "awi", "messages": [message]})

    assert response.status_code == 400
    assert "target_repo" in (await response.get_json())["error"]


@pytest.mark.asyncio
async def test_batch_api_imports_entries(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """一括登録APIが保存名・対応・警告を返し、原文保持で保存する。"""
    _patch_batch_repo_operations(monkeypatch)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post("/api/entries/batch", json={"text": _BATCH_TEXT})

    assert response.status_code == 201
    assert await response.get_json() == {
        "filenames": ["keep.md"],
        "mapping": {"keep.md": "keep.md"},
        "warnings": [],
    }
    assert (tmp_path / "inbox" / "keep.md").read_text(encoding="utf-8") == (
        "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文\n"
    )


@pytest.mark.asyncio
async def test_batch_api_imports_crlf_text(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CRLF改行の一括登録テキストも取り込み、保存内容の改行をLFへ正規化する。"""
    _patch_batch_repo_operations(monkeypatch)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post("/api/entries/batch", json={"text": _BATCH_TEXT.replace("\n", "\r\n")})

    assert response.status_code == 201
    assert (await response.get_json())["filenames"] == ["keep.md"]
    assert (tmp_path / "inbox" / "keep.md").read_text(encoding="utf-8") == (
        "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文\n"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{}, {"text": ""}, {"text": "  "}, {"text": 1}, {"text": "ただの本文\n"}, {"text": _BATCH_TEXT, "type": "awi"}],
)
async def test_batch_api_rejects_invalid_inputs(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    """必須キー・型・空文字・未知キー・show形式外の入力を400で拒否する。"""
    _patch_batch_repo_operations(monkeypatch)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post("/api/entries/batch", json=payload)

    assert response.status_code == 400
    assert not (tmp_path / "inbox").exists() or not list((tmp_path / "inbox").iterdir())


def test_assets_offer_batch_creation_without_required_target_repo() -> None:
    """新規追加ダイアログが一括登録種別を持ち、対象リポジトリの必須指定を外す。"""
    assert '<option value="batch">一括登録（show形式）</option>' in assets.HTML
    assert 'id="create-repo-fields"' in assets.HTML
    assert "対象リポジトリ（frontmatterに無い場合は必須）" in assets.HTML
    assert '<input id="create-target" name="target_repo" list="repo-options" aria-describedby="create-target-error">' in (
        assets.HTML
    )
    assert "'/api/entries/batch'" in assets.JS
    assert "対象リポジトリを入力してください" not in assets.JS


def test_assets_offer_every_queue_state_filter() -> None:
    """状態フィルターがキューの全状態を個別に選択できる。"""
    for state_name in common.WI_STATES:
        assert f'<option value="{state_name}">{state_name}</option>' in assets.HTML


def test_assets_state_sets_match_python_states() -> None:
    """フロントエンドが持つ状態集合をPython側の保存状態と一致させる。"""
    labels = re.search(r"const STATE_LABELS = \{(.*?)\n\};", assets.JS, re.DOTALL)
    assert labels is not None
    assert set(re.findall(r"(\w+):", labels.group(1))) == set(common.WI_STATES)

    deletable = re.search(r"const DELETABLE_STATES = new Set\(\[(.*?)\]\);", assets.JS)
    assert deletable is not None
    assert set(re.findall(r"'(\w+)'", deletable.group(1))) == set(common.WI_STATES)

    processable = re.search(r"const PROCESSABLE_STATES = new Set\(\[(.*?)\]\);", assets.JS)
    assert processable is not None
    assert set(re.findall(r"'(\w+)'", processable.group(1))) == set(common.WI_PROCESSABLE_STATES)


def test_batch_creation_sends_raw_text_and_hides_frontmatter_driven_fields() -> None:
    """一括登録では生テキストを送り、対象リポジトリ欄と投入元欄を隠す。"""
    result = _run_node_ui(
        """
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-kind'].value = 'batch';
updateCreateFields();
const hiddenRepoFields = elements['create-repo-fields'].hidden;
const contentLabel = elements['create-content-label'].textContent;
elements['create-content'].value = '  show形式テキスト  ';
fetchHandler = async (url) => {
  if (url.endsWith('/api/entries/batch')) {
    return {
      ok: true, status: 201, statusText: 'Created',
      json: async () => ({filenames: ['new.md'], mapping: {'old.md': 'new.md'}, warnings: ['依存先が不在']})
    };
  }
  if (url.endsWith('/api/repos?status=active')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await createEntry({preventDefault() {}});
const call = fetchCalls.find(item => item.url.endsWith('/api/entries/batch'));
process.stdout.write(JSON.stringify({
  hiddenRepoFields,
  contentLabel,
  body: JSON.parse(call.options.body),
  toast: elements['toast'].textContent,
  detailOpen: elements['detail-dialog'].open
}));
"""
    )
    assert result == {
        "hiddenRepoFields": True,
        "contentLabel": "show形式テキスト（必須）",
        "body": {"text": "  show形式テキスト  "},
        "toast": "1件を取り込みました。改名: old.md -> new.md 警告: 依存先が不在",
        "detailOpen": False,
    }


def test_normal_creation_omits_empty_target_repo_from_payload() -> None:
    """対象リポジトリ欄が空の通常追加では、target_repoを送らずサーバー検証へ委ねる。"""
    result = _run_node_ui(
        """
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-content'].value = '---\\ntarget_repo: example/repo\\n---\\n\\n本文';
fetchHandler = async (url) => {
  if (url.endsWith('/api/entries')) {
    return {ok: true, status: 201, statusText: 'Created', json: async () => ({filenames: ['new.md']})};
  }
  if (url.endsWith('/api/repos?status=active')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await createEntry({preventDefault() {}});
const call = fetchCalls.find(item => item.url.endsWith('/api/entries'));
process.stdout.write(JSON.stringify({body: JSON.parse(call.options.body)}));
"""
    )
    assert result == {"body": {"type": "awi", "messages": ["---\ntarget_repo: example/repo\n---\n\n本文"]}}


def _session_review_awi(
    body: str,
    *,
    entry_type: str = "awi",
    source: str | None = "session-review",
) -> str:
    """ユーザーコメント操作テスト用のawi本文を組み立てる。"""
    source_line = f"source: {source}\n" if source is not None else ""
    return f"---\ntype: {entry_type}\ntarget_repo: example/repo\n{source_line}---\n\n{body}"


def _patch_comment_edit_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """ユーザーコメントAPIテストでGit同期だけを無効化する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    for module in (common, awi_repo, serve_app.awi_mutations):
        monkeypatch.setattr(module, "_repo_lock", lock, raising=False)
        monkeypatch.setattr(module, "_pull", lambda _path: None, raising=False)
        monkeypatch.setattr(module, "_commit_and_push", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(module, "_push_pending_commits", lambda _path: None, raising=False)


def test_user_comment_pure_update_preserves_frontmatter_and_previous_body() -> None:
    """予約節の置換でfrontmatterと通常本文を保持し、再抽出できる。"""
    original = _session_review_awi("通常本文\n\n## 実装メモ\n\n既存の見出し本文\n\n## ユーザーコメント\n\n旧コメント\n")

    updated = user_comment.update_user_comment(original, "\n\n新しいコメント\n\n2行目\n\n")

    assert updated == _session_review_awi(
        "通常本文\n\n## 実装メモ\n\n既存の見出し本文\n\n## ユーザーコメント\n\n新しいコメント\n\n2行目\n"
    )
    assert user_comment.extract_user_comment(updated) == "新しいコメント\n\n2行目"


def test_user_comment_pure_update_ignores_fenced_heading_and_appends_once() -> None:
    """コードフェンス内の同名文字列を見出しと誤認せず、予約節を1件だけ追記する。"""
    original = _session_review_awi("```markdown\n## ユーザーコメント\n```\n\n通常本文\n")

    updated = user_comment.update_user_comment(original, "コメント")

    assert updated.count("## ユーザーコメント") == 2
    assert updated.endswith("## ユーザーコメント\n\nコメント\n")
    assert user_comment.extract_user_comment(updated) == "コメント"


@pytest.mark.parametrize(
    ("body", "comment", "message"),
    [
        (
            "本文\n\n## ユーザーコメント\n\nコメント\n\n## 後続見出し\n\n後続本文\n",
            "更新",
            "ユーザーコメント節の後ろに別のH2見出しがあります",
        ),
        (
            "本文\n\n## ユーザーコメント\n\n一つ目\n\n## ユーザーコメント\n\n二つ目\n",
            "更新",
            "ユーザーコメント節が複数あります",
        ),
        ("本文\n", "## コメント内見出し", "ユーザーコメントにコードフェンス外のH2見出しを含められません"),
        ("本文\n", "```markdown\n## コメント内見出し\n```", ""),
        ("本文\n", " \n\n", "ユーザーコメントは空にできません"),
    ],
)
def test_user_comment_pure_update_rejects_invalid_structure_without_mutation(
    body: str,
    comment: str,
    message: str,
) -> None:
    """不正な節・コメントは判別可能なエラーとなり、保存対象を変更しない。"""
    original = _session_review_awi(body)

    if message:
        with pytest.raises(user_comment.UserCommentError, match=re.escape(message)):
            user_comment.update_user_comment(original, comment)
        assert original == _session_review_awi(body)
    else:
        updated = user_comment.update_user_comment(original, comment)
        assert user_comment.extract_user_comment(updated) == comment


@pytest.mark.asyncio
async def test_user_comment_api_appends_and_replaces_inbox_and_hold_session_review_awi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """専用APIがinboxとholdの対象本文へ追記し、同じ節だけを置換する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = _session_review_awi("通常本文\n")
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    detail_response = await client.get("/api/entries/inbox/awi.md")
    detail = await detail_response.get_json()
    assert detail_response.status_code == 200
    assert detail["entry"]["user_comment"] is None
    assert detail["entry"]["user_comment_editable"] is True

    first = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "最初のコメント",
            "expected_content": original,
        },
    )
    assert first.status_code == 200
    assert await first.get_json() == {"changed": True}
    after_first = path.read_text(encoding="utf-8")
    assert after_first == _session_review_awi("通常本文\n\n## ユーザーコメント\n\n最初のコメント\n")

    second = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "置換後のコメント",
            "expected_content": after_first,
        },
    )
    assert second.status_code == 200
    assert await second.get_json() == {"changed": True}
    after_second = path.read_text(encoding="utf-8")
    assert after_second == _session_review_awi("通常本文\n\n## ユーザーコメント\n\n置換後のコメント\n")

    final_detail = await (await client.get("/api/entries/inbox/awi.md")).get_json()
    assert final_detail["entry"]["user_comment"] == "置換後のコメント"

    hold = tmp_path / "hold"
    hold.mkdir()
    held_path = hold / "held.md"
    held_path.write_text(original, encoding="utf-8")
    held_detail = await (await client.get("/api/entries/hold/held.md")).get_json()
    assert held_detail["entry"]["user_comment_editable"] is True
    held = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "hold",
            "filename": "held.md",
            "comment": "保留中のコメント",
            "expected_content": original,
        },
    )
    assert held.status_code == 200
    assert user_comment.extract_user_comment(held_path.read_text(encoding="utf-8")) == "保留中のコメント"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "editable"),
    [(None, False), ("human", True), ("alert-monitor", True), ("plan", True)],
)
async def test_user_comment_api_matches_agent_source_classification(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str | None,
    editable: bool,
) -> None:
    """詳細表示と保存APIが同じエージェント由来判定を使う。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = _session_review_awi("本文\n", source=source)
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    detail = await (await client.get("/api/entries/inbox/awi.md")).get_json()
    assert detail["entry"]["user_comment_editable"] is editable
    response = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "追加コメント",
            "expected_content": original,
        },
    )
    assert response.status_code == (200 if editable else 400)
    if editable:
        assert user_comment.extract_user_comment(path.read_text(encoding="utf-8")) == "追加コメント"
    else:
        assert path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_user_comment_api_rejects_non_inbox_states_and_uwi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """processing・終端状態及びUWIには操作を提供しない。"""
    _patch_comment_edit_dependencies(monkeypatch)
    contents = {
        "processing": _session_review_awi("processing本文\n"),
        "adopted": _session_review_awi("adopted本文\n"),
        "rejected": _session_review_awi("rejected本文\n"),
        "uwi": _session_review_awi("UWI本文\n", entry_type="uwi"),
    }
    for state_name, content in contents.items():
        directory = tmp_path / ("inbox" if state_name == "uwi" else state_name)
        directory.mkdir(exist_ok=True)
        path = directory / f"{state_name}.md"
        path.write_text(content, encoding="utf-8")

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()
    for state_name, content in contents.items():
        filename = f"{state_name}.md"
        response = await client.post(
            "/api/entries/user-comment",
            json={
                "state": "inbox" if state_name == "uwi" else state_name,
                "filename": filename,
                "comment": "追加コメント",
                "expected_content": content,
            },
        )
        assert response.status_code == 400
        actual_path = tmp_path / ("inbox" if state_name == "uwi" else state_name) / filename
        assert actual_path.read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_remove_api_accepts_terminal_states_and_preserves_protected_states(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """終端状態とholdは削除し、processingの保護を維持する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    content = _session_review_awi("本文\n")
    for state_name in ("adopted", "rejected", "hold", "processing"):
        directory = tmp_path / state_name
        directory.mkdir()
        (directory / f"{state_name}.md").write_text(content, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    for state_name in ("adopted", "rejected", "hold"):
        response = await client.post(
            "/api/entries/remove",
            json={
                "filenames": [f"{state_name}.md"],
                "state": state_name,
                "expected_content": content,
                "force": False,
            },
        )
        assert response.status_code == 200
        assert not (tmp_path / state_name / f"{state_name}.md").exists()

    for state_name in ("processing",):
        response = await client.post(
            "/api/entries/remove",
            json={
                "filenames": [f"{state_name}.md"],
                "state": state_name,
                "expected_content": content,
                "force": False,
            },
        )
        assert response.status_code == 400
        assert (tmp_path / state_name / f"{state_name}.md").is_file()


@pytest.mark.asyncio
async def test_user_comment_api_returns_edit_conflict_without_losing_latest_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """期待本文と現行本文が異なる場合は409として最新本文を保持する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = _session_review_awi("取得時本文\n")
    latest = _session_review_awi("外部更新後の本文\n")
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    detail = await (await app.test_client().get("/api/entries/inbox/awi.md")).get_json()
    path.write_text(latest, encoding="utf-8")

    response = await app.test_client().post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "古い基準のコメント",
            "expected_content": detail["entry"]["content"],
        },
    )

    assert response.status_code == 409
    assert await response.get_json() == {
        "code": "edit_conflict",
        "error": "編集中に他プロセスが対象を変更しました",
    }
    assert path.read_text(encoding="utf-8") == latest


@pytest.mark.asyncio
async def test_user_comment_api_requires_expected_content_and_rejects_comment_h2(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """expected_contentの省略・空値とコメント内H2を保存前に拒否する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = _session_review_awi("本文\n")
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    for payload in (
        {"state": "inbox", "filename": "awi.md", "comment": "コメント"},
        {
            "state": "inbox",
            "filename": "awi.md",
            "comment": "コメント",
            "expected_content": "",
        },
        {
            "state": "inbox",
            "filename": "awi.md",
            "comment": "## 禁止見出し",
            "expected_content": original,
        },
    ):
        response = await client.post("/api/entries/user-comment", json=payload)
        assert response.status_code == 400
        assert path.read_text(encoding="utf-8") == original
