"""`atk serve`の自己完結型フロントエンド資産。"""

import base64

# ruff: noqa: E501

THEME_COLOR = "#3157d5"
# タブ識別とPWAアイコンの双方でSSOTにするため、faviconはインラインSVGを単一定数で保持する。
# 白い縁取りを背景レイヤーとして重ね、黒系・白系・青系いずれのタイトルバー背景でも輪郭を識別可能にする。
# ベクターで配布するためPWAの192x192/512x512要件も1ファイルで満たせる。
FAVICON_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none">
  <path stroke="none" d="M0 0h24v24H0z" fill="none"/>
  <!-- 白い縁取り（背景レイヤー） -->
  <g stroke="white" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" fill="white">
    <path d="M4 13h3l3 3h4l3 -3h3"/>
    <path d="M4 13v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-4l-3 -8a2 2 0 0 0 -2 -1h-6a2 2 0 0 0 -2 1z"/>
  </g>
  <!-- 本来のストローク色 -->
  <g stroke="#3157d5" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none">
    <path d="M4 13h3l3 3h4l3 -3h3"/>
    <path d="M4 13v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-4l-3 -8a2 2 0 0 0 -2 -1h-6a2 2 0 0 0 -2 1z"/>
  </g>
</svg>
"""
# PNGアイコンはmanifestの宣言からSVGへ移行した後も保持する。
# 既存の配信URLは長期キャッシュ指定で配布済みであり、インストール済みPWAが
# 当該URLを参照し続けるため、定数と配信ルートを残して404を避ける。
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
  <!-- Basic Auth配下でもmanifestへ認証情報を送る。 -->
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
      <button id="refresh-button" class="button-secondary" type="button">再読込</button>
      <button id="create-button" class="button-primary" type="button">新規追加</button>
    </div>
  </header>

  <div id="global-error" class="global-error" role="alert" hidden></div>

  <main class="app-layout">
    <aside class="filters card" aria-label="フィルター条件">
      <h2>フィルター</h2>
      <label for="search-input">検索</label>
      <input id="search-input" type="search" placeholder="本文やファイル名を検索">

      <div class="filter-grid">
        <label for="kind-filter">種別</label>
        <select id="kind-filter">
          <option value="all">すべて</option>
          <option value="feedback">フィードバック</option>
          <option value="tbd">確認事項</option>
        </select>

        <label for="state-filter">状態</label>
        <select id="state-filter">
          <option value="active">対応中</option>
          <option value="all">すべて</option>
          <option value="inbox">未処理</option>
          <option value="processing">処理中</option>
          <option value="adopted">採用済み</option>
          <option value="rejected">不採用</option>
        </select>

        <label for="answer-filter">回答状況</label>
        <select id="answer-filter">
          <option value="all">すべて</option>
          <option value="no">未回答</option>
          <option value="yes">回答済み</option>
        </select>
      </div>

      <section class="additional-filters" aria-labelledby="additional-filters-heading">
        <h3 id="additional-filters-heading">追加条件</h3>
        <div class="filter-grid">
          <label for="target-filter">対象リポジトリ</label>
          <select id="target-filter">
            <option value="">すべて</option>
          </select>
          <label for="category-filter">カテゴリ</label>
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
        <span id="entry-count" class="secondary-text">0件</span>
      </div>
      <div id="loading-indicator" class="loading-state" role="status" hidden>読み込んでいます</div>
      <div class="entry-columns" aria-hidden="true">
        <span>ファイル名</span>
        <span>対象リポジトリ</span>
        <span>種別・状態・回答状況</span>
        <span>更新日時</span>
        <span>要約</span>
      </div>
      <ul id="entry-list" class="entry-list" aria-label="エントリ一覧"></ul>
      <div id="other-entry-group" class="entry-group" hidden>
        <div class="pane-heading">
          <h2 id="other-entry-heading">その他一覧</h2>
          <span id="other-entry-count" class="secondary-text">0件</span>
        </div>
        <div class="entry-columns" aria-hidden="true">
          <span>ファイル名</span>
          <span>対象リポジトリ</span>
          <span>種別・状態・回答状況</span>
          <span>更新日時</span>
          <span>要約</span>
        </div>
        <ul id="other-entry-list" class="entry-list" aria-label="その他のエントリ一覧"></ul>
      </div>
      <div id="empty-state" class="empty-state" hidden>
        <p>条件に一致する項目はありません。</p>
        <button id="empty-create-button" class="button-primary" type="button">最初の項目を追加</button>
      </div>
    </section>

  </main>

  <dialog id="detail-dialog" class="detail-dialog" aria-labelledby="detail-heading">
    <article class="detail-pane">
      <div class="detail-dialog-heading">
        <h2 id="detail-heading">詳細</h2>
        <button id="detail-close-button" class="button-secondary" type="button">閉じる</button>
      </div>

      <section id="detail-view" class="detail-view" hidden>
        <div class="detail-summary">
          <div class="detail-title-row">
            <h3 id="detail-filename"></h3>
            <span id="detail-state" class="state-badge"></span>
          </div>
          <dl id="detail-metadata" class="metadata"></dl>
          <p id="readonly-notice" class="readonly-notice" hidden>この項目は完了しているため、読取り専用です。</p>
        </div>
        <div class="detail-body">
          <h3>本文</h3>
          <div id="detail-content" class="entry-content markdown-body"></div>
        </div>
        <div id="detail-actions" class="detail-actions">
          <button id="edit-button" class="button-primary" type="button">編集</button>
          <button id="answer-button" class="button-primary" type="button" hidden>回答</button>
          <button id="delete-button" class="button-danger" type="button">削除</button>
        </div>
      </section>

      <section id="edit-panel" class="edit-panel" hidden>
        <h3>編集</h3>
        <label for="edit-content">本文</label>
        <textarea id="edit-content" aria-describedby="edit-content-error" required></textarea>
        <p id="edit-content-error" class="inline-error" hidden></p>
        <div class="form-actions">
          <button id="save-entry-button" class="button-primary" type="button">本文を保存</button>
          <button id="cancel-edit-button" class="button-secondary" type="button">中止</button>
        </div>

      </section>

      <section id="answer-panel" class="answer-panel" hidden>
        <h3>回答</h3>
        <label for="answer-input">確認事項への回答</label>
        <textarea id="answer-input" aria-describedby="answer-input-error" required></textarea>
        <p id="answer-input-error" class="inline-error" hidden></p>
        <div class="form-actions">
          <button id="save-answer-button" class="button-primary" type="button">回答を保存</button>
          <button id="cancel-answer-button" class="button-secondary" type="button">中止</button>
        </div>
      </section>
    </article>
  </dialog>

  <dialog id="create-dialog" aria-labelledby="create-dialog-heading">
    <form id="create-form" method="dialog" novalidate>
      <h2 id="create-dialog-heading">新規追加</h2>
      <label for="create-kind">種別</label>
      <select id="create-kind" name="type">
        <option value="feedback">フィードバック</option>
        <option value="tbd">確認事項</option>
      </select>

      <label for="create-content">本文</label>
      <textarea id="create-content" name="message" aria-describedby="create-content-error" required></textarea>
      <p id="create-content-error" class="inline-error" hidden></p>

      <label for="create-target">対象リポジトリ</label>
      <input id="create-target" name="target_repo" list="repo-options"
             aria-describedby="create-target-error" required>
      <datalist id="repo-options"></datalist>
      <p id="create-target-error" class="inline-error" hidden></p>

      <label for="create-source">投入元</label>
      <input id="create-source" name="source">

      <div id="tbd-fields" hidden>
        <label for="create-scope">確認範囲</label>
        <input id="create-scope" name="scope">
        <label for="create-question-type">回答形式</label>
        <select id="create-question-type" name="question_type">
          <option value="free-form">自由記述</option>
          <option value="yes-no">はい／いいえ</option>
          <option value="choice">選択式</option>
        </select>
        <div id="choice-fields" hidden>
          <label for="create-choices">選択肢（1行1件）</label>
          <textarea id="create-choices" name="choices" aria-describedby="create-choices-error"></textarea>
          <p id="create-choices-error" class="inline-error" hidden></p>
        </div>
      </div>

      <div class="form-actions">
        <button class="button-primary" type="submit">追加</button>
        <button id="cancel-create-button" class="button-secondary" type="button">中止</button>
      </div>
    </form>
  </dialog>

  <dialog id="delete-dialog" aria-labelledby="delete-dialog-heading">
    <form id="delete-form" method="dialog">
      <h2 id="delete-dialog-heading">削除の確認</h2>
      <p>対象: <strong id="delete-target"></strong></p>
      <p>状態: <span id="delete-state" class="state-badge"></span></p>
      <label id="force-delete-row" class="force-confirmation" hidden>
        <input id="force-delete-confirmation" type="checkbox">
        処理中の項目を強制的に削除する
      </label>
      <p id="delete-error" class="inline-error" hidden></p>
      <div class="form-actions">
        <button class="button-danger" type="submit">削除する</button>
        <button id="cancel-delete-button" class="button-secondary" type="button">中止</button>
      </div>
    </form>
  </dialog>

  <dialog id="unsaved-dialog" aria-labelledby="unsaved-dialog-heading">
    <form method="dialog">
      <h2 id="unsaved-dialog-heading">未保存の変更</h2>
      <p>入力内容を破棄しますか。</p>
      <div class="form-actions">
        <button id="discard-changes-button" class="button-danger" type="button">破棄する</button>
        <button id="continue-editing-button" class="button-secondary" type="button">編集を続ける</button>
      </div>
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
  --color-border: #d9dfE8;
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
  --font-size-body: 1rem;
  --font-size-secondary: 0.875rem;
  --font-size-heading: 1.25rem;
}

* {
  box-sizing: border-box;
}

html {
  scroll-behavior: smooth;
}

body {
  margin: 0;
  color: var(--color-text);
  background: var(--color-background);
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: var(--font-size-body);
  line-height: 1.55;
}

button,
input,
select,
textarea {
  max-width: 100%;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  font: inherit;
}

input,
select,
textarea {
  width: 100%;
  padding: 0.7rem 0.8rem;
  color: var(--color-text);
  background: var(--color-surface);
}

textarea {
  min-height: 24rem;
  resize: vertical;
}

button {
  padding: 0.65rem 1rem;
  color: var(--color-text);
  background: var(--color-normal);
  cursor: pointer;
}

button:hover {
  background: var(--color-normal-hover);
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 2px;
}

[hidden] {
  display: none !important;
}

.app-header {
  position: sticky;
  z-index: 10;
  top: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-2) max(var(--space-3), calc((100vw - 1500px) / 2));
  border-bottom: 1px solid var(--color-border);
  background: rgb(255 255 255 / 0.96);
  box-shadow: 0 0.25rem 1rem rgb(30 45 75 / 0.06);
}

.header-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.app-header h1,
.pane-heading h2,
.filters h2,
.detail-dialog-heading h2 {
  margin: 0;
}

.app-header h1 {
  font-size: 1.375rem;
}

.connection-status,
.secondary-text,
.entry-meta,
.readonly-notice {
  color: var(--color-secondary-text);
  font-size: var(--font-size-secondary);
}

.header-actions,
.form-actions,
.detail-actions,
.pane-heading,
.detail-dialog-heading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.header-actions button {
  padding: var(--space-1) var(--space-2);
}

.pane-heading,
.detail-dialog-heading {
  justify-content: space-between;
}

.button-primary {
  border-color: var(--color-primary);
  color: #ffffff;
  background: var(--color-primary);
}

.button-primary:hover {
  background: var(--color-primary-hover);
}

.button-secondary {
  background: var(--color-normal);
}

.button-danger {
  border-color: var(--color-danger);
  color: #ffffff;
  background: var(--color-danger);
}

.button-danger:hover {
  background: var(--color-danger-hover);
}

.global-error {
  max-width: 1500px;
  margin: var(--space-3) auto 0;
  padding: var(--space-3);
  border: 1px solid #f2b8b5;
  border-radius: var(--radius-small);
  color: var(--color-danger);
  background: #fff1f0;
  white-space: pre-wrap;
}

.app-layout {
  display: grid;
  grid-template-columns: minmax(15rem, 0.25fr) minmax(0, 1fr);
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

.filters {
  display: grid;
  gap: var(--space-2);
}

.filter-grid {
  display: grid;
  grid-template-columns: minmax(7rem, auto) 1fr;
  gap: var(--space-2);
  align-items: center;
}

.additional-filters {
  margin-top: var(--space-2);
}

.additional-filters h3 {
  margin: 0 0 var(--space-2) 0;
  font-size: inherit;
  font-weight: 600;
}

.checkbox-field {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  grid-column: 2;
  font-size: inherit;
  font-weight: normal;
}

.checkbox-field input {
  width: auto;
}

.entry-pane {
  min-height: 34rem;
}

.entry-columns,
.entry-select {
  display: grid;
  grid-template-columns:
    minmax(12rem, 1.6fr)
    minmax(10rem, 1fr)
    minmax(11rem, 1.2fr)
    minmax(7rem, 0.7fr)
    minmax(16rem, 2fr);
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

.entry-group {
  margin-top: var(--space-5);
}

.entry-row {
  margin: 0;
}

.entry-select {
  width: 100%;
  padding: var(--space-2);
  text-align: left;
  background: var(--color-surface);
}

.entry-select:hover {
  border-color: #aeb9cd;
  background: #f7f9fd;
}

.entry-select[aria-current="true"] {
  border-color: var(--color-primary);
  background: #eef2ff;
  box-shadow: 0 0 0 1px var(--color-primary);
}

.entry-cell {
  min-width: 0;
  overflow-wrap: anywhere;
}

.entry-cell::before {
  display: none;
}

.entry-cell.filename-cell {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.entry-cell.target-repo-cell {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.entry-cell.status-cell {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  align-items: center;
}

.entry-cell.time-cell {
  display: flex;
  flex-direction: column;
  gap: 0;
  font-size: var(--font-size-secondary);
}

.entry-cell.time-cell time {
  line-height: 1.2;
}

.entry-kind {
  font-weight: 700;
}

.state-badge {
  display: inline-flex;
  width: fit-content;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  color: #344054;
  background: #e9edf4;
  font-size: var(--font-size-secondary);
  font-weight: 700;
}

.state-badge[data-state="inbox"] {
  color: #175cd3;
  background: #eff8ff;
}

.state-badge[data-state="processing"] {
  color: var(--color-warning);
  background: #fffaeb;
}

.state-badge[data-state="adopted"] {
  color: var(--color-success);
  background: #ecfdf3;
}

.state-badge[data-state="rejected"] {
  color: var(--color-danger);
  background: #fff1f0;
}

.loading-state,
.empty-state {
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

.detail-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.detail-title-row h3 {
  overflow-wrap: anywhere;
}

.detail-view {
  display: grid;
  grid-template-columns: minmax(18rem, 0.35fr) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.detail-view[hidden] {
  display: none;
}

.detail-summary,
.detail-body {
  min-width: 0;
}

.detail-body > h3 {
  margin-top: 0;
}

.detail-actions {
  grid-column: 1 / -1;
}

.metadata {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--space-1) var(--space-3);
}

.metadata dt {
  font-weight: 700;
}

.metadata dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.entry-content {
  max-height: 32rem;
  overflow: auto;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: #f8fafc;
  overflow-wrap: anywhere;
}

.markdown-body > :first-child {
  margin-top: 0;
}

.markdown-body > :last-child {
  margin-bottom: 0;
}

.markdown-body h1,
.markdown-body h2,
.markdown-body h3,
.markdown-body h4,
.markdown-body ul,
.markdown-body ol,
.markdown-body pre,
.markdown-body blockquote,
.markdown-body table {
  margin-block: var(--space-3);
}

.markdown-body pre,
.markdown-body code {
  font-family: ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace;
}

.frontmatter pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.markdown-body pre {
  overflow: auto;
  padding: var(--space-2);
  border-radius: var(--radius-small);
  background: #e9eef5;
}

.markdown-body blockquote {
  margin-inline: 0;
  padding-left: var(--space-3);
  border-left: 0.25rem solid var(--color-border);
  color: var(--color-secondary-text);
}

.markdown-body table {
  border-collapse: collapse;
}

.markdown-body th,
.markdown-body td {
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
}

.detail-actions {
  justify-content: space-between;
  margin-top: var(--space-4);
}

.edit-panel,
.answer-panel {
  display: grid;
  gap: var(--space-2);
}

.answer-panel {
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}

.inline-error {
  margin: 0;
  color: var(--color-danger);
  font-size: var(--font-size-secondary);
}

dialog {
  width: min(38rem, calc(100% - 2rem));
  max-height: calc(100vh - 2rem);
  overflow: auto;
  padding: var(--space-4);
  border: 0;
  border-radius: var(--radius-medium);
  box-shadow: 0 1.5rem 4rem rgb(20 30 50 / 0.25);
}

.detail-dialog {
  width: min(80rem, calc(100% - 2rem));
}

.detail-pane {
  min-width: 0;
}

dialog::backdrop {
  background: rgb(15 23 42 / 0.55);
}

dialog form {
  display: grid;
  gap: var(--space-2);
}

.force-confirmation {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-small);
  background: #fff7ed;
}

.force-confirmation input {
  width: auto;
}

.toast {
  position: fixed;
  z-index: 20;
  right: var(--space-4);
  bottom: var(--space-4);
  max-width: min(28rem, calc(100% - 2rem));
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-small);
  color: #ffffff;
  background: #1f2937;
  box-shadow: var(--shadow-card);
  animation: toast-in 0.18s ease-out;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes toast-in {
  from {
    opacity: 0;
    transform: translateY(0.5rem);
  }
}

@media (max-width: 1280px) {
  .entry-columns {
    display: none;
  }

  .entry-select {
    grid-template-columns: 1fr;
    gap: var(--space-1);
    padding: var(--space-3);
  }

  .entry-cell {
    display: grid;
    grid-template-columns: minmax(7rem, 40%) minmax(0, 1fr);
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
  .app-header,
  .header-actions,
  .form-actions,
  .detail-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .app-layout {
    grid-template-columns: 1fr;
    padding: var(--space-2);
  }

  .entry-pane {
    min-height: 0;
  }

  .detail-dialog {
    width: calc(100% - 1rem);
    max-height: calc(100vh - 1rem);
    padding: var(--space-3);
  }

  .detail-dialog-heading {
    align-items: stretch;
    flex-direction: column;
  }

  .detail-view {
    grid-template-columns: 1fr;
    gap: var(--space-3);
  }

  .detail-actions {
    grid-column: auto;
  }

  .filter-grid {
    grid-template-columns: 1fr;
  }

  .checkbox-field {
    grid-column: auto;
  }

  button,
  input,
  select,
  textarea {
    width: 100%;
  }

  textarea {
    min-height: 16rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  html {
    scroll-behavior: auto;
  }

  *,
  *::before,
  *::after {
    animation: none !important;
    transition: none !important;
  }
}"""

