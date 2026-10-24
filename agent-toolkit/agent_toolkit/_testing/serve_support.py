"""`_atk/serve/`のテストが共有する、WI画面のUI実行、3画面のアプリの組み立てと擬似の状態・操作。"""

# pylint: disable=protected-access

import contextlib
import html.parser
import json
import pathlib
import re
import subprocess
import threading
import types
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import assets, config, state, wi_operations
from agent_toolkit._atk.serve import cli as serve
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing import isolation, wi_entry_files

# UI検証で起動する`node`は、CIの実行環境ではmiseのshimとして提供され、版と信頼設定の解決に
# 実行環境のホーム・設定ディレクトリを参照する。conftestが適用する隔離（`agent_toolkit._testing.isolation`）が差し替えた環境を
# そのまま渡すとこの解決が失敗するため、Nodeを起動する時点で隔離前の値を組み立てて渡す。
_MODULE_IMPORT_RE = re.compile(r"^import\s*\{[^}]*\}\s*from\s*\"[^\"]+\";\n", re.MULTILINE)
_MODULE_EXPORT_RE = re.compile(r"^export (?=(?:async )?function |const |let |class )", re.MULTILINE)


def _module_body(source: str) -> str:
    """ESモジュールの本文から`import`文と`export`の修飾を除き、同じスコープで評価できる本文にする。"""
    body = _MODULE_IMPORT_RE.sub("", source)
    assert not re.search(r"^import ", body, re.MULTILINE), "解釈できないimport文が残っている"
    return _MODULE_EXPORT_RE.sub("", body)


class _HtmlDom(html.parser.HTMLParser):
    """本番HTMLの要素、属性、親子関係をNodeの擬似DOMへ渡す。"""

    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[dict[str, typing.Any]] = []
        self._stack: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        index = len(self.nodes)
        self.nodes.append({"tag": tag, "attrs": dict(attrs), "children": [], "text": ""})
        if self._stack:
            self.nodes[self._stack[-1]]["children"].append(index)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self._stack.append(index)

    def handle_endtag(self, tag: str) -> None:
        for position in range(len(self._stack) - 1, -1, -1):
            if self.nodes[self._stack[position]]["tag"] == tag:
                del self._stack[position:]
                break

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._stack and self.nodes[self._stack[-1]]["tag"] == "option":
            self.nodes[self._stack[-1]]["text"] += data


