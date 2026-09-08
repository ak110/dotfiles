# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
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

import filelock
import pytest
import watchdog.events

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import assets, config, state
from agent_toolkit._atk.serve import cli as serve
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.wi import common, user_comment
from agent_toolkit._atk.wi import repo as awi_repo

# UI検証で起動する`node`は、CIの実行環境ではmiseのshimとして提供され、版と信頼設定の解決に
# 実行環境のホーム・設定ディレクトリを参照する。conftestの`_isolated_home`が差し替えた環境を
# そのまま渡すとこの解決が失敗するため、取り込み時点の環境変数を控えてNodeへ渡す。
# 同じ目的のconftestの`host_environ` fixtureは使わない。`node`を起動する`_run_node_ui`は
# module levelのヘルパーであり、fixtureを受け取るには全呼び出し元のテストへ引数を追加する必要がある。
_HOST_ENVIRON = dict(os.environ)


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
  'create-target-error', 'uwi-fields', 'create-scope',
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
    elements['create-target'], elements['create-scope'],
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


def _write_detail_entry(tmp_path: pathlib.Path, text: str) -> None:
    """詳細表示テスト用の入力ファイルを作成する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "entry.md").write_text(text, encoding="utf-8")


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


__all__ = [
    "_BATCH_TEXT",
    "_FakeTimer",
    "_HOST_ENVIRON",
    "_patch_batch_repo_operations",
    "_patch_comment_edit_dependencies",
    "_recorder",
    "_run_node_ui",
    "_session_review_awi",
    "_stub_state",
    "_three_screen_app",
    "_write_detail_entry",
    "_write_repo_entry",
]
