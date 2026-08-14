"""`atk serve`の自己完結型フロントエンド資産。"""

import base64

# ruff: noqa: E501

THEME_COLOR = "#3157d5"
FAVICON_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none">
  <path stroke="none" d="M0 0h24v24H0z" fill="none"/>
  <g stroke="white" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" fill="white">
    <path d="M4 13h3l3 3h4l3 -3h3"/>
    <path d="M4 13v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-4l-3 -8a2 2 0 0 0 -2 -1h-6a2 2 0 0 0 -2 1z"/>
  </g>
  <g stroke="#3157d5" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none">
    <path d="M4 13h3l3 3h4l3 -3h3"/>
    <path d="M4 13v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-4l-3 -8a2 2 0 0 0 -2 -1h-6a2 2 0 0 0 -2 1z"/>
  </g>
</svg>
"""
# 既存URLを参照するインストール済みPWAとの互換性を保つ。
ICON_192_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAYAAABS3GwHAAABiUlEQVR42u3TMQ0AAAjAMCyhBuXoARN89KiBJYusHvgqRMAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwAAYQAQOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAYAAwABgADAAGAAOAAcAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAbAACJgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAMAAYAA4ABwABgADAAGAAuLQQp097LuitXAAAAAElFTkSuQmCC"
)
ICON_512_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAAG40lEQVR42u3WMQEAAAQAQZWkkVweStjccAV++sjqAQB+CREAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAAAyAEABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAAAYABEAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAAAyAEABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAAAYABEAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAAAyAEABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAAAYABEAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAAAyAEABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAAAYABEAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAAAyAEABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAAAYABEAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAwAAGAAAAADAAAYAADAAAAABgAAMAAAgAEAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAABgAAMAAAAAGAAAwAACAAQAADAAAYAAAAAMAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAAwAAAAAYAAAyAEABgAAAAAwAAGAAAwAAAAAYAADAAAIABAAAMAABgAAAAAwAAGAAA4M4Co2GNkXMVNYcAAAAASUVORK5CYII="
)

HTML = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="__THEME_COLOR__">
  <title>フィードバック管理</title>
  <link rel="icon" type="image/svg+xml" href="__BASE_PATH_HTML__/favicon.svg">
  <link rel="manifest" href="__BASE_PATH_HTML__/manifest.webmanifest" crossorigin="use-credentials">
  <link rel="stylesheet" href="__BASE_PATH_HTML__/static/app.css">
</head>
<body>
  <header class="app-header">
    <div class="header-title">
      <h1>フィードバック管理</h1>
      <span id="connection-status" class="connection-status" role="status">接続中</span>
    </div>
    <div class="header-actions">
      <span id="sync-result" class="secondary-text" role="status"></span>
      <button id="notification-button" class="button-secondary" type="button" hidden>通知を有効化</button>
      <button id="refresh-button" class="button-secondary" type="button">今すぐ同期</button>
      <button id="create-button" class="button-primary" type="button">新規追加</button>
    </div>
  </header>

  <div id="global-error" class="global-error" role="alert" hidden></div>

  <main class="app-layout">
    <aside class="filters card" aria-label="フィルター条件">
      <div class="pane-heading">
        <h2>フィルター</h2>
        <button id="clear-filters-button" class="button-text" type="button">条件をクリア</button>
      </div>
      <label for="search-input">検索</label>
      <input id="search-input" type="search" placeholder="本文・ファイル名・対象・カテゴリ・投入元を検索">

      <div class="filter-grid">
        <label for="kind-filter">種別</label>
        <select id="kind-filter">
          <option value="all">all</option>
          <option value="feedback">feedback</option>
          <option value="tbd">tbd</option>
        </select>
        <label for="state-filter">状態</label>
        <select id="state-filter">
          <option value="active">active</option>
          <option value="all">all</option>
          <option value="inbox">inbox</option>
          <option value="processing">processing</option>
          <option value="adopted">adopted</option>
          <option value="rejected">rejected</option>
        </select>
        <label for="answer-filter">回答状況</label>
        <select id="answer-filter">
          <option value="all">すべて</option>
          <option value="no">未回答</option>
          <option value="yes">回答済み</option>
        </select>
      </div>

      <section class="additional-filters" aria-label="追加のフィルター条件">
        <div class="filter-grid">
          <label for="target-filter">対象リポジトリ</label>
          <select id="target-filter"><option value="">すべて</option></select>
          <label for="category-filter">category</label>
          <input id="category-filter" type="text">
          <label for="source-filter">投入元</label>
          <input id="source-filter" type="text">
          <label class="checkbox-field" for="source-empty-filter">
            <input id="source-empty-filter" type="checkbox">
            投入元が空
          </label>
        </div>
      </section>
    </aside>

    <section class="entry-pane card" aria-labelledby="entry-heading">
      <div class="pane-heading">
        <h2 id="entry-heading">一覧</h2>
        <span id="entry-count" class="secondary-text">0件（未回答TBD 0件）</span>
      </div>
      <div id="result-status" class="visually-hidden" role="status"></div>
      <div id="list-warning" class="list-warning" role="alert" hidden></div>
      <div id="loading-indicator" class="loading-state" role="status" hidden>読み込んでいます</div>
      <div class="entry-columns" aria-hidden="true">
        <span>ファイル名</span>
        <span>対象リポジトリ</span>
        <span>種別・状態</span>
        <span>要約</span>
      </div>
      <ul id="entry-list" class="entry-list" aria-label="エントリ一覧"></ul>
      <div id="empty-state" class="empty-state" hidden>
        <p id="empty-state-message"></p>
        <button id="empty-clear-button" class="button-secondary" type="button" hidden>条件をクリア</button>
        <button id="empty-all-states-button" class="button-secondary" type="button" hidden>すべての状態を表示</button>
        <button id="empty-create-button" class="button-primary" type="button" hidden>項目を追加</button>
      </div>
    </section>
  </main>

  <!-- ダイアログ内は確定操作と［×］だけを置き、終了時の一時入力は破棄する。 -->
  <dialog id="detail-dialog" class="dialog-shell detail-dialog" aria-labelledby="detail-heading">
    <article id="detail-shell" class="dialog-frame">
      <header class="dialog-header">
        <h2 id="detail-heading">詳細</h2>
        <button id="detail-close-button" class="dialog-close" type="button" aria-label="閉じる">×</button>
      </header>
      <div id="detail-dialog-body" class="dialog-body" tabindex="0">
        <div id="detail-alert" class="dialog-message error-message" role="alert" hidden></div>
        <div id="detail-status" class="dialog-message success-message" role="status" hidden></div>
        <section id="detail-view" hidden>
          <div class="detail-title-row">
            <h3 id="detail-filename"></h3>
            <span id="detail-state" class="state-badge"></span>
          </div>
          <dl id="detail-metadata" class="metadata"></dl>
          <p id="readonly-notice" class="readonly-notice" hidden>この項目は完了しているため、読取り専用です。</p>
          <section class="detail-body">
            <h3>本文</h3>
            <div id="detail-content" class="entry-content markdown-body"></div>
          </section>
        </section>
        <section id="edit-panel" class="edit-panel" hidden>
          <h3>ファイル全体を編集</h3>
          <label for="edit-content">ファイル全体（メタデータを含む、必須）</label>
          <p class="field-hint">frontmatterを含むMarkdownファイル全体を編集する。</p>
          <textarea id="edit-content" aria-describedby="edit-content-error" required></textarea>
          <p id="edit-content-error" class="inline-error" hidden></p>
        </section>
        <section id="answer-panel" class="answer-panel" hidden>
          <h3>回答</h3>
          <div id="answer-choices" class="answer-choices" aria-label="回答候補" hidden></div>
          <label for="answer-input">TBDへの回答（必須）</label>
          <textarea id="answer-input" aria-describedby="answer-input-error" required></textarea>
          <p id="answer-input-error" class="inline-error" hidden></p>
        </section>
      </div>
      <footer id="detail-footer" class="dialog-footer detail-footer">
        <div class="detail-footer-left">
          <button id="delete-button" class="button-danger" type="button">削除</button>
        </div>
        <div class="detail-footer-right">
          <button id="answer-button" class="button-primary" type="button" hidden>回答</button>
          <button id="edit-button" class="button-primary" type="button">編集</button>
          <button id="save-entry-button" class="button-primary" type="button" hidden>保存</button>
          <button id="save-answer-button" class="button-primary" type="button" hidden>回答を保存</button>
        </div>
      </footer>
    </article>
  </dialog>

  <dialog id="create-dialog" class="dialog-shell" aria-labelledby="create-dialog-heading">
    <form id="create-form" class="dialog-frame" novalidate>
      <header class="dialog-header">
        <h2 id="create-dialog-heading">新規追加</h2>
        <button id="create-close-button" class="dialog-close" type="button" aria-label="閉じる">×</button>
      </header>
      <div class="dialog-body" tabindex="0">
        <div id="create-alert" class="dialog-message error-message" role="alert" hidden></div>
        <div id="create-status" class="dialog-message success-message" role="status" hidden></div>
        <div class="dialog-form-fields">
          <label for="create-kind">種別（必須）</label>
          <select id="create-kind" name="type">
            <option value="feedback">feedback</option>
            <option value="tbd">tbd</option>
          </select>
          <label for="create-content">本文（必須）</label>
          <textarea id="create-content" name="message" aria-describedby="create-content-error" required></textarea>
          <p id="create-content-error" class="inline-error" hidden></p>
          <label for="create-target">対象リポジトリ（必須）</label>
          <input id="create-target" name="target_repo" list="repo-options" aria-describedby="create-target-error" required>
          <datalist id="repo-options"></datalist>
          <p id="create-target-error" class="inline-error" hidden></p>
          <label for="create-source">投入元（任意）</label>
          <input id="create-source" name="source">
          <div id="tbd-fields" class="dialog-form-fields" hidden>
            <label for="create-scope">確認範囲（任意）</label>
            <input id="create-scope" name="scope">
            <label for="create-question-type">回答形式（必須）</label>
            <select id="create-question-type" name="question_type">
              <option value="free-form">自由記述</option>
              <option value="yes-no">はい／いいえ</option>
              <option value="choice">選択式</option>
            </select>
            <div id="choice-fields" class="dialog-form-fields" hidden>
              <label for="create-choices">選択肢（1行1件、必須）</label>
              <textarea id="create-choices" name="choices" aria-describedby="create-choices-error"></textarea>
              <p id="create-choices-error" class="inline-error" hidden></p>
            </div>
          </div>
        </div>
      </div>
      <footer class="dialog-footer">
        <button id="create-submit-button" class="button-primary" type="submit">追加</button>
      </footer>
    </form>
  </dialog>

  <dialog id="delete-dialog" class="dialog-shell" aria-labelledby="delete-dialog-heading">
    <form id="delete-form" class="dialog-frame">
      <header class="dialog-header">
        <h2 id="delete-dialog-heading">削除の確認</h2>
        <button id="delete-close-button" class="dialog-close" type="button" aria-label="閉じる">×</button>
      </header>
      <div class="dialog-body" tabindex="0">
        <div id="delete-alert" class="dialog-message error-message" role="alert" hidden></div>
        <div id="delete-status" class="dialog-message success-message" role="status" hidden></div>
        <dl class="metadata">
          <dt>対象</dt><dd id="delete-target"></dd>
          <dt>状態</dt><dd><span id="delete-state" class="state-badge"></span></dd>
          <dt>対象リポジトリ</dt><dd id="delete-target-repo"></dd>
          <dt>要約</dt><dd id="delete-summary"></dd>
        </dl>
        <label id="force-delete-row" class="force-confirmation" hidden>
          <input id="force-delete-confirmation" type="checkbox" aria-describedby="delete-error">
          処理中の項目を強制的に削除する
        </label>
        <p id="delete-error" class="inline-error" hidden></p>
      </div>
      <footer class="dialog-footer">
        <button id="delete-submit-button" class="button-danger" type="submit">削除する</button>
      </footer>
    </form>
  </dialog>

  <div id="toast" class="toast" role="status" hidden></div>
  <script src="__BASE_PATH_HTML__/static/app.js"></script>
</body>
</html>""".replace("__THEME_COLOR__", THEME_COLOR)