def _run_node_ui(scenario: str, *, environment: typing.Mapping[str, str] | None = None) -> dict[str, typing.Any]:
    """WI画面のUI関数を最小DOM上で実行し、シナリオのJSON結果を返す。

    WI画面はESモジュールであり、モジュールの外から内部の関数と変数を参照できない。
    共通モジュールとWI画面の本文を`import`と`export`を除いて1つの関数スコープへ並べ、
    シナリオも同じスコープへ置いて検証対象の関数を直接呼べるようにする。
    画面の登録先の`shell.js`は文書全体のナビゲーションを扱うため読み込まず、登録関数だけを代替する。
    `environment`を指定した呼び出し元は、隔離を避けるために子のNodeへ渡す環境を明示する。
    """
    executable = (
        "(() => {\n"
        + _module_body(assets.SCRIPTS["common.js"])
        + "\nfunction registerScreen() {}\n"
        + _module_body(assets.SCRIPTS["wi.js"])
        + "\n(async () => {\n"
        + scenario
        + "\n})().catch(error => { process.stderr.write(String(error.stack || error)); process.exitCode = 1; });\n"
        + "})();\n"
    )
    dom = _HtmlDom()
    dom.feed(assets.HTML)
    script = f"""
class Element {{
  constructor(id = '', tagName = 'DIV') {{
    this.id = id;
    this.tagName = tagName;
    this.children = [];
    this.dataset = {{}};
    this.attributes = {{}};
    this.listeners = {{}};
    this.parentNode = null;
    this.ownText = '';
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
  // DOMと同じく、子を持つ要素の`textContent`は子孫の文字列を連結し、代入は子を取り除く。
  get textContent() {{ return this.ownText + this.children.map(child => child.textContent).join(''); }}
  set textContent(value) {{
    this.replaceChildren();
    this.ownText = String(value);
  }}
  get firstChild() {{ return this.children[0] ?? null; }}
  get nextSibling() {{
    const siblings = this.parentNode?.children || [];
    return siblings[siblings.indexOf(this) + 1] ?? null;
  }}
  adopt(child) {{
    if (typeof child.remove === 'function') child.remove();
    child.parentNode = this;
    if (typeof child.setConnected === 'function') child.setConnected(this.isConnected);
  }}
  insertBefore(child, reference) {{
    this.adopt(child);
    const index = reference ? this.children.indexOf(reference) : -1;
    if (index < 0) this.children.push(child);
    else this.children.splice(index, 0, child);
  }}
  remove() {{
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter(child => child !== this);
    this.parentNode = null;
    this.setConnected(false);
  }}
  setConnected(value) {{
    this.isConnected = value;
    this.children.forEach(child => {{ if (typeof child.setConnected === 'function') child.setConnected(value); }});
  }}
  append(...children) {{
    children.forEach(child => {{
      this.adopt(child);
      this.children.push(child);
    }});
  }}
  replaceChildren(...children) {{
    this.children.forEach(child => {{
      child.parentNode = null;
      if (typeof child.setConnected === 'function') child.setConnected(false);
    }});
    this.children = [];
    this.append(...children);
  }}
  contains(node) {{ return this === node || this.children.some(child => child.contains?.(node)); }}
  closest() {{ return null; }}
  setAttribute(name, value) {{ this.attributes[name] = String(value); }}
  getAttribute(name) {{ return this.attributes[name] ?? null; }}
  removeAttribute(name) {{ delete this.attributes[name]; }}
  addEventListener(name, handler) {{ this.listeners[name] = handler; }}
  querySelectorAll(selector) {{
    const tags = new Set(selector.split(',').map(value => value.trim().toUpperCase()));
    return this.children.flatMap(child => [
      ...(tags.has(child.tagName) ? [child] : []), ...child.querySelectorAll(selector)
    ]);
  }}
  showModal() {{ this.open = true; }}
  close() {{ this.open = false; }}
  focus() {{
    if (this.disabled) return;
    document.activeElement = this;
    globalThis.focused = this.dataset.key || this.id;
    document.listeners.focusin?.({{target: this}});
  }}
}}
const structure = {json.dumps(dom.nodes)};
const nodes = structure.map(node => {{
  const element = new Element(node.attrs.id || '', node.tag.toUpperCase());
  Object.entries(node.attrs).forEach(([name, value]) => element.setAttribute(name, value ?? ''));
  element.className = node.attrs.class || '';
  element.type = node.attrs.type || '';
  element.value = node.attrs.value || '';
  element.hidden = Object.hasOwn(node.attrs, 'hidden');
  element.disabled = Object.hasOwn(node.attrs, 'disabled');
  element.checked = Object.hasOwn(node.attrs, 'checked');
  element.ownText = node.text;
  Object.entries(node.attrs).filter(([name]) => name.startsWith('data-')).forEach(([name, value]) => {{
    element.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value ?? '';
  }});
  return element;
}});
structure.forEach((node, index) => nodes[index].append(...node.children.map(child => nodes[child])));
nodes.filter(node => node.tagName === 'SELECT').forEach(select => {{
  const options = select.querySelectorAll('option');
  const chosen = options.find(option => option.getAttribute('selected') !== null) || options[0];
  select.value = chosen ? chosen.getAttribute('value') ?? chosen.textContent : '';
}});
const elements = Object.fromEntries(nodes.filter(node => node.id).map(node => [node.id, node]));
elements['toast'] = elements['operation-notice-message'];
const appHeader = nodes.find(node => node.classList.contains('app-header'));
globalThis.controlGroups = Object.fromEntries(
  Object.entries(elements).map(([id, element]) => [id, element.querySelectorAll('input, select, textarea, button')])
);
globalThis.controlGroups['app-header'] = appHeader.querySelectorAll('input, select, textarea, button');
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
elements['serve-bootstrap'] = new Element('serve-bootstrap', 'SCRIPT');
elements['serve-bootstrap'].textContent = JSON.stringify({{
  base_path: '/atk', stall_ms: 45000, host_name: 'node-test', wi_root: '/notes'
}});
globalThis.window = globalThis;
globalThis.confirm = () => true;
globalThis.setTimeout = () => 1;
globalThis.clearTimeout = () => undefined;
globalThis.setInterval = () => 1;
globalThis.clearInterval = () => undefined;
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
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        env=isolation.build_host_environ() if environment is None else environment,
    )
    assert completed.returncode == 0, completed.stderr
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


_STALE_FALLBACK_SEARCH_SCRIPT = """
@DECLARATION@
let fallbackStarted;
const fallbackReady = new Promise(resolve => { fallbackStarted = resolve; });
elements['search-input'].value = 'old';
fetchHandler = async url => {
  if (url === '/atk/api/entries?q=old&page=1') {
    fallbackStarted();
    return @PENDING@;
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
"""


def stale_fallback_search_script(declaration: str, pending_promise: str) -> str:
    """古い検索語の補助検索を保留させたまま、新しい検索語の一覧要求を完了させるNodeの画面スクリプトを返す。

    `declaration`は保留を解く関数を受ける変数の宣言、`pending_promise`は補助検索が返す保留中のPromiseの式とする。
    呼び出し側は、返したスクリプトの後ろへ保留の解き方と検証を続ける。
    """
    return _STALE_FALLBACK_SEARCH_SCRIPT.replace("@DECLARATION@", declaration).replace("@PENDING@", pending_promise)


UNANSWERED_UWI_TEXT = (
    "---\nstate: inbox\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
    "<!-- ユーザーはこの行以降に回答を追記する -->\n"
)
"""回答欄が空のUWIの本文。"""

AWI_TEXT = "---\nstate: inbox\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n"
"""最小のAWIの本文。"""


def make_inbox_and_processing(private_notes: pathlib.Path, *, legacy_layout: bool = False) -> tuple[pathlib.Path, pathlib.Path]:
    """固定配置の共有ディレクトリを返す。各本文のstateでinboxとprocessingを指定する。

    移行だけを検証する場合はlegacy_layoutを指定し、旧状態ディレクトリを生成する。
    """
    inbox = wi_entry_files.entry_path(private_notes, "entry.md", legacy_layout=legacy_layout).parent
    processing = wi_entry_files.entry_path(private_notes, "entry.md", state="processing", legacy_layout=legacy_layout).parent
    return inbox, processing


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


class _BlockingSync:
    """同期処理の代替。呼び出し回数を数え、テストが解放するまで完了しない。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, *_args: object, **_kwargs: object) -> bool:
        self.calls += 1
        self.started.set()
        self.release.wait()
        return True


def _sync_app(tmp_path: pathlib.Path, operations: wi_operations.Operations) -> typing.Any:
    """同期APIの検証用に、指定した操作を使うアプリを生成する。"""
    return serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )


