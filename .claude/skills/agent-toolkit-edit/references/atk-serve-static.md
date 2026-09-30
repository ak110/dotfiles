# atk serveの静的資産

`agent-toolkit/agent_toolkit/_atk/serve/static/`配下のCSS・HTML・JavaScriptを変更する場合は、次の3点に沿って書体と共有シェルの規則を一元定義に保つことを推奨する。
一元定義を保つと3画面の体裁がそろい、画面ごとの再定義や上書きは画面間の体裁のずれを招く。
3点に沿えない事情がある場合は、その事情と体裁のずれの不利益を比べて判断する。

- 書体、文字サイズ、行の高さ、字間および文字色は`:root`のカスタムプロパティーと`body`で一元定義し、
  `#screen-wi`・`#screen-plans`・`#screen-sessions`のIDセレクター配下でこれらを再定義しない
- `#screen-*`のIDセレクター直下へ、本文の装飾を担う裸のタグセレクター
  （`a`・`h1`から`h6`・`p`・`ul`・`ol`・`li`・`hr`・`blockquote`・`code`・`pre`・`table`・`thead`・`th`・`td`・`img`・`strong`）を書かない。
  Markdown本文とセッション本文向けの装飾は、本文コンテナー用クラス`.markdown-body`を経由してだけ適用する。
  画面の骨組みを選ぶ`main`・`aside`と入力要素を選ぶ`input`・`select`・`textarea`・`dialog`は
  前項が挙げる5つの宣言を持つ場合だけ本項の対象とする
- 共有シェル要素（`.app-header`・`.app-nav`・共通ダイアログ・`main > .toolbar`）は3画面で同一の規則を共有し、
  画面別の上書きを水平方向の余白にとどめる
