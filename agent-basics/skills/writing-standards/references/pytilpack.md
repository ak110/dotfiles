# pytilpackの使い方

pytilpackはPythonのユーティリティ集で、各種ライブラリ向けの拡張と汎用モジュールを`import pytilpack.<対象ライブラリ名または汎用モジュール名>`（`pytilpack.httpx`、`pytilpack.cache`など）で提供する。
本書は、pytilpackを使うコードを読み書きする時と、pytilpackへ依存するプロジェクトで汎用的な処理を新しく実装する前に読む。
既存の機能を先に探すのは`agent-basics:writing-standards/references/quality-principles-subtraction.md`の適用である。

APIの確認には公式ドキュメントの取得手段が、導入と動作確認には対象のPython環境を操作できるCLIが要る。
ドキュメントを読めるだけの環境では資料に基づく使い方を説明し、導入とテストはPythonとCLIを使える環境（Claude Code、Codexなど）で行う。
資料を取得できない時は、版と必要なAPIを書いて未確認として残す。

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 収録モジュール、CLIのサブコマンド、extras要件またはAPIの詳細が必要なとき | <https://ak110.github.io/pytilpack/llms.txt>をWebFetchで取得し、そこからたどる対象モジュールのページ（例: `https://ak110.github.io/pytilpack/api/functools/index.md`）だけを個別に取得する。収録モジュールとCLIのサブコマンドは版により増減するため本ファイルへ列挙しない | 判断 | `read:agent-basics:*/skills/writing-standards/references/pytilpack.md` | `external-pytilpack-index` |

## 汎用処理を自作する前の確認

作業対象のプロジェクトが依存定義（`pyproject.toml`など）でpytilpackへ依存している場合は、Pythonで汎用的な処理を新しく実装する前に、pytilpackに同じ機能が無いかを確かめる。
汎用的な処理は、特定の業務や画面に依存せず他のプロジェクトでもそのまま使える処理を指す（リトライ、キャッシュ、日時・パス・JSONの補助、外部ライブラリ向けの定型処理など）。
編集するファイルが`import pytilpack`を含むかと、新規のファイルかは問わない。
依存していないプロジェクトへ、この確認のためにpytilpackを導入しない。

1. 本書「読込表」の索引（`llms.txt`）を取得し、該当しそうなモジュールのページを確かめる。
2. 該当する機能があれば使い、extrasが要る場合は依存定義へ足す。
3. 無ければプロジェクトの中へ実装して作業を続け、作業完了報告で「pytilpackに無かった汎用処理」として処理の内容と実装した場所をユーザーへ伝える。

## 導入とextras

新規のプロジェクトへは`uv add pytilpack`で追加する。
外部ライブラリ向けの機能（BabelやSQLAlchemyなど）はextrasが要るため、`uv add "pytilpack[babel,sqlalchemy]"`のようにextras名を含める。
