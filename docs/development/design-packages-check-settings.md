# pyfltrのチェック設定の設計記録

[設計記録の索引](design.md)

## pyfltrのsubproject分割とチェック設定の置き場所

dotfilesとagent-toolkitのmcp依存はSDK 2系を要求し、`uv.lock`と`agent-toolkit/scripts/launch.py.lock`は上書きなしの通常解決で生成する。
以前のmcp上書きはsemgrepによる脆弱版の固定を避けるために導入したが、pyfltrの2系要求まで1系へ置き換え、lock成功後にMCP起動のimportを失敗させた。
SDK移行後は通常解決で脆弱版の固定を避けられるため、直下の宣言と`launch.py`の宣言と配布先導入の上書きを撤去する。
宣言依存の両立判定をuvへ戻し、将来の要求の衝突を解決時に検出する。
別環境のuvx起動の成功を、プロジェクト環境や配布先導入の依存充足の代わりにはしない。

Python系のチェック設定は[リポジトリ直下の設定](../../pyproject.toml)に集約する。
リポジトリ全体を一度だけ検査するコマンドは分割しない。
分割しないチェックの設定をサブプロジェクトへ置いても読まれないため、直下にだけ置く。

`subproject-exclude`で分割そのものを外す案は、サブプロジェクト側のチェックが直下の環境で動き、サブプロジェクトの設定を使えなくなるため採らない。

レビュー限定基準の定義と索引は`agent-basics`の`writing-standards/references/review-only-criteria.md`に置く。
品質基準を所有する`agent-basics`へ定義を集約し、工程を扱う`agent-toolkit`の文書はその定義を参照する。
toolkitからbasicsへの一方向の依存を保つことで、品質基準の改訂が工程文書との循環参照を生まず、作成とレビューの両方が同じ定義を適用できる。