CSS = """:root {
  --color-background: #f3f6fa;
  --color-surface: #ffffff;
  --color-text: #172033;
  --color-secondary-text: #5d6678;
  --color-border: #d9dfe8;
  --color-primary: #3157d5;
  --color-primary-hover: #2444ae;
  --color-normal: #eef2f8;
  --color-normal-hover: #e0e6ef;
  --color-danger: #b42318;
  --color-danger-hover: #8f1c13;
  --color-success: #18794e;
  --color-warning: #9a6700;
  --color-focus: #2563eb;
  --space-1: 0.375rem;
  --space-2: 0.625rem;
  --space-3: 1rem;
  --space-4: 1.5rem;
  --space-5: 2rem;
  --radius-small: 0.5rem;
  --radius-medium: 0.875rem;
  --shadow-card: 0 0.5rem 1.5rem rgb(30 45 75 / 0.09);
  --font-size-secondary: 0.875rem;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  color: var(--color-text);
  background: var(--color-background);
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 1rem;
  line-height: 1.55;
}
button, input, select, textarea {
  max-width: 100%;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  font: inherit;
}
input, select, textarea {
  width: 100%;
  padding: 0.7rem 0.8rem;
  color: var(--color-text);
  background: var(--color-surface);
}
textarea { resize: vertical; }
button {
  padding: 0.65rem 1rem;
  color: var(--color-text);
  background: var(--color-normal);
  cursor: pointer;
}
button:hover { background: var(--color-normal-hover); }
button:disabled { cursor: not-allowed; opacity: 0.55; }
:focus-visible { outline: 3px solid var(--color-focus); outline-offset: 2px; }
[hidden] { display: none !important; }

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
.app-header {
  position: sticky;
  z-index: 10;
  top: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-2) max(var(--space-3), calc((100vw - 1800px) / 2));
  border-bottom: 1px solid var(--color-border);
  background: rgb(255 255 255 / 0.96);
  box-shadow: 0 0.25rem 1rem rgb(30 45 75 / 0.06);
}
.header-title, .header-actions, .pane-heading, .detail-title-row, .dialog-footer {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.header-actions { justify-content: flex-end; flex-wrap: wrap; }
.header-actions button { padding: var(--space-1) var(--space-2); }
.app-header h1, .pane-heading h2, .dialog-header h2 { margin: 0; }
.app-header h1 { font-size: 1.375rem; }
.pane-heading { justify-content: space-between; }
.connection-status, .secondary-text, .readonly-notice, .field-hint {
  color: var(--color-secondary-text);
  font-size: var(--font-size-secondary);
}
.button-primary {
  border-color: var(--color-primary);
  color: #fff;
  background: var(--color-primary);
}
.button-primary:hover { background: var(--color-primary-hover); }
.button-secondary { background: var(--color-normal); }
.button-text { border-color: transparent; background: transparent; }
.button-danger {
  border-color: var(--color-danger);
  color: #fff;
  background: var(--color-danger);
}
.button-danger:hover { background: var(--color-danger-hover); }
.is-pending::before {
  display: inline-block;
  width: 0.85rem;
  height: 0.85rem;
  margin-right: var(--space-1);
  border: 2px solid currentcolor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  content: "";
}
.global-error, .list-warning, .dialog-message {
  padding: var(--space-3);
  border: 1px solid #f2b8b5;
  border-radius: var(--radius-small);
  color: var(--color-danger);
  background: #fff1f0;
  white-space: pre-wrap;
}
.global-error { max-width: 1800px; margin: var(--space-3) auto 0; }
.list-warning { margin-top: var(--space-3); color: #7a4e00; background: #fffaeb; border-color: #fedf89; }
.success-message { color: var(--color-success); background: #ecfdf3; border-color: #abefc6; }
.app-layout {
  display: grid;
  grid-template-columns: minmax(15rem, 0.24fr) minmax(0, 1fr);
  gap: var(--space-3);
  width: min(1800px, 100%);
  margin: 0 auto;
  padding: var(--space-3);
  align-items: start;
}
.card {
  min-width: 0;
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-medium);
  background: var(--color-surface);
  box-shadow: var(--shadow-card);
}
.filters, .dialog-form-fields, .edit-panel, .answer-panel {
  display: grid;
  gap: var(--space-2);
}
.filter-grid {
  display: grid;
  grid-template-columns: minmax(7rem, auto) 1fr;
  gap: var(--space-2);
  align-items: center;
}
.additional-filters { margin-top: var(--space-2); }
.additional-filters h3 { margin: 0 0 var(--space-2); font-size: inherit; }
.checkbox-field {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  grid-column: 2;
}
.checkbox-field input { width: auto; }
.entry-pane { min-height: 34rem; }
.entry-columns, .entry-select {
  display: grid;
  /* ファイル名と要約を主情報として幅を配分する。 */
  grid-template-columns:
    minmax(12rem, 1.4fr)
    minmax(10rem, 1.35fr)
    minmax(8rem, 1fr)
    minmax(12rem, 2fr);
  gap: var(--space-2);
  align-items: center;
}
.entry-columns {
  margin-top: var(--space-3);
  padding: 0 var(--space-2);
  color: var(--color-secondary-text);
  font-size: var(--font-size-secondary);
  font-weight: 700;
}
.entry-list {
  display: grid;
  gap: var(--space-2);
  margin: var(--space-2) 0 0;
  padding: 0;
  list-style: none;
}
.entry-select {
  width: 100%;
  padding: var(--space-2);
  text-align: left;
  background: var(--color-surface);
}
.entry-select:hover { border-color: #aeb9cd; background: #f7f9fd; }
.entry-select[aria-current="true"] {
  border-color: var(--color-primary);
  background: #eef2ff;
  box-shadow: 0 0 0 1px var(--color-primary);
}
.entry-select[data-unanswered-tbd="true"] {
  border-left: 0.4rem solid var(--color-warning);
  background: #fffdf5;
}
.entry-select[data-kind="unknown"] {
  border-style: dashed;
}
.entry-cell { min-width: 0; overflow-wrap: anywhere; }
.entry-cell::before { display: none; }
.filename-cell, .target-repo-cell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-cell { display: flex; flex-wrap: wrap; gap: var(--space-1); align-items: center; }
.time-cell { font-size: var(--font-size-secondary); }
.entry-kind { font-weight: 700; }
.state-badge, .attention-badge {
  display: inline-flex;
  width: fit-content;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  color: #344054;
  background: #e9edf4;
  font-size: var(--font-size-secondary);
  font-weight: 700;
}
.attention-badge { color: #7a4e00; background: #fef0c7; }
.state-badge[data-state="inbox"] { color: #175cd3; background: #eff8ff; }
.state-badge[data-state="processing"] { color: var(--color-warning); background: #fffaeb; }
.state-badge[data-state="adopted"] { color: var(--color-success); background: #ecfdf3; }
.state-badge[data-state="rejected"] { color: var(--color-danger); background: #fff1f0; }
.loading-state, .empty-state {
  margin-top: var(--space-4);
  padding: var(--space-5) var(--space-3);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-small);
  color: var(--color-secondary-text);
  text-align: center;
}
.loading-state::before {
  display: inline-block;
  width: 0.9rem;
  height: 0.9rem;
  margin-right: var(--space-2);
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  content: "";
}

dialog.dialog-shell {
  width: min(42rem, calc(100% - 2rem));
  max-height: calc(100vh - 2rem);
  overflow: hidden;
  padding: 0;
  border: 0;
  border-radius: var(--radius-medium);
  box-shadow: 0 1.5rem 4rem rgb(20 30 50 / 0.25);
}
dialog.detail-dialog { width: min(72rem, calc(100% - 2rem)); }
dialog::backdrop { background: rgb(15 23 42 / 0.55); }
.dialog-frame {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  max-height: calc(100vh - 2rem);
  margin: 0;
}
.dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}
.dialog-close {
  display: inline-grid;
  flex: 0 0 2.75rem;
  width: 2.75rem;
  height: 2.75rem;
  padding: 0;
  place-items: center;
  border-color: transparent;
  background: transparent;
  font-size: 1.5rem;
  line-height: 1;
}
.dialog-body {
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-4);
  background: var(--color-surface);
}
.dialog-footer {
  justify-content: flex-end;
  flex-wrap: wrap;
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
}
.detail-footer { justify-content: space-between; }
.detail-footer-left, .detail-footer-right { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.detail-title-row { justify-content: space-between; }
.detail-title-row h3 { overflow-wrap: anywhere; }
.metadata {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--space-1) var(--space-3);
}
.metadata dt { font-weight: 700; }
.metadata dd { margin: 0; overflow-wrap: anywhere; }
.detail-body { margin-top: var(--space-4); }
.entry-content {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-surface);
  overflow-wrap: anywhere;
}
.markdown-body > :first-child { margin-top: 0; }
.markdown-body > :last-child { margin-bottom: 0; }
.markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body h4,
.markdown-body ul, .markdown-body ol, .markdown-body pre, .markdown-body blockquote,
.markdown-body table { margin-block: var(--space-3); }
.markdown-body pre, .markdown-body code {
  font-family: ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace;
}
.markdown-body :not(pre) > code {
  padding: 0.12rem 0.35rem;
  border-radius: 0.3rem;
  background: #e9eef5;
}
.markdown-body pre {
  padding: var(--space-2);
  border-radius: var(--radius-small);
  background: #e9eef5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.markdown-body pre code { padding: 0; background: transparent; }
.markdown-body blockquote {
  margin-inline: 0;
  padding-left: var(--space-3);
  border-left: 0.25rem solid var(--color-border);
  color: var(--color-secondary-text);
}
.markdown-body table { border-collapse: collapse; }
.markdown-body th, .markdown-body td {
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
}
.edit-panel, .answer-panel {
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}
.field-hint { margin: 0; }
#edit-content { min-height: clamp(14rem, 38vh, 30rem); }
#answer-input { min-height: clamp(7rem, 20vh, 14rem); }
#create-content { min-height: clamp(9rem, 28vh, 20rem); }
#create-choices { min-height: clamp(6rem, 16vh, 11rem); }
.answer-choices { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.inline-error { margin: 0; color: var(--color-danger); font-size: var(--font-size-secondary); }
[aria-invalid="true"] { border-color: var(--color-danger); }
.force-confirmation {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-small);
  background: #fff7ed;
}
.force-confirmation input { width: auto; }
.toast {
  position: fixed;
  z-index: 30;
  right: var(--space-4);
  bottom: var(--space-4);
  max-width: min(28rem, calc(100% - 2rem));
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-small);
  color: #fff;
  background: #1f2937;
  box-shadow: var(--shadow-card);
}
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 1279px) {
  .entry-columns { display: none; }
  .entry-select { grid-template-columns: 1fr; gap: var(--space-1); padding: var(--space-3); }
  .entry-cell {
    display: grid;
    grid-template-columns: minmax(7rem, 35%) minmax(0, 1fr);
    gap: var(--space-2);
  }
  .entry-cell::before {
    display: inline;
    color: var(--color-secondary-text);
    content: attr(data-label);
    font-size: var(--font-size-secondary);
    font-weight: 700;
  }
}
@media (max-width: 700px) {
  .app-header { align-items: flex-start; flex-wrap: wrap; }
  .header-title { min-width: 100%; justify-content: space-between; }
  .header-actions {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    width: 100%;
    margin-left: auto;
  }
  #sync-result { font-size: 0.75rem; line-height: 1.25; }
  .app-layout { grid-template-columns: 1fr; padding: var(--space-2); }
  .entry-pane { min-height: 0; }
  .filter-grid { grid-template-columns: 1fr; }
  .checkbox-field { grid-column: auto; }
  dialog.dialog-shell { width: calc(100% - 1rem); max-height: calc(100vh - 1rem); }
  .dialog-frame { max-height: calc(100vh - 1rem); }
  .dialog-header, .dialog-body, .dialog-footer { padding-inline: var(--space-3); }
  .dialog-footer button { width: auto; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { animation: none !important; transition: none !important; }
}"""