JS = """const BASE_PATH=__BASE_PATH_JS__;
const KIND_LABELS = {feedback: 'フィードバック', tbd: '確認事項'};
const STATE_LABELS = {inbox: '未処理', processing: '処理中', adopted: '採用済み', rejected: '不採用'};
const ANSWER_LABELS = {true: '回答済み', false: '未回答', null: '対象外'};
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
let visibleEntries = [];
let currentEntry = null;
let editing = false;
let editMode = 'body';
let loading = false;
let editBaseline = '';
let answerBaseline = '';
let detailOrigin = null;
let detailOriginKey = '';
let detailRequestGeneration = 0;
let listRequestGeneration = 0;
let pendingListRequests = 0;
let searchTimer = null;
let pendingDiscardAction = null;
let toastTimer = null;

const byId = id => document.getElementById(id);

async function api(path, options = {}) {
  showError('');
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

function setLoading(value) {
  loading = value;
  byId('loading-indicator').hidden = !value;
  byId('refresh-button').disabled = value;
  byId('entry-list').setAttribute('aria-busy', String(value));
  byId('other-entry-list').setAttribute('aria-busy', String(value));
}

function showToast(message) {
  const toast = byId('toast');
  toast.textContent = message;
  toast.hidden = false;
  if (toastTimer !== null) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 4000);
}

function showError(message) {
  const area = byId('global-error');
  area.textContent = message instanceof Error ? message.message : String(message || '');
  area.hidden = !area.textContent;
}

function setFieldError(inputId, message) {
  const error = byId(`${inputId}-error`);
  if (!error) return;
  error.textContent = message;
  error.hidden = !message;
  byId(inputId).setAttribute('aria-invalid', String(Boolean(message)));
}

function requireValue(inputId, label) {
  const value = byId(inputId).value.trim();
  setFieldError(inputId, value ? '' : `${label}を入力してください。`);
  return value;
}

function clearFieldError(event) {
  setFieldError(event.currentTarget.id, '');
}

function entryKey(entry) {
  return entry ? entry.filename : '';
}

function labelFor(field, value) {
  if (field === 'kind') return KIND_LABELS[value] || value || 'なし';
  if (field === 'state') return STATE_LABELS[value] || value || 'なし';
  if (field === 'answered') return ANSWER_LABELS[String(value)] || '対象外';
  return value || 'なし';
}

function formatUpdatedAt(value, part = 'datetime') {
  if (!value) return '更新日時なし';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const options = {timeZone: 'Asia/Tokyo'};
  if (part === 'date') return date.toLocaleDateString('ja-JP', options);
  if (part === 'time') return date.toLocaleTimeString('ja-JP', options);
  return date.toLocaleString('ja-JP', options);
}

function applyClientFilters() {
  visibleEntries = entries;
  renderList();
}

function renderEntry(entry) {
  const item = document.createElement('li');
  item.className = 'entry-row';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'entry-select';
  button.dataset.key = entryKey(entry);
  if (button.dataset.key === detailOriginKey) detailOrigin = button;
  button.setAttribute('aria-current', String(entryKey(entry) === entryKey(currentEntry)));
  const cell = (label, value, className = '') => {
    const node = document.createElement('span');
    node.className = `entry-cell ${className}`.trim();
    node.dataset.label = label;
    node.textContent = value || 'なし';
    return node;
  };

  const filename = cell('ファイル名', entry.filename, 'filename-cell');
  const targetRepo = cell('対象リポジトリ', entry.target_repo, 'target-repo-cell');

  const statusCell = document.createElement('span');
  statusCell.className = 'entry-cell status-cell';
  statusCell.dataset.label = '種別・状態・回答状況';

  const kindBadge = document.createElement('span');
  kindBadge.className = 'state-badge';
  kindBadge.textContent = KIND_LABELS[entry.kind] || entry.kind;

  const stateBadge = document.createElement('span');
  stateBadge.className = 'state-badge';
  stateBadge.dataset.state = entry.state;
  stateBadge.textContent = STATE_LABELS[entry.state] || entry.state;

  const answeredLabel = labelFor('answered', entry.answered);
  if (entry.kind === 'tbd' && entry.answered !== null) {
    const answeredBadge = document.createElement('span');
    answeredBadge.className = 'state-badge';
    answeredBadge.textContent = answeredLabel;
    statusCell.append(kindBadge, stateBadge, answeredBadge);
  } else {
    statusCell.append(kindBadge, stateBadge);
  }

  const timeCell = document.createElement('span');
  timeCell.className = 'entry-cell time-cell';
  timeCell.dataset.label = '更新日時';

  const fullDateTimeStr = formatUpdatedAt(entry.updated_at);
  let dateStr = fullDateTimeStr;
  let timeStr = '';

  if (entry.updated_at) {
    const date = new Date(entry.updated_at);
    if (!Number.isNaN(date.getTime())) {
      dateStr = formatUpdatedAt(entry.updated_at, 'date');
      timeStr = formatUpdatedAt(entry.updated_at, 'time');
    } else {
      dateStr = String(entry.updated_at);
    }
  }

  const timeEl = document.createElement('time');
  timeEl.dateTime = entry.updated_at || '';
  timeEl.setAttribute('aria-label', fullDateTimeStr);

  const dateLine = document.createElement('div');
  dateLine.textContent = dateStr;

  const timeLine = document.createElement('div');
  timeLine.textContent = timeStr;

  timeEl.append(dateLine, timeLine);
  timeCell.append(timeEl);

  const summary = cell('要約', entry.summary);

  const cells = [filename, targetRepo, statusCell, timeCell, summary];

  const ariaLabels = [
    `ファイル名: ${entry.filename || 'なし'}`,
    `対象リポジトリ: ${entry.target_repo || 'なし'}`,
    `種別・状態・回答状況: ${[KIND_LABELS[entry.kind] || entry.kind, STATE_LABELS[entry.state] || entry.state, (entry.kind === 'tbd' && entry.answered !== null ? labelFor('answered', entry.answered) : '')].filter(Boolean).join('、')}`,
    `更新日時: ${fullDateTimeStr}`,
    `要約: ${entry.summary || 'なし'}`
  ];
  button.setAttribute('aria-label', ariaLabels.join('、'));
  button.append(...cells);
  button.addEventListener('click', () => selectEntry(entry, button));
  item.append(button);
  return item;
}

function renderList() {
  const unfinishedTbdEntries = visibleEntries.filter(entry => (
    entry.kind === 'tbd' && entry.answered === false && ACTIVE_STATES.has(entry.state)
  ));
  const hasUnfinishedTbd = unfinishedTbdEntries.length > 0;
  const primaryEntries = hasUnfinishedTbd ? unfinishedTbdEntries : visibleEntries;
  const otherEntries = hasUnfinishedTbd
    ? visibleEntries.filter(entry => !unfinishedTbdEntries.includes(entry))
    : [];
  byId('entry-heading').textContent = hasUnfinishedTbd ? '未完了TBD' : '一覧';
  byId('entry-count').textContent = `${primaryEntries.length}件`;
  byId('other-entry-count').textContent = `${otherEntries.length}件`;
  byId('other-entry-group').hidden = !hasUnfinishedTbd;
  byId('empty-state').hidden = loading || visibleEntries.length > 0;
  byId('entry-list').replaceChildren(...primaryEntries.map(renderEntry));
  byId('other-entry-list').replaceChildren(...otherEntries.map(renderEntry));
}

function renderMetadata(entry) {
  const nodes = [];
  for (const [field, label] of METADATA_FIELDS) {
    const term = document.createElement('dt');
    const description = document.createElement('dd');
    term.textContent = label;
    description.textContent = labelFor(field, entry[field]);
    nodes.push(term, description);
  }
  byId('detail-metadata').replaceChildren(...nodes);
}

function displayEntry(entry, preserveForm = false) {
  currentEntry = entry;
  byId('detail-view').hidden = editing;
  byId('edit-panel').hidden = !editing || editMode !== 'body';
  byId('answer-panel').hidden = !editing || editMode !== 'answer';
  byId('detail-filename').textContent = entry.filename;
  byId('detail-state').textContent = STATE_LABELS[entry.state] || entry.state;
  byId('detail-state').dataset.state = entry.state;
  const detailContent = byId('detail-content');
  detailContent.innerHTML = entry.content_html;
  renderMetadata(entry);

  const active = ACTIVE_STATES.has(entry.state);
  byId('detail-actions').hidden = !active;
  byId('readonly-notice').hidden = active;
  byId('edit-button').hidden = !active;
  byId('answer-button').hidden = !(active && entry.kind === 'tbd' && entry.answered === false);
  byId('delete-button').hidden = !active;
  if (!preserveForm) {
    byId('edit-content').value = entry.content;
    byId('answer-input').value = '';
    editBaseline = entry.content;
    answerBaseline = entry.content;
  }
  applyClientFilters();
}

async function renderDetail(entry, options = {}) {
  if (!entry) return;
  const requestKey = entryKey(entry);
  if (requestKey !== detailOriginKey) return;
  const requestGeneration = ++detailRequestGeneration;
  try {
    const payload = await api(`/api/entries/${entry.state}/${encodeURIComponent(entry.filename)}`);
    if (requestGeneration !== detailRequestGeneration || requestKey !== detailOriginKey) return;
    if (editing) {
      showError('外部で項目が更新されました。編集中の入力を保持しています。保存前に詳細を再読込してください。');
      return;
    }
    displayEntry(payload.entry, Boolean(options.preserveForm));
    if (options.open && !byId('detail-dialog').open) byId('detail-dialog').showModal();
  } catch (error) {
    if (requestGeneration !== detailRequestGeneration || requestKey !== detailOriginKey) return;
    if (error.status === 404 && options.closeWhenMissing) {
      closeDetailDialog();
      return;
    }
    showError(error);
  }
}

async function selectEntry(entry, origin) {
  if (hasUnsavedChanges() && entryKey(entry) !== entryKey(currentEntry)) {
    requestDiscard(() => {
      cancelEdit(true);
      selectEntry(entry, origin);
    });
    return;
  }
  if (editing && entryKey(entry) !== entryKey(currentEntry)) cancelEdit(true);
  detailOrigin = origin;
  detailOriginKey = entryKey(entry);
  await renderDetail(entry, {open: true});
}

function closeDetailDialog(force = false) {
  if (editing && !force) return requestDiscard(() => closeDetailDialog(true));
  if (force) editing = false;
  if (byId('detail-dialog').open) byId('detail-dialog').close();
  return true;
}

function resetDetailSelection() {
  currentEntry = null;
  editing = false;
  detailOrigin = null;
  renderList();
  if (detailOrigin) {
    detailOrigin.focus();
    detailOrigin = null;
  }
  detailOriginKey = '';
}

function enterEdit() {
  if (!currentEntry || !ACTIVE_STATES.has(currentEntry.state)) return;
  editing = true;
  editMode = 'body';
  editBaseline = currentEntry.content;
  answerBaseline = currentEntry.content;
  byId('edit-content').value = currentEntry.content;
  byId('answer-input').value = '';
  byId('detail-view').hidden = true;
  byId('edit-panel').hidden = false;
  byId('answer-panel').hidden = true;
  byId('edit-content').focus();
}

function enterAnswer() {
  if (!currentEntry || currentEntry.kind !== 'tbd' || currentEntry.answered !== false) return;
  editing = true;
  editMode = 'answer';
  answerBaseline = currentEntry.content;
  byId('answer-input').value = '';
  byId('detail-view').hidden = true;
  byId('edit-panel').hidden = true;
  byId('answer-panel').hidden = false;
  byId('answer-input').focus();
}

function cancelEdit(force = false) {
  if (!force && hasUnsavedChanges()) return requestDiscard(() => cancelEdit(true));
  editing = false;
  setFieldError('edit-content', '');
  setFieldError('answer-input', '');
  byId('edit-panel').hidden = true;
  byId('answer-panel').hidden = true;
  byId('detail-view').hidden = !currentEntry;
  if (currentEntry) {
    byId('edit-content').value = currentEntry.content;
    byId('answer-input').value = '';
  }
}

function hasUnsavedChanges() {
  if (!editing || !currentEntry) return false;
  if (editMode === 'answer') return byId('answer-input').value !== '';
  return byId('edit-content').value !== editBaseline;
}

function requestDiscard(action) {
  if (!hasUnsavedChanges()) {
    action();
    return true;
  }
  pendingDiscardAction = action;
  byId('unsaved-dialog').showModal();
  return false;
}

function discardChanges() {
  const action = pendingDiscardAction;
  pendingDiscardAction = null;
  byId('unsaved-dialog').close();
  if (action) action();
}

function continueEditing() {
  pendingDiscardAction = null;
  byId('unsaved-dialog').close();
  const inputId = editMode === 'answer' ? 'answer-input' : 'edit-content';
  byId(inputId).focus();
}

function conflictMessage(error) {
  if (error && error.payload && error.payload.code === 'edit_conflict') {
    showError('外部で項目が更新されました。入力内容を保持しています。詳細を再読込してから保存してください。');
    return true;
  }
  return false;
}

async function saveEntry() {
  if (!currentEntry || !editing || editMode !== 'body') return;
  const content = requireValue('edit-content', '本文');
  if (!content) return;
  try {
    await api(`/api/entries/${currentEntry.state}/${encodeURIComponent(currentEntry.filename)}`, {
      method: 'PUT',
      body: JSON.stringify({content, expected_content: editBaseline})
    });
    cancelEdit(true);
    showToast('本文を保存しました。');
    await loadEntries();
  } catch (error) {
    if (!conflictMessage(error)) showError(error);
  }
}

async function saveAnswer() {
  if (!currentEntry || !editing || editMode !== 'answer') return;
  const answer = requireValue('answer-input', '回答');
  if (!answer) return;
  try {
    await api('/api/entries/answer', {
      method: 'POST',
      body: JSON.stringify({filename: currentEntry.filename, answer, expected_content: answerBaseline})
    });
    cancelEdit(true);
    showToast('回答を保存しました。');
    await loadEntries();
  } catch (error) {
    if (!conflictMessage(error)) showError(error);
  }
}

function updateCreateFields() {
  const isTbd = byId('create-kind').value === 'tbd';
  byId('tbd-fields').hidden = !isTbd;
  byId('choice-fields').hidden = !(isTbd && byId('create-question-type').value === 'choice');
}

function openCreateDialog() {
  const target = byId('target-filter').value.trim();
  if (target && !byId('create-target').value) byId('create-target').value = target;
  updateCreateFields();
  byId('create-dialog').showModal();
  byId('create-content').focus();
}

function closeCreateDialog() {
  byId('create-dialog').close();
}

async function createEntry(event) {
  event.preventDefault();
  const message = requireValue('create-content', '本文');
  const targetRepo = requireValue('create-target', '対象リポジトリ');
  const kind = byId('create-kind').value;
  if (!message || !targetRepo) return;

  const body = {type: kind, messages: [message], target_repo: targetRepo};
  const source = byId('create-source').value.trim();
  if (source) body.source = source;
  if (kind === 'tbd') {
    body.scope = byId('create-scope').value.trim();
    body.question_type = byId('create-question-type').value;
    if (body.question_type === 'choice') {
      const choices = byId('create-choices').value.split('\\n').map(choice => choice.trim()).filter(Boolean);
      if (choices.length < 2) {
        setFieldError('create-choices', '選択肢を2件以上入力してください。');
        return;
      }
      body.choices = choices;
    }
  }

  try {
    await api('/api/entries', {method: 'POST', body: JSON.stringify(body)});
    byId('create-form').reset();
    updateCreateFields();
    closeCreateDialog();
    showToast('項目を追加しました。');
    await loadEntries();
  } catch (error) {
    showError(error);
  }
}

function openDeleteDialog() {
  if (!currentEntry || !ACTIVE_STATES.has(currentEntry.state)) return;
  byId('delete-target').textContent = currentEntry.filename;
  byId('delete-state').textContent = STATE_LABELS[currentEntry.state] || currentEntry.state;
  byId('delete-state').dataset.state = currentEntry.state;
  byId('force-delete-row').hidden = currentEntry.state !== 'processing';
  byId('force-delete-confirmation').checked = false;
  byId('delete-error').hidden = true;
  byId('delete-dialog').showModal();
}

async function removeEntry(event) {
  event.preventDefault();
  if (!currentEntry) return;
  const body = {filenames: [currentEntry.filename]};
  if (currentEntry.state === 'processing') {
    if (!byId('force-delete-confirmation').checked) {
      byId('delete-error').textContent = '強制削除の確認が必要です。';
      byId('delete-error').hidden = false;
      return;
    }
    body.force = true;
  }
  try {
    await api('/api/entries/remove', {method: 'POST', body: JSON.stringify(body)});
    byId('delete-dialog').close();
    editing = false;
    closeDetailDialog();
    showToast('項目を削除しました。');
    await loadEntries();
  } catch (error) {
    showError(error);
  }
}

function listQuery() {
  const query = new URLSearchParams();
  const fields = [
    ['type', 'kind-filter'],
    ['status', 'state-filter'],
    ['answered', 'answer-filter'],
    ['target_repo', 'target-filter'],
    ['category', 'category-filter'],
    ['source', 'source-filter']
  ];
  for (const [name, id] of fields) {
    const value = byId(id).value.trim();
    if (value) query.set(name, value);
  }
  if (byId('source-empty-filter').checked) {
    query.set('source_empty', 'true');
  }
  const search = byId('search-input').value.trim();
  if (search) query.set('q', search);
  return query.toString();
}

async function loadTargetRepos() {
  // 対象リポジトリの候補を既存エントリから取得し、フィルターと新規登録欄へ反映する。
  // フィルターは選択式とし、新規登録欄は未登録のリポジトリも入力できるよう補完候補に留める。
  let repos = [];
  try {
    const payload = await api('/api/repos');
    repos = Array.isArray(payload.repos) ? payload.repos : [];
  } catch {
    // 候補の取得失敗で一覧表示を止めない。既存の選択肢と選択値をそのまま残す。
    return;
  }
  const filter = byId('target-filter');
  const selected = filter.value;
  filter.replaceChildren();
  const blank = document.createElement('option');
  blank.value = '';
  blank.textContent = 'すべて';
  filter.append(blank);
  // 選択中の値が候補から消えた場合も選択状態を保つため、候補へ補う。
  const values = repos.includes(selected) || !selected ? repos : [selected, ...repos];
  for (const repo of values) {
    const option = document.createElement('option');
    option.value = repo;
    option.textContent = repo;
    filter.append(option);
  }
  filter.value = selected;
  const datalist = byId('repo-options');
  datalist.replaceChildren();
  for (const repo of repos) {
    const option = document.createElement('option');
    option.value = repo;
    datalist.append(option);
  }
}

async function loadEntries(options = {}) {
  const selectedFilename = currentEntry ? currentEntry.filename : '';
  const requestGeneration = ++listRequestGeneration;
  pendingListRequests += 1;
  setLoading(true);
  try {
    const payload = await api(`/api/entries?${listQuery()}`);
    if (requestGeneration !== listRequestGeneration) return;
    entries = payload.entries;
    applyClientFilters();
    const selected = entries.find(entry => entry.filename === selectedFilename);
    if (!selected) {
      if (byId('detail-dialog').open) closeDetailDialog();
      return;
    }
    if (editing) {
      if (options.fromSse) {
        showError('外部で項目が更新されました。編集中の入力を保持しています。保存前に詳細を再読込してください。');
      }
      applyClientFilters();
      return;
    }
    if (byId('detail-dialog').open) await renderDetail(selected, {closeWhenMissing: true});
  } catch (error) {
    if (requestGeneration !== listRequestGeneration) return;
    showError(error);
  } finally {
    pendingListRequests -= 1;
    setLoading(pendingListRequests > 0);
    if (requestGeneration === listRequestGeneration) renderList();
  }
}

async function synchronizeAndLoad() {
  let syncError = null;
  setLoading(true);
  try {
    await api('/api/sync', {method: 'POST'});
  } catch (error) {
    syncError = error;
  }
  await loadEntries();
  if (syncError) showError(syncError);
}

function closeTopmostDialog() {
  for (const dialog of [byId('unsaved-dialog'), byId('delete-dialog'), byId('create-dialog')]) {
    if (dialog.open) {
      dialog.close();
      return true;
    }
  }
  return false;
}

function bindEvents() {
  byId('refresh-button').addEventListener('click', synchronizeAndLoad);
  byId('create-button').addEventListener('click', openCreateDialog);
  byId('empty-create-button').addEventListener('click', openCreateDialog);
  byId('cancel-create-button').addEventListener('click', closeCreateDialog);
  byId('create-form').addEventListener('submit', createEntry);
  byId('create-kind').addEventListener('change', updateCreateFields);
  byId('create-question-type').addEventListener('change', updateCreateFields);
  byId('edit-button').addEventListener('click', enterEdit);
  byId('answer-button').addEventListener('click', enterAnswer);
  byId('cancel-edit-button').addEventListener('click', () => cancelEdit());
  byId('cancel-answer-button').addEventListener('click', () => cancelEdit());
  byId('save-entry-button').addEventListener('click', saveEntry);
  byId('save-answer-button').addEventListener('click', saveAnswer);
  byId('delete-button').addEventListener('click', openDeleteDialog);
  byId('delete-form').addEventListener('submit', removeEntry);
  byId('cancel-delete-button').addEventListener('click', () => byId('delete-dialog').close());
  byId('detail-close-button').addEventListener('click', () => closeDetailDialog());
  byId('detail-dialog').addEventListener('cancel', event => {
    if (hasUnsavedChanges()) {
      event.preventDefault();
      requestDiscard(() => closeDetailDialog(true));
    }
  });
  byId('detail-dialog').addEventListener('close', resetDetailSelection);
  byId('discard-changes-button').addEventListener('click', discardChanges);
  byId('continue-editing-button').addEventListener('click', continueEditing);

  byId('search-input').addEventListener('input', () => {
    listRequestGeneration += 1;
    if (searchTimer !== null) clearTimeout(searchTimer);
    searchTimer = setTimeout(loadEntries, 200);
  });
  for (const id of ['kind-filter', 'state-filter', 'answer-filter', 'target-filter', 'category-filter', 'source-filter']) {
    byId(id).addEventListener('change', () => loadEntries());
  }
  byId('source-empty-filter').addEventListener('change', (event) => {
    const sourceFilter = byId('source-filter');
    if (event.currentTarget.checked) {
      sourceFilter.value = '';
      sourceFilter.disabled = true;
    } else {
      sourceFilter.disabled = false;
    }
    loadEntries();
  });
  for (const id of ['create-content', 'create-target', 'create-choices', 'edit-content', 'answer-input']) {
    byId(id).addEventListener('input', clearFieldError);
  }

  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 's' && editing) {
      event.preventDefault();
      if (editMode === 'answer') saveAnswer();
      else saveEntry();
    } else if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const tagName = document.activeElement ? document.activeElement.tagName : '';
      if (!['INPUT', 'TEXTAREA', 'SELECT'].includes(tagName)) {
        event.preventDefault();
        byId('search-input').focus();
      }
    } else if (event.key === 'Escape') {
      if (!closeTopmostDialog() && editing && byId('detail-dialog').open) {
        event.preventDefault();
        closeDetailDialog();
      }
    }
  });
}

bindEvents();
const events = new EventSource(BASE_PATH + '/api/events');
events.addEventListener('open', () => {
  byId('connection-status').textContent = '接続済み';
});
events.addEventListener('changed', () => {
  loadEntries({fromSse: true});
  loadTargetRepos();
});
events.onerror = () => {
  byId('connection-status').textContent = '再接続中';
};
loadTargetRepos();
synchronizeAndLoad();"""
