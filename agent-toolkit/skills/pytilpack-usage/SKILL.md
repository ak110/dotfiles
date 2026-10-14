---
name: pytilpack-usage
user-invocable: false
description: >
  pytilpackのモジュール構成・API・extras・CLIの使い方を参照するときに起動する。
  `import pytilpack`を含むコードを読み書きする時に起動する。
# 編集時の注意点: pytilpack本体の使用方法を変更した場合は本ファイルも更新する。
---

# pytilpackの使い方

pytilpackはPythonのユーティリティ集で、各種ライブラリ向けの拡張とフレームワーク非依存の汎用モジュールを`import pytilpack.<対象ライブラリ名または汎用モジュール名>`（`pytilpack.httpx`、`pytilpack.cache`など）で提供する。

APIの確認には公式資料の取得手段、導入と動作確認には対象のPython環境を操作するCLIが必要である。資料だけを読める環境では、その資料が示す使い方を説明し、導入と試験はPythonとCLIを使えるClaude CodeまたはCodexで行う。資料も取得できない場合は版と必要なAPIを明示して未確認の点を残す。説明は[文章の品質基準](references/quality-standards.md)に従う。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 収録モジュール、CLIのサブコマンド、extras要件またはAPIの詳細が必要なとき | <https://ak110.github.io/pytilpack/llms.txt>をWebFetchで取得し、そこからたどる対象モジュールのページ（例: `https://ak110.github.io/pytilpack/api/functools/index.md`）だけを個別に取得する。収録モジュールとCLIのサブコマンドは版により増減するため本ファイルへ列挙しない |

## 導入とextras

新規プロジェクトへは`uv add pytilpack`で追加する。
ライブラリ用ユーティリティ（Babel・SQLAlchemyなど）はextrasを要するため、`uv add "pytilpack[babel,sqlalchemy]"`のように依存指定へextras名を含める。
