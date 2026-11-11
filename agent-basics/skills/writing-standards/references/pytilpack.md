# pytilpackの使い方

pytilpackはPythonのユーティリティ集であり、各種ライブラリ向けの拡張機能や、フレームワークに依存しない汎用モジュールを`import pytilpack.<対象ライブラリ名または汎用モジュール名>`（`pytilpack.httpx`、`pytilpack.cache`など）の形式で提供する。

APIを確認するには公式ドキュメントの取得手段が必要であり、導入や動作確認には対象のPython環境を操作できるCLIが必要となる。
ドキュメントの閲覧のみが可能な環境では、その資料に基づく使用方法を説明し、実際の導入やテストはPythonとCLIを利用できるClaude CodeまたはCodexで行う。
資料を取得できない場合は、バージョンと必要なAPIを明記した上で未確認事項として残す。
説明にあたっては[文章の品質基準](writing.md)に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 収録モジュール、CLIのサブコマンド、extras要件またはAPIの詳細が必要なとき | <https://ak110.github.io/pytilpack/llms.txt>をWebFetchで取得し、そこからたどる対象モジュールのページ（例: `https://ak110.github.io/pytilpack/api/functools/index.md`）だけを個別に取得する。収録モジュールとCLIのサブコマンドは版により増減するため本ファイルへ列挙しない | 判断 | `read:agent-basics:*/skills/writing-standards/references/pytilpack.md` | `external-pytilpack-index` |

## 汎用処理を自作する前の確認

作業対象のプロジェクトが依存定義（`pyproject.toml`など）でpytilpackへ依存している場合は、Pythonで汎用的な処理を新しく実装する前に、pytilpackに同じ機能が無いかを確認する。
汎用的な処理とは、特定の業務や画面に依存せず、他のプロジェクトでもそのまま使える処理を指す（リトライ、キャッシュ、日時・パス・JSONの補助、外部ライブラリ向けの定型処理など）。
編集するファイルが`import pytilpack`を含むか、新規ファイルかは問わない。
pytilpackへ依存していないプロジェクトへ、この確認のためにpytilpackを導入することはしない。

1. 読込表の索引（`llms.txt`）を取得し、該当しそうなモジュールのページを確認する。
2. 該当する機能があれば、それを使う。
   extrasが必要な場合は、依存定義へ追加する。
3. 該当する機能が無ければ、プロジェクト内へ実装して作業を続ける。
   作業完了報告で「pytilpackに無かった汎用処理」として、処理の内容と実装した場所をユーザーへ伝える。

## 導入とextras

新規プロジェクトへ導入する際は`uv add pytilpack`で追加する。
外部ライブラリ向けのユーティリティ（BabelやSQLAlchemyなど）を利用する場合はextrasが必要となるため、`uv add "pytilpack[babel,sqlalchemy]"`のように依存関係の指定にextras名を含める。
