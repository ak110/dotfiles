# atk serveの静的資産

`agent-toolkit/agent_toolkit/_atk/serve/static/`配下のCSS、HTML、JavaScriptでは、フォント（書体）、共通シェルのスタイル規則、JavaScriptの共通処理を一元的に定義する（努力目標）。画面ごとに再定義や上書きすると、3つの画面（WI、Plans、Sessions）の間で見た目や挙動にずれが生じる原因になる。一元定義に従えない事情がある場合は、その事情とずれによる不利益を比較して個別に判断する。

- 書体、フォントサイズ、行の高さ、文字間隔、文字色は`:root`のカスタムプロパティと`body`で一括して定義し、`#screen-wi`、`#screen-plans`、`#screen-sessions`の配下では再定義しない
- `#screen-*`の直下に本文装飾用のタグセレクタ（`a`、見出し、`p`、リスト、`table`、`code`、`pre`など）を直接書かない。Markdown本文やセッション本文の装飾は、`.markdown-body`を経由して適用する。なお、`main`、`aside`、および入力要素（`input`、`select`、`textarea`、`dialog`）については、前項のスタイル宣言を持つ場合に限ってこの対象に含める
- 共通シェル要素（`.app-header`、`.app-nav`、共通ダイアログ、`main > .toolbar`）には3画面共通のスタイル規則を適用し、画面ごとの個別の上書きは水平方向の余白（marginやpadding）だけにとどめる
- 3画面で共通して使う処理（SSEの購読と通知の振り分け、一覧の差分描画や選択状態の管理、前後移動、警告表示、ドロワーの開閉など）は`common.js`に集約し、画面ごとの差分は引数の関数で渡す。スクリプトは`index.html`からESモジュールとして読み込み、`BASE_PATH`などのリクエストごとに変わる値は`serve-bootstrap`などのJSONブロック経由で取得する
