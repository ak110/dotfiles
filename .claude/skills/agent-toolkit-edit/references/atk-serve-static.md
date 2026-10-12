# atk serveの静的資産

`agent-toolkit/agent_toolkit/_atk/serve/static/`配下のCSS・HTML・JavaScriptは、書体、共有シェルの規則、JavaScriptの共通処理を一元定義に保つ（努力目標）。画面ごとの再定義や上書きは3画面の体裁と挙動のずれを招く。沿えない事情がある場合は、その事情とずれの不利益を比べて判断する。

- 書体、文字サイズ、行の高さ、字間、文字色は`:root`のカスタムプロパティーと`body`で定義し、`#screen-wi`・`#screen-plans`・`#screen-sessions`配下で再定義しない
- `#screen-*`直下へ本文装飾の裸のタグセレクター（`a`・見出し・`p`・リスト・`table`・`code`・`pre`など）を書かない。Markdown本文とセッション本文の装飾は`.markdown-body`を経由して適用する。`main`・`aside`と入力要素（`input`・`select`・`textarea`・`dialog`）は、前項の宣言を持つ場合だけこの対象とする
- 共有シェル要素（`.app-header`・`.app-nav`・共通ダイアログ・`main > .toolbar`）は3画面で同一の規則を使い、画面別の上書きは水平方向の余白にとどめる
- 3画面が同じ役割で使う処理（SSEの購読と通知の振り分け、一覧の差分描画と選択、前後移動、警告表示、ドロワーの開閉など）は`common.js`へ置き、画面ごとの差は引数の関数で渡す。スクリプトは`index.html`からESモジュールとして読み込み、要求ごとに変わる値（`BASE_PATH`など）は`serve-bootstrap`などのJSONブロックから読む
