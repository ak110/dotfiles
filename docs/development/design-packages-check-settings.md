# pyfltrのチェック設定の設計記録

[設計記録の索引](design.md)から参照する、pyfltrのチェック設定の記録である。

## pyfltrのsubproject分割とチェック設定の置き場所

dotfilesとagent-toolkitのmcp依存はSDK 2系を要求し、各lockは上書きなしの通常解決で生成する。
以前のmcp上書きはsemgrepによる脆弱版の固定を避けるために導入したが、pyfltrの2系要求まで1系へ置き換え、lock成功後にMCP起動のimportを失敗させた。
SDK移行後は通常解決で脆弱版の固定を避けられるため、両プロジェクトと配布先導入の上書きを撤去する。
宣言依存の両立判定をuvへ戻し、将来の要求の衝突を解決時に検出する。
別環境のuvx起動の成功を、プロジェクト環境や配布先導入の依存充足の代わりにはしない。

`agent-toolkit/`は独自の`pyproject.toml`と`uv.lock`を持つため、pyfltrはこれをsubprojectとして分割する。
分割したチェックは`agent-toolkit/pyproject.toml`とそのcwdの設定ファイルだけを読み、分割しないチェックはリポジトリ直下の設定だけを読む。
どのチェックがどちらから設定を読むかを設定ファイルから読み取れないため、直下だけの変更が`agent-toolkit/`配下のチェックへ届かない事象が繰り返し起きた。
lycheeの`.lycheeignore`が届かずCIが外部サイトの応答待ちで失敗した事例と、カスタムコマンドが`agent-toolkit/`配下の指定でskippedになる事例がこれに当たる。

設定の置き場所は次のように決める。

- pytest、mypy、pyright、ty、pylintなどのPython系のチェックは、agent-toolkitの環境で動く必要があるため分割を維持する。
  これらが読む`[tool.pyfltr]`のキーと`[tool.ruff]`・`[tool.pylint]`・`[tool.mypy]`・`[tool.pyright]`・`[tool.pytest.ini_options]`・`[tool.arid]`には両側へ同じ値を置く
- markdownlint、textlint、lychee、直下で定義した全カスタムコマンドなど、agent-toolkitの環境に依存しないチェックは`<名前>-subproject-aware = false`で分割を無効にし、直下の設定と設定ファイルでリポジトリ全体を1回で調べる。
  shellcheck、shfmt、colloquial-check、typosはpyfltrが元から分割しない
- 分割しないチェックのキーと設定ファイル（`.lycheeignore`、`.markdownlint-cli2.*`、`.textlintrc*`、`.textlintignore`）は`agent-toolkit/`側に置かない。
  置いても読まれず、直下と値が一致しなくても気付けない

`pyfltr_subproject_config_invariant_test.py`がこの対応を保証する。
分割しないチェックの値と設定ファイルがsubproject側に無いこと、直下のカスタムコマンドが分割を無効にしていること、分割するチェックと全体に作用するキーが両側で一致することを確かめる。
分割の有無はpyfltrの`resolve_subproject_aware`で直下の設定から求め、pyfltrの判定をテスト側に再実装しない。
探索パスをcwd基準で書く`ty-args`と`[tool.pyright]`の`extraPaths`、直下の実行だけの事情による`mypy-exclude`、直下基準のパスだけを持つ`extend-exclude`は意図的な差異としてテストに理由付きで列挙する。
意図的な差異を加える場合は同じ列挙へ理由とともに加える。
aridの検出条件は両側で共有するが、既存負債の受容データはチェック範囲ごとに持つ。
`arid-baseline.json`は受容中の負債が残る直下だけに置き、負債を解消したagent-toolkit側は基準ファイルも参照も持たない。
空のファイルを同期する仕組みは追加せず、設定整合テストも検出条件の同値と受容データの有無を区別する。
`[tool.typos]`はtyposが各ファイルに最も近い設定を自ら読み、語の不足は変更時点のチェックで失敗として現れるため対象から外す。

`subproject-exclude`で分割そのものを外す案は、Python系のチェックがリポジトリ直下の環境で動いてagent-toolkitの依存と設定を使えなくなるため採らない。
個別のキーを見つけるたびに両側へ値を追随させ、そのキーだけを比べるテストを追加する案は、テストの無いキーで同じ事象が続くため採らない。

レビュー限定基準の定義と索引は`agent-basics`の`writing-standards/references/review-only-criteria.md`に置く。
品質基準を所有する`agent-basics`へ定義を集約し、工程を扱う`agent-toolkit`の文書はその定義を参照する。
toolkitからbasicsへの一方向の依存を保つことで、品質基準の改訂が工程文書との循環参照を生まず、作成とレビューの両方が同じ定義を適用できる。