def _write_detail_entry(tmp_path: pathlib.Path, text: str, *, legacy_layout: bool = False) -> None:
    """詳細表示テスト用の入力ファイルを作成する。"""
    path = wi_entry_files.entry_path(tmp_path, "entry.md", legacy_layout=legacy_layout)
    path.write_text(text, encoding="utf-8")


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


def _write_repo_entry(
    root: pathlib.Path, state_name: str, filename: str, target_repo: str, *, legacy_layout: bool = False
) -> None:
    """対象リポジトリを持つエントリを配置する。"""
    path = wi_entry_files.entry_path(root, filename, state=state_name, legacy_layout=legacy_layout)
    state_line = "" if legacy_layout else f"state: {state_name}\n"
    path.write_text(
        f"---\n{state_line}target_repo: {target_repo}\ntype: awi\n---\n\n本文\n",
        encoding="utf-8",
    )


_BATCH_TEXT = (
    "# awi\n## target_repo: github.com/example/foo\n"
    "### keep.md [inbox]\n---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文\n\n"
)


def _patch_batch_repo_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    """一括取り込みで使うロック・remote同期・commitを無効化する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)


def _session_review_awi(
    body: str,
    *,
    entry_type: str = "awi",
    source: str | None = "session-review",
) -> str:
    """ユーザーコメント操作テスト用のawi本文を組み立てる。"""
    source_line = f"source: {source}\n" if source is not None else ""
    return f"---\nstate: inbox\ntype: {entry_type}\ntarget_repo: example/repo\n{source_line}---\n\n{body}"


def _patch_comment_edit_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """ユーザーコメントAPIテストでGit同期だけを無効化する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)


def _disable_wi_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """WI画面の操作がprivate-notesで行うロック・pull・commit・pushを、何もしない処理へ差し替える。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
