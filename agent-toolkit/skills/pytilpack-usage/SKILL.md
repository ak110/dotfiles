---
name: pytilpack-usage
user-invocable: false
description: >
  pytilpackのモジュール構成・API・extras・CLIの使い方を参照するときに起動する。
  `import pytilpack`を含むコードを読み書きする時に起動する。
# 編集時の注意点: pytilpack本体の使用方法を変更した場合は本ファイルも更新する。
---

# pytilpackの使い方

pytilpackはPythonのユーティリティ集であり、各種ライブラリ向けの拡張機能や、フレームワークに依存しない汎用モジュールを`import pytilpack.<対象ライブラリ名または汎用モジュール名>`（`pytilpack.httpx`、`pytilpack.cache`など）の形式で提供する。

APIを確認するには公式ドキュメントの取得手段が必要であり、導入や動作確認には対象のPython環境を操作できるCLIが必要となる。ドキュメントの閲覧のみが可能な環境では、その資料に基づく使用方法を説明し、実際の導入やテストはPythonとCLIを利用できるClaude CodeまたはCodexで行う。資料を取得できない場合は、バージョンと必要なAPIを明記した上で未確認事項として残す。説明にあたっては[文章の品質基準](references/quality-standards.md)に従う。

## 名指し参照の読込方法

本文に記載された各参照の適用条件を確認し、選んだ資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断へ進む。起動時の行に記載された資料は、起動本文とともに配送される。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/pytilpack-usage/references/quality-standards.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 収録モジュール、CLIのサブコマンド、extras要件またはAPIの詳細が必要なとき | <https://ak110.github.io/pytilpack/llms.txt>をWebFetchで取得し、そこからたどる対象モジュールのページ（例: `https://ak110.github.io/pytilpack/api/functools/index.md`）だけを個別に取得する。収録モジュールとCLIのサブコマンドは版により増減するため本ファイルへ列挙しない | 判断 | `skill:pytilpack-usage` | `external-pytilpack-index` |

## 導入とextras

新規プロジェクトへ導入する際は`uv add pytilpack`で追加する。
外部ライブラリ向けのユーティリティ（BabelやSQLAlchemyなど）を利用する場合はextrasが必要となるため、`uv add "pytilpack[babel,sqlalchemy]"`のように依存関係の指定にextras名を含める。