JS = """const BASE_PATH=__BASE_PATH_JS__;
// エラー表示は既存のError契約に合わせ、error.messageを直接参照する。
const KIND_LABELS = {feedback: 'フィードバック', tbd: 'TBD', unknown: '種別不明'};
const STATE_LABELS = {inbox: '未処理', processing: '処理中', adopted: '採用済み', rejected: '不採用'};
const ACTIVE_STATES = new Set(['inbox', 'processing']);
const METADATA_FIELDS = [
  ['kind', '種別'],
  ['state', '状態'],
  ['answered', '回答状況'],
  ['target_repo', '対象リポジトリ'],
  ['category', 'カテゴリ'],
  ['source', '投入元'],
  ['updated_at', '更新日時']
];

let entries = [];
let currentEntry = null;
let detailOrigin = null;
let detailOriginKey = '';
let detailRequestGeneration = 0;
let detailSessionGeneration = 0;
let listRequestGeneration = 0;
let targetRepoRequestGeneration = 0;
let pendingListRequests = 0;
let pendingListAnnouncement = false;
let detailRefreshRequired = false;
let deleteDialogEntrySnapshot = '';
let searchTimer = null;
let toastTimer = null;
let knownTbdBaselineReady = false;
const knownTbdFilenames = new Set();
const pendingOperations = new Set();
const dialogOrigins = new Map();
const dialogStack = [];

const byId = id => document.getElementById(id);
const entryKey = entry => entry ? `${entry.state}/${entry.filename}` : '';
const deleteEntrySnapshot = entry => entry ? JSON.stringify([
  entryKey(entry), entry.content, entry.target_repo || '', entry.summary || ''
]) : '';

async function api(path, options = {}) {
  const request = {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})}
  };
  const response = await fetch(BASE_PATH + path, request);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || response.statusText || '通信に失敗しました。');
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function setTextMessage(id, message) {
  const element = byId(id);
  element.textContent = message;
  element.hidden = !message;
}

function clearDialogMessages(dialogName) {
  setTextMessage(`${dialogName}-alert`, '');
  setTextMessage(`${dialogName}-status`, '');
}

function showToast(message) {
  const toast = byId('toast');
  toast.textContent = message;
  toast.hidden = false;
  if (toastTimer !== null) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 4000);
}

function topmostDialog() {
  for (let index = dialogStack.length - 1; index >= 0; index -= 1) {
    const dialog = byId(dialogStack[index]);
    if (dialog && dialog.open) return dialog;
  }
  return null;
}

function deliverOperationMessage(message, isError = false) {
  const dialog = topmostDialog();
  if (dialog) {
    const name = dialog.id.replace('-dialog', '');
    setTextMessage(`${name}-${isError ? 'alert' : 'status'}`, message);
    return;
  }
  if (isError) setTextMessage('global-error', message);
  else showToast(message);
}

function openDialog(dialog, origin, focusTarget) {
  dialogOrigins.set(dialog.id, origin || document.activeElement);
  const previous = dialogStack.indexOf(dialog.id);
  if (previous >= 0) dialogStack.splice(previous, 1);
  dialogStack.push(dialog.id);
  dialog.showModal();
  if (focusTarget) focusTarget.focus();
}

function closeDialog(dialog, {restoreFocus = true} = {}) {
  const wasOpen = dialog.open;
  if (wasOpen) dialog.close();
  const index = dialogStack.lastIndexOf(dialog.id);
  if (index >= 0) dialogStack.splice(index, 1);
  const origin = dialogOrigins.get(dialog.id);
  dialogOrigins.delete(dialog.id);
  if (wasOpen && restoreFocus && origin && typeof origin.focus === 'function') origin.focus();
}

function setFieldError(input, errorElement, message) {
  input.setAttribute('aria-invalid', message ? 'true' : 'false');
  errorElement.textContent = message;
  errorElement.hidden = !message;
}

function firstInvalid(inputs) {
  const invalid = inputs.find(input => input.getAttribute('aria-invalid') === 'true');
  if (invalid) invalid.focus();
  return invalid;
}

async function runPending(key, {container, button, busyLabel}, operation) {
  if (pendingOperations.has(key)) return undefined;
  pendingOperations.add(key);
  const controls = Array.from(container.querySelectorAll('input, select, textarea, button'))
    .filter(control => !control.classList.contains('dialog-close'));
  const previous = controls.map(control => control.disabled);
  const originalLabel = button.textContent;
  controls.forEach(control => { control.disabled = true; });
  button.disabled = true;
  button.textContent = busyLabel;
  button.classList.add('is-pending');
  button.setAttribute('aria-busy', 'true');
  container.setAttribute('aria-busy', 'true');
  try {
    return await operation();
  } finally {
    controls.forEach((control, index) => { control.disabled = previous[index]; });
    button.textContent = originalLabel;
    button.classList.remove('is-pending');
    button.setAttribute('aria-busy', 'false');
    container.setAttribute('aria-busy', 'false');
    pendingOperations.delete(key);
    syncFilterDependencies();
    syncDetailMutationAvailability();
  }
}

function formatDateParts(value) {
  if (!value) return {date: '—', time: ''};
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return {date: String(value), time: ''};
  const parts = new Intl.DateTimeFormat('ja-JP', {
    timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false
  }).formatToParts(date);
  const part = type => parts.find(item => item.type === type)?.value || '';
  return {date: `${part('year')}/${part('month')}/${part('day')}`, time: `${part('hour')}:${part('minute')}`};
}

function kindLabel(kind) { return KIND_LABELS[kind] || '種別不明'; }
function stateLabel(state) { return STATE_LABELS[state] || state || '不明'; }

function appendCell(row, label, className) {
  const cell = document.createElement('span');
  cell.className = `entry-cell ${className}`;
  cell.dataset.label = label;
  row.append(cell);
  return cell;
}

function appendTextCell(row, label, className, value) {
  const cell = appendCell(row, label, className);
  cell.textContent = value || '—';
  return cell;
}

function renderEntry(entry) {
  const item = document.createElement('li');
  item.className = 'entry-row';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'entry-select';
  button.dataset.key = entryKey(entry);
  button.dataset.kind = entry.kind || 'unknown';
  const unanswered = entry.kind === 'tbd' && entry.answered === false;
  button.dataset.unansweredTbd = String(unanswered);
  button.setAttribute('aria-current', String(entryKey(currentEntry) === entryKey(entry)));

  appendTextCell(button, 'ファイル名', 'filename-cell', entry.filename);
  appendTextCell(button, '対象リポジトリ', 'target-repo-cell', entry.target_repo);
  const status = appendCell(button, '種別・状態', 'status-cell');
  const kind = document.createElement('span');
  kind.className = 'entry-kind';
  kind.textContent = entry.kind || 'unknown';
  status.append(kind);
  const badge = document.createElement('span');
  badge.className = 'state-badge';
  badge.dataset.state = entry.state;
  badge.textContent = entry.state || 'unknown';
  status.append(badge);
  if (unanswered) {
    const attention = document.createElement('span');
    attention.className = 'attention-badge';
    attention.textContent = '未回答';
    status.append(attention);
  }
  appendTextCell(button, '要約', 'summary-cell', entry.summary);
  button.setAttribute(
    'aria-label',
    [entry.filename, entry.target_repo || '対象なし', entry.kind || 'unknown', entry.state || 'unknown',
      unanswered ? '未回答' : '', entry.summary || '要約なし'].filter(Boolean).join('、')
  );
  button.addEventListener('click', () => selectEntry(entry, button));
  item.append(button);
  return item;
}

function hasNonStateFilters() {
  return byId('search-input').value !== '' ||
    byId('kind-filter').value !== 'all' ||
    byId('answer-filter').value !== 'all' ||
    byId('target-filter').value !== '' ||
    byId('category-filter').value !== '' ||
    byId('source-filter').value !== '' ||
    byId('source-empty-filter').checked;
}

function filtersAreDefault() {
  return !hasNonStateFilters() &&
    byId('kind-filter').value === 'all' &&
    byId('state-filter').value === 'active' &&
    byId('answer-filter').value === 'all';
}

function renderEmptyState() {
  const empty = byId('empty-state');
  const message = byId('empty-state-message');
  const clear = byId('empty-clear-button');
  const allStates = byId('empty-all-states-button');
  const create = byId('empty-create-button');
  empty.hidden = entries.length !== 0;
  clear.hidden = true;
  allStates.hidden = true;
  create.hidden = true;
  if (entries.length) return;
  if (hasNonStateFilters() || !['active', 'all'].includes(byId('state-filter').value)) {
    message.textContent = '条件に一致する項目はありません。';
    clear.hidden = false;
  } else if (byId('state-filter').value === 'active') {
    message.textContent = '対応中の項目はありません。';
    allStates.hidden = false;
  } else {
    message.textContent = '項目はまだありません。';
    create.hidden = false;
  }
}

function renderWarnings(warnings) {
  const warning = byId('list-warning');
  if (!warnings.length) {
    warning.hidden = true;
    warning.textContent = '';
    return;
  }
  warning.textContent = `一覧から除外したファイル: ${warnings.map(item => `${item.filename}（${item.reason}）`).join('、')}`;
  warning.hidden = false;
}

function renderList(warnings = [], announce = false) {
  const list = byId('entry-list');
  list.replaceChildren(...entries.map(renderEntry));
  const unanswered = entries.filter(entry => entry.kind === 'tbd' && entry.answered === false).length;
  byId('entry-count').textContent = `${entries.length}件（未回答TBD ${unanswered}件）`;
  renderWarnings(warnings);
  renderEmptyState();
  if (announce) {
    byId('result-status').textContent = entries.length ? `${entries.length}件を表示` : '一致する項目はありません';
  }
}

function buildQuery() {
  const parameters = new URLSearchParams();
  parameters.set('type', byId('kind-filter').value);
  parameters.set('status', byId('state-filter').value);
  parameters.set('answered', byId('answer-filter').value);
  const values = {
    target_repo: byId('target-filter').value,
    category: byId('category-filter').value.trim(),
    source: byId('source-filter').value.trim(),
    q: byId('search-input').value.trim()
  };
  Object.entries(values).forEach(([name, value]) => { if (value) parameters.set(name, value); });
  if (byId('source-empty-filter').checked) parameters.set('source_empty', 'true');
  return parameters;
}

function setListLoading(value) {
  pendingListRequests += value ? 1 : -1;
  pendingListRequests = Math.max(0, pendingListRequests);
  const loading = pendingListRequests > 0;
  byId('loading-indicator').hidden = !loading;
  byId('entry-list').setAttribute('aria-busy', String(loading));
}

async function loadEntries({announce = false} = {}) {
  pendingListAnnouncement = pendingListAnnouncement || announce;
  const generation = ++listRequestGeneration;
  setListLoading(true);
  try {
    const payload = await api(`/api/entries?${buildQuery().toString()}`);
    if (generation !== listRequestGeneration) return entries;
    entries = Array.isArray(payload.entries) ? payload.entries : [];
    const selected = entries.find(item => entryKey(item) === entryKey(currentEntry));
    if (selected) currentEntry = {...currentEntry, ...selected};
    const shouldAnnounce = pendingListAnnouncement;
    pendingListAnnouncement = false;
    renderList(Array.isArray(payload.warnings) ? payload.warnings : [], shouldAnnounce);
    return entries;
  } catch (error) {
    if (generation === listRequestGeneration) {
      pendingListAnnouncement = false;
      setTextMessage('global-error', error.message);
    }
    return entries;
  } finally {
    setListLoading(false);
  }
}

function syncNotificationButton() {
  const button = byId('notification-button');
  button.hidden = typeof Notification === 'undefined' || Notification.permission !== 'default';
}

async function refreshKnownTbds({notify = false} = {}) {
  const payload = await api('/api/entries?type=tbd&status=all&answered=all');
  const allTbds = Array.isArray(payload.entries) ? payload.entries : [];
  const newUnanswered = knownTbdBaselineReady && notify ? allTbds.filter(entry =>
    !knownTbdFilenames.has(entry.filename) && ACTIVE_STATES.has(entry.state) && entry.answered === false
  ) : [];
  allTbds.forEach(entry => knownTbdFilenames.add(entry.filename));
  knownTbdBaselineReady = true;
  if (newUnanswered.length && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
    const filenames = newUnanswered.map(entry => entry.filename);
    new Notification('新規未回答TBD', {
      body: filenames.length === 1 ? filenames[0] : `${filenames.length}件: ${filenames.join('、')}`
    });
  }
}

async function enableNotifications() {
  if (typeof Notification === 'undefined') return;
  await Notification.requestPermission();
  syncNotificationButton();
}

function replaceOptions(select, values, firstLabel) {
  const selected = select.value;
  select.replaceChildren();
  const first = document.createElement('option');
  first.value = '';
  first.textContent = firstLabel;
  select.append(first);
  values.forEach(value => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.append(option);
  });
  select.value = values.includes(selected) ? selected : '';
}

async function loadTargetRepos() {
  const generation = ++targetRepoRequestGeneration;
  const requestedState = byId('state-filter').value;
  try {
    const status = encodeURIComponent(requestedState);
    const payload = await api(`/api/repos?status=${status}`);
    if (generation !== targetRepoRequestGeneration || byId('state-filter').value !== requestedState) return false;
    const repos = Array.isArray(payload.repos) ? payload.repos : [];
    replaceOptions(byId('target-filter'), repos, 'すべて');
    const datalist = byId('repo-options');
    datalist.replaceChildren(...repos.map(value => {
      const option = document.createElement('option');
      option.value = value;
      return option;
    }));
    return true;
  } catch (error) {
    const isCurrent = generation === targetRepoRequestGeneration && byId('state-filter').value === requestedState;
    if (isCurrent) setTextMessage('global-error', error.message);
    return isCurrent;
  }
}

function syncFilterDependencies() {
  const feedbackOnly = byId('kind-filter').value === 'feedback';
  if (feedbackOnly) byId('answer-filter').value = 'all';
  byId('answer-filter').disabled = feedbackOnly;
  const sourceEmpty = byId('source-empty-filter').checked;
  if (sourceEmpty) byId('source-filter').value = '';
  byId('source-filter').disabled = sourceEmpty;
}

async function clearFilters({load = true} = {}) {
  byId('search-input').value = '';
  byId('kind-filter').value = 'all';
  byId('state-filter').value = 'active';
  byId('answer-filter').value = 'all';
  byId('target-filter').value = '';
  byId('category-filter').value = '';
  byId('source-filter').value = '';
  byId('source-empty-filter').checked = false;
  syncFilterDependencies();
  if (load) {
    await loadTargetRepos();
    await loadEntries({announce: true});
  }
}

function updateCurrentRowSelection() {
  document.querySelectorAll('.entry-select').forEach(button => {
    button.setAttribute('aria-current', String(button.dataset.key === entryKey(currentEntry)));
  });
}

function entryButtonForKey(key) {
  return Array.from(document.querySelectorAll('.entry-select')).find(button => button.dataset.key === key) || null;
}

function detailReturnTarget() {
  const currentOrigin = entryButtonForKey(detailOriginKey);
  if (currentOrigin) return currentOrigin;
  const original = dialogOrigins.get('detail-dialog');
  if (original?.isConnected) return original;
  const firstRow = document.querySelectorAll('.entry-select')[0];
  if (firstRow) return firstRow;
  const emptyAction = [byId('empty-clear-button'), byId('empty-all-states-button'), byId('empty-create-button')]
    .find(button => !button.hidden);
  return emptyAction || byId('search-input');
}

function metadataValue(entry, key) {
  if (key === 'kind') return entry.kind || 'unknown';
  if (key === 'state') return entry.state || 'unknown';
  if (key === 'answered') return entry.answered === true ? '回答済み' : entry.answered === false ? '未回答' : '';
  if (key === 'updated_at') {
    const parts = formatDateParts(entry.updated_at);
    return parts.time ? `${parts.date} ${parts.time}` : parts.date;
  }
  return entry[key] == null ? '' : String(entry[key]);
}

function renderMetadata(entry) {
  const metadata = byId('detail-metadata');
  metadata.replaceChildren();
  METADATA_FIELDS.forEach(([key, label]) => {
    const value = metadataValue(entry, key);
    if (!value) return;
    const term = document.createElement('dt');
    term.textContent = label;
    const definition = document.createElement('dd');
    definition.textContent = value;
    metadata.append(term, definition);
  });
}

function setDetailMode(mode) {
  const editing = mode === 'edit';
  const answering = mode === 'answer';
  const unansweredTbd = currentEntry?.kind === 'tbd' && currentEntry.answered === false;
  byId('edit-panel').hidden = !editing;
  byId('answer-panel').hidden = !answering;
  byId('edit-button').hidden = editing || answering || !currentEntry || !ACTIVE_STATES.has(currentEntry.state);
  byId('answer-button').hidden = editing || answering || !currentEntry ||
    currentEntry.kind !== 'tbd' || !ACTIVE_STATES.has(currentEntry.state);
  byId('answer-button').textContent = currentEntry?.answered === true ? '回答を変更' : '回答';
  byId('delete-button').hidden = editing || answering || !currentEntry || !ACTIVE_STATES.has(currentEntry.state);
  byId('save-entry-button').hidden = !editing;
  byId('save-answer-button').hidden = !answering;
  syncDetailMutationAvailability();
  byId('edit-button').className = unansweredTbd ? 'button-secondary' : 'button-primary';
  if (!editing) setFieldError(byId('edit-content'), byId('edit-content-error'), '');
  if (!answering) setFieldError(byId('answer-input'), byId('answer-input-error'), '');
}

function syncDetailMutationAvailability() {
  for (const id of ['edit-button', 'answer-button', 'delete-button', 'save-entry-button', 'save-answer-button']) {
    const button = byId(id);
    button.disabled = !button.hidden && detailRefreshRequired;
  }
}

function renderAnswerChoices(entry) {
  const container = byId('answer-choices');
  const choices = entry.question_type === 'yes-no' ? ['はい', 'いいえ'] :
    entry.question_type === 'choice' && Array.isArray(entry.choices) ? entry.choices : [];
  container.replaceChildren(...choices.map(value => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'button-secondary';
    button.textContent = value;
    button.addEventListener('click', () => {
      byId('answer-input').value = value;
      byId('answer-input').focus();
    });
    return button;
  }));
  container.hidden = choices.length === 0;
}

function displayEntry(entry) {
  currentEntry = entry;
  detailRefreshRequired = false;
  setTextMessage('detail-alert', '');
  byId('detail-view').hidden = false;
  byId('detail-filename').textContent = entry.filename;
  byId('detail-state').textContent = `${entry.kind || 'unknown'} / ${entry.state || 'unknown'}`;
  byId('detail-state').dataset.state = entry.state;
  byId('detail-content').innerHTML = entry.body_html ?? entry.content_html ?? '';
  renderMetadata(entry);
  renderAnswerChoices(entry);
  byId('readonly-notice').hidden = ACTIVE_STATES.has(entry.state);
  setDetailMode('view');
  updateCurrentRowSelection();
}

async function selectEntry(entry, origin = null) {
  const requestGeneration = ++detailRequestGeneration;
  const sessionGeneration = ++detailSessionGeneration;
  detailOrigin = origin || document.activeElement;
  detailOriginKey = entryKey(entry);
  clearDialogMessages('detail');
  try {
    const payload = await api(`/api/entries/${encodeURIComponent(entry.state)}/${encodeURIComponent(entry.filename)}`);
    if (requestGeneration !== detailRequestGeneration || sessionGeneration !== detailSessionGeneration) return;
    displayEntry(payload.entry);
    openDialog(byId('detail-dialog'), detailOrigin, byId('detail-dialog-body'));
  } catch (error) {
    if (requestGeneration === detailRequestGeneration) setTextMessage('global-error', error.message);
  }
}

function closeDeleteDialog({restoreFocus = true} = {}) {
  closeDialog(byId('delete-dialog'), {restoreFocus});
  deleteDialogEntrySnapshot = '';
}

function invalidateDeleteConfirmation() {
  if (!byId('delete-dialog').open) return false;
  closeDeleteDialog({restoreFocus: false});
  byId('detail-dialog-body').focus();
  return true;
}

const DELETE_RECONFIRM_MESSAGE =
  '外部更新により削除確認を閉じました。詳細を確認し、削除操作をやり直してください。';
const DELETE_CONFLICT_MESSAGE =
  '外部更新により削除確認を閉じました。詳細を閉じて開き直してから削除してください。';

function reportExternalDetailFailure(error, deleteConfirmationInvalidated) {
  const invalidated = invalidateDeleteConfirmation() || deleteConfirmationInvalidated;
  const recovery = invalidated ? ` ${DELETE_RECONFIRM_MESSAGE}` : '';
  setTextMessage('detail-alert', `${error.message}${recovery}`);
}

function closeDetailDialog() {
  const detailDialog = byId('detail-dialog');
  const deleteDialog = byId('delete-dialog');
  const hadOpenDialog = detailDialog.open || deleteDialog.open;
  if (!hadOpenDialog && !currentEntry) return;
  const returnTarget = detailReturnTarget();
  detailRequestGeneration += 1;
  detailSessionGeneration += 1;
  closeDeleteDialog({restoreFocus: false});
  closeDialog(detailDialog, {restoreFocus: false});
  currentEntry = null;
  detailOriginKey = '';
  detailRefreshRequired = false;
  setTextMessage('detail-alert', '');
  setDetailMode('view');
  updateCurrentRowSelection();
  if (hadOpenDialog && returnTarget && typeof returnTarget.focus === 'function') returnTarget.focus();
}

function currentDetailMode() {
  if (!byId('edit-panel').hidden) return 'edit';
  if (!byId('answer-panel').hidden) return 'answer';
  return 'view';
}

async function reloadOpenDetailFromExternalChange() {
  if (!byId('detail-dialog').open || !currentEntry) return;
  if (detailOriginKey !== entryKey(currentEntry)) return;
  const sessionGeneration = detailSessionGeneration;
  const originalState = currentEntry.state;
  const filename = currentEntry.filename;
  const requestGeneration = ++detailRequestGeneration;
  const requestIsCurrent = () => requestGeneration === detailRequestGeneration &&
    sessionGeneration === detailSessionGeneration && byId('detail-dialog').open &&
    currentEntry?.filename === filename;
  let deleteConfirmationInvalidated = false;
  if (byId('delete-dialog').open && deleteEntrySnapshot(currentEntry) !== deleteDialogEntrySnapshot) {
    deleteConfirmationInvalidated = invalidateDeleteConfirmation();
  }
  let resolvedEntry = null;
  try {
    const payload = await api(`/api/entries/${encodeURIComponent(originalState)}/${encodeURIComponent(filename)}`);
    if (!requestIsCurrent()) return;
    resolvedEntry = payload.entry;
  } catch (error) {
    if (!requestIsCurrent()) return;
    if (error.status !== 404) {
      reportExternalDetailFailure(error, deleteConfirmationInvalidated);
      return;
    }
    deleteConfirmationInvalidated = invalidateDeleteConfirmation() || deleteConfirmationInvalidated;
  }
  if (!resolvedEntry) {
    const candidates = [];
    for (const state of Object.keys(STATE_LABELS).filter(state => state !== originalState)) {
      try {
        const payload = await api(`/api/entries/${encodeURIComponent(state)}/${encodeURIComponent(filename)}`);
        if (!requestIsCurrent()) return;
        candidates.push(payload.entry);
      } catch (error) {
        if (!requestIsCurrent()) return;
        if (error.status !== 404) {
          reportExternalDetailFailure(error, deleteConfirmationInvalidated);
          return;
        }
      }
    }
    if (candidates.length > 1) {
      closeDetailDialog();
      setTextMessage('global-error', `${filename}の移動先を一意に特定できません。詳細を開き直してください。`);
      return;
    }
    resolvedEntry = candidates[0] || null;
  }
  if (!resolvedEntry) {
    closeDetailDialog();
    return;
  }
  const detailChanged = entryKey(resolvedEntry) !== entryKey(currentEntry) ||
    resolvedEntry.content !== currentEntry.content;
  if (byId('delete-dialog').open && deleteEntrySnapshot(resolvedEntry) !== deleteDialogEntrySnapshot) {
    deleteConfirmationInvalidated = invalidateDeleteConfirmation() || deleteConfirmationInvalidated;
  }
  if (currentDetailMode() !== 'view') {
    currentEntry = {...currentEntry, state: resolvedEntry.state};
    detailOriginKey = entryKey(currentEntry);
    if (detailChanged) {
      detailRefreshRequired = true;
      setTextMessage(
        'detail-alert',
        '外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。'
      );
      setDetailMode(currentDetailMode());
    }
    updateCurrentRowSelection();
    return;
  }
  detailOriginKey = entryKey(resolvedEntry);
  displayEntry(resolvedEntry);
  if (deleteConfirmationInvalidated) setTextMessage('detail-alert', DELETE_RECONFIRM_MESSAGE);
}

function enterEdit() {
  if (!currentEntry) return;
  byId('edit-content').value = currentEntry.content;
  setDetailMode('edit');
  byId('edit-content').focus();
}

function enterAnswer() {
  if (!currentEntry) return;
  byId('answer-input').value = currentEntry.answer || '';
  setDetailMode('answer');
  byId('answer-input').focus();
}

function mutationFailureMessage(key, failure, error) {
  const recoveryRequired = error.payload?.code === 'edit_conflict' || detailRefreshRequired;
  if (!recoveryRequired) return failure;
  detailRefreshRequired = true;
  syncDetailMutationAvailability();
  const recovery = `${key}は外部で更新されました。入力を保持しています。` +
    '詳細を閉じて開き直してから保存してください。';
  return `${failure} ${recovery}`;
}

async function saveEntry() {
  if (!currentEntry || detailRefreshRequired) return;
  const content = byId('edit-content').value;
  setFieldError(byId('edit-content'), byId('edit-content-error'), content.trim() ? '' : 'ファイル全体を入力してください。');
  if (firstInvalid([byId('edit-content')])) return;
  const key = entryKey(currentEntry);
  const sessionGeneration = detailSessionGeneration;
  const payload = {content, expected_content: currentEntry.content};
  clearDialogMessages('detail');
  try {
    await runPending('save', {
      container: byId('detail-shell'), button: byId('save-entry-button'), busyLabel: '保存中'
    }, () => api(`/api/entries/${encodeURIComponent(currentEntry.state)}/${encodeURIComponent(currentEntry.filename)}`, {
      method: 'PUT', body: JSON.stringify(payload)
    }));
    await loadEntries();
    if (byId('detail-dialog').open && entryKey(currentEntry) === key &&
        sessionGeneration === detailSessionGeneration) {
      const refreshed = await api(`/api/entries/${encodeURIComponent(currentEntry.state)}/${encodeURIComponent(currentEntry.filename)}`);
      if (byId('detail-dialog').open && entryKey(currentEntry) === key &&
          sessionGeneration === detailSessionGeneration) {
        displayEntry(refreshed.entry);
        byId('detail-dialog-body').focus();
      }
    }
    deliverOperationMessage(`${key}を保存しました。`);
  } catch (error) {
    const failure = `${key}を保存できませんでした。 ${error.message}`;
    deliverOperationMessage(mutationFailureMessage(key, failure, error), true);
    if (byId('detail-dialog').open && entryKey(currentEntry) === key) byId('edit-content').focus();
  }
}

async function saveAnswer() {
  if (!currentEntry || detailRefreshRequired) return;
  const answer = byId('answer-input').value;
  setFieldError(byId('answer-input'), byId('answer-input-error'), answer.trim() ? '' : '回答を入力してください。');
  if (firstInvalid([byId('answer-input')])) return;
  const key = entryKey(currentEntry);
  const sessionGeneration = detailSessionGeneration;
  const payload = {
    filename: currentEntry.filename,
    state: currentEntry.state,
    answer,
    expected_content: currentEntry.content
  };
  clearDialogMessages('detail');
  try {
    await runPending('answer', {
      container: byId('detail-shell'), button: byId('save-answer-button'), busyLabel: '保存中'
    }, () => api('/api/entries/answer', {method: 'POST', body: JSON.stringify(payload)}));
    await loadEntries();
    if (byId('detail-dialog').open && entryKey(currentEntry) === key &&
        sessionGeneration === detailSessionGeneration) {
      const refreshed = await api(`/api/entries/${encodeURIComponent(currentEntry.state)}/${encodeURIComponent(currentEntry.filename)}`);
      if (byId('detail-dialog').open && entryKey(currentEntry) === key &&
          sessionGeneration === detailSessionGeneration) {
        displayEntry(refreshed.entry);
        byId('detail-dialog-body').focus();
      }
    }
    deliverOperationMessage(`${key}へ回答しました。`);
  } catch (error) {
    const failure = `${key}へ回答できませんでした。 ${error.message}`;
    deliverOperationMessage(mutationFailureMessage(key, failure, error), true);
    if (byId('detail-dialog').open && entryKey(currentEntry) === key) byId('answer-input').focus();
  }
}

function resetCreateForm() {
  byId('create-kind').value = 'feedback';
  byId('create-content').value = '';
  byId('create-target').value = '';
  byId('create-source').value = '';
  byId('create-scope').value = '';
  byId('create-question-type').value = 'free-form';
  byId('create-choices').value = '';
  setFieldError(byId('create-content'), byId('create-content-error'), '');
  setFieldError(byId('create-target'), byId('create-target-error'), '');
  setFieldError(byId('create-choices'), byId('create-choices-error'), '');
  updateCreateFields();
}

function updateCreateFields() {
  const isTbd = byId('create-kind').value === 'tbd';
  const isChoice = isTbd && byId('create-question-type').value === 'choice';
  byId('tbd-fields').hidden = !isTbd;
  byId('choice-fields').hidden = !isChoice;
}

function openCreateDialog(origin = null) {
  resetCreateForm();
  clearDialogMessages('create');
  openDialog(byId('create-dialog'), origin || document.activeElement, byId('create-content'));
}

async function createEntry(event) {
  event.preventDefault();
  const type = byId('create-kind').value;
  const message = byId('create-content').value.trim();
  const targetRepo = byId('create-target').value.trim();
  const choiceValues = byId('create-choices').value.split(/\\r?\\n/).map(item => item.trim()).filter(Boolean);
  setFieldError(byId('create-content'), byId('create-content-error'), message ? '' : '本文を入力してください。');
  setFieldError(byId('create-target'), byId('create-target-error'), targetRepo ? '' : '対象リポジトリを入力してください。');
  const choiceInvalid = type === 'tbd' && byId('create-question-type').value === 'choice' && choiceValues.length < 2;
  setFieldError(byId('create-choices'), byId('create-choices-error'), choiceInvalid ? '選択肢を2件以上入力してください。' : '');
  if (firstInvalid([byId('create-content'), byId('create-target'), byId('create-choices')])) return;
  const payload = {type, messages: [message], target_repo: targetRepo};
  const source = byId('create-source').value.trim();
  if (source) payload.source = source;
  if (type === 'tbd') {
    const scope = byId('create-scope').value.trim();
    if (scope) payload.scope = scope;
    payload.question_type = byId('create-question-type').value;
    if (payload.question_type === 'choice') payload.choices = choiceValues;
  }
  clearDialogMessages('create');
  try {
    const result = await runPending('create', {
      container: byId('create-form'), button: byId('create-submit-button'), busyLabel: '追加中'
    }, () => api('/api/entries', {method: 'POST', body: JSON.stringify(payload)}));
    const filename = result.filenames?.[0];
    closeDialog(byId('create-dialog'));
    await clearFilters({load: false});
    await loadTargetRepos();
    await loadEntries({announce: true});
    const created = entries.find(entry => entry.filename === filename);
    if (created) await selectEntry(created, byId('create-button'));
    deliverOperationMessage(filename ? `${filename}を追加しました。` : '項目を追加しました。');
  } catch (error) {
    deliverOperationMessage(`項目を追加できませんでした。 ${error.message}`, true);
    if (byId('create-dialog').open) byId('create-content').focus();
  }
}

function openDeleteDialog() {
  if (!currentEntry) return;
  deleteDialogEntrySnapshot = deleteEntrySnapshot(currentEntry);
  clearDialogMessages('delete');
  byId('delete-target').textContent = currentEntry.filename;
  byId('delete-state').textContent = `${currentEntry.kind || 'unknown'} / ${currentEntry.state || 'unknown'}`;
  byId('delete-state').dataset.state = currentEntry.state;
  byId('delete-target-repo').textContent = currentEntry.target_repo || '—';
  byId('delete-summary').textContent = currentEntry.summary || '—';
  byId('force-delete-row').hidden = currentEntry.state !== 'processing';
  byId('force-delete-confirmation').checked = false;
  setFieldError(byId('force-delete-confirmation'), byId('delete-error'), '');
  openDialog(byId('delete-dialog'), byId('delete-button'), byId('delete-close-button'));
}

async function deleteEntry(event) {
  event.preventDefault();
  if (!currentEntry) return;
  const force = byId('force-delete-confirmation').checked;
  if (currentEntry.state === 'processing' && !force) {
    setFieldError(byId('force-delete-confirmation'), byId('delete-error'), '処理中の項目を削除するには確認が必要です。');
    byId('force-delete-confirmation').focus();
    return;
  }
  const key = entryKey(currentEntry);
  const payload = {
    filenames: [currentEntry.filename],
    state: currentEntry.state,
    expected_content: currentEntry.content,
    force
  };
  clearDialogMessages('delete');
  try {
    await runPending('delete', {
      container: byId('delete-form'), button: byId('delete-submit-button'), busyLabel: '削除中'
    }, () => api('/api/entries/remove', {method: 'POST', body: JSON.stringify(payload)}));
    if (byId('delete-dialog').open) closeDeleteDialog();
    await loadEntries();
    if (!entries.some(entry => entryKey(entry) === key)) {
      if (byId('detail-dialog').open || currentEntry) closeDetailDialog();
      else detailReturnTarget().focus();
    } else {
      byId('edit-button').hidden = true;
      byId('answer-button').hidden = true;
      byId('delete-button').hidden = true;
    }
    deliverOperationMessage(`${key}を削除しました。`);
  } catch (error) {
    const failure = `${key}を削除できませんでした。 ${error.message}`;
    if (error.payload?.code === 'edit_conflict' && byId('detail-dialog').open) {
      invalidateDeleteConfirmation();
      detailRefreshRequired = true;
      syncDetailMutationAvailability();
      setTextMessage('detail-alert', `${failure} ${DELETE_CONFLICT_MESSAGE}`);
      byId('detail-dialog-body').focus();
    } else {
      deliverOperationMessage(failure, true);
      if (byId('delete-dialog').open) byId('delete-close-button').focus();
    }
  }
}

async function synchronizeAndLoad() {
  const payload = {};
  setTextMessage('sync-result', '');
  try {
    await runPending('sync', {
      container: document.querySelector('.app-header'), button: byId('refresh-button'), busyLabel: '同期中'
    }, () => api('/api/sync', {method: 'POST', body: JSON.stringify(payload)}));
    setTextMessage('sync-result', 'Git同期が完了しました。');
  } catch (error) {
    setTextMessage('sync-result', `Git同期に失敗しました。ローカル内容を表示中です。 ${error.message}`);
  }
  await loadTargetRepos();
  await loadEntries({announce: true});
}

async function handleFilterChange({reloadRepos = false} = {}) {
  syncFilterDependencies();
  const requestedState = byId('state-filter').value;
  if (reloadRepos && !await loadTargetRepos() && byId('state-filter').value !== requestedState) return;
  await loadEntries({announce: true});
}

async function reloadFromExternalChange() {
  void refreshKnownTbds({notify: true}).catch((error) => {
    setTextMessage('global-error', error.message);
  });
  await loadTargetRepos();
  await loadEntries({announce: false});
  await reloadOpenDetailFromExternalChange();
}

function attachDialogCloseHandlers(dialogId, closeButtonId, closeHandler = null) {
  const dialog = byId(dialogId);
  const close = closeHandler || (() => closeDialog(dialog));
  byId(closeButtonId).addEventListener('click', close);
  dialog.addEventListener('cancel', event => {
    event.preventDefault();
    close();
  });
}

function bindEvents() {
  byId('refresh-button').addEventListener('click', synchronizeAndLoad);
  byId('notification-button').addEventListener('click', () => { void enableNotifications(); });
  byId('create-button').addEventListener('click', event => openCreateDialog(event.currentTarget));
  byId('empty-create-button').addEventListener('click', event => openCreateDialog(event.currentTarget));
  byId('clear-filters-button').addEventListener('click', () => clearFilters());
  byId('empty-clear-button').addEventListener('click', () => clearFilters());
  byId('empty-all-states-button').addEventListener('click', () => {
    byId('state-filter').value = 'all';
    handleFilterChange({reloadRepos: true});
  });
  byId('kind-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('state-filter').addEventListener('change', () => { void handleFilterChange({reloadRepos: true}); });
  byId('answer-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('target-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('category-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('source-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('source-empty-filter').addEventListener('change', () => { void handleFilterChange(); });
  byId('search-input').addEventListener('input', () => {
    if (searchTimer !== null) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadEntries({announce: true}), 250);
  });
  byId('edit-button').addEventListener('click', enterEdit);
  byId('answer-button').addEventListener('click', enterAnswer);
  byId('save-entry-button').addEventListener('click', saveEntry);
  byId('save-answer-button').addEventListener('click', saveAnswer);
  byId('delete-button').addEventListener('click', openDeleteDialog);
  byId('create-kind').addEventListener('change', updateCreateFields);
  byId('create-question-type').addEventListener('change', updateCreateFields);
  byId('create-form').addEventListener('submit', createEntry);
  byId('delete-form').addEventListener('submit', deleteEntry);
  attachDialogCloseHandlers('detail-dialog', 'detail-close-button', closeDetailDialog);
  attachDialogCloseHandlers('create-dialog', 'create-close-button', () => closeDialog(byId('create-dialog')));
  attachDialogCloseHandlers('delete-dialog', 'delete-close-button', closeDeleteDialog);
}

let initialization = Promise.resolve();

function initializeApp() {
  const eventSource = new EventSource(BASE_PATH + '/api/events');
  eventSource.addEventListener('open', () => { byId('connection-status').textContent = '自動更新に接続済み'; });
  eventSource.addEventListener('error', () => { byId('connection-status').textContent = '自動更新を再接続中'; });
  eventSource.addEventListener('changed', () => { void initialization.then(reloadFromExternalChange); });
  syncFilterDependencies();
  syncNotificationButton();
  initialization = synchronizeAndLoad()
    .then(() => refreshKnownTbds({notify: false}))
    .catch((error) => {
      setTextMessage('global-error', error.message);
    });
}

bindEvents();
initializeApp();
"""
