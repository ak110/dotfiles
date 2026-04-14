---
name: pyfltr-usage
description: pyfltrの使い方・JSONL出力解釈・トラブルシューティングのリファレンス。pyfltrの実行結果の解釈に迷ったとき、特定のツールだけ実行したいとき、lintエラーの一括自動修正（`pyfltr run`／`pyfltr fast`）を使いたいとき、サブコマンドの違いを確認したいとき、pyfltrの設定やカスタムコマンドを変更したいときに使う。`pyfltr`が`CLAUDE.md`や`pyproject.toml`に記載されているプロジェクトで特に有用。pyfltrの出力でエラーが出たとき、formatterがファイルを変更したとき、特定ツールの再実行が必要なときにも参照する。
user-invocable: true
---

# pyfltrの使い方

pyfltrは各種コード品質ツール（formatter/linter/tester）を統合的に並列実行するツール。Python・Rust・.NET・TypeScript/JSなどに対応する。

## サブコマンド

| サブコマンド      | 用途                                   | fixステージ | formatterの変更で失敗するか | 主な使用場面                 |
| ----------------- | -------------------------------------- | ----------- | --------------------------- | ---------------------------- |
| `ci`（既定）      | 全チェック実行                         | なし        | する（exit 1）              | CI、pre-commit               |
| `run`             | 全チェック実行                         | あり        | しない（exit 0）            | ローカル開発                 |
| `fast`            | 軽量チェック（mypy/pylint/pytest除外） | あり        | しない（exit 0）            | pre-commitフック、手動整形   |
| `generate-config` | 設定ファイル雛形を標準出力             | 対象外      | 対象外                      | 新規プロジェクト設定の初期化 |

`run`／`fast`は前段で自動fixステージを実行する（`ruff check --fix` → `ruff format` → `ruff check` のような2段階方式を一般化した仕組み）。抑止したい場合は`--no-fix`を付ける。`ci`はfixステージを含まないため、修正済みを前提とした検証に使う。

### pre-commit統合

`pyfltr`は`pyproject.toml`の`[tool.pyfltr] pre-commit = true`で`pre-commit run --all-files`を内部から呼び出せる。v2.0以降は`pre-commit-fast`が既定`True`のため、`pyfltr fast`も統合対象となる。`pre-commit`配下から`pyfltr`が起動された場合は`PRE_COMMIT=1`環境変数の検出で二重実行を自動回避する。

## JSONL出力

`--output-format=jsonl`を付けるとLLM向けの構造化出力が得られる。stdoutにJSONLのみを書き、テキストログは抑止される。

### レコード種別

出力は3種別のレコードからなり、`diagnostic` → `tool` → `summary`の順で出力される。

```json
{"kind":"diagnostic","tool":"ruff-check","file":"src/a.py","line":1,"col":8,"rule":"F401","severity":"error","fix":"safe","msg":"`os` imported but unused"}
{"kind":"tool","tool":"ruff-check","type":"linter","status":"failed","files":12,"elapsed":0.8,"diagnostics":1,"rc":1}
{"kind":"tool","tool":"black","type":"formatter","status":"formatted","files":12,"elapsed":0.3,"diagnostics":0,"rc":1}
{"kind":"summary","total":2,"succeeded":0,"formatted":1,"failed":1,"skipped":0,"diagnostics":1,"exit":1}
```

- `diagnostic`: 個々の診断。`col`・`rule`・`severity`・`fix`は抽出できた場合のみ含まれる。`fix`は`safe`／`unsafe`のいずれか
- `tool`: ツールごとの実行結果。`status == "failed"`かつ`diagnostics == 0`のときのみ`message`フィールドに出力末尾（30行／2000文字の短い方）が含まれる。`rc`は`skipped`では省略される
- `summary`: 全体集計（常に末尾1行）

### statusフィールドの意味

| status      | 意味                          | 対応                                                                       |
| ----------- | ----------------------------- | -------------------------------------------------------------------------- |
| `succeeded` | 問題なし                      | 不要                                                                       |
| `formatted` | formatterがファイルを変更した | 基本的に再実行不要（formatter/linter間で設定矛盾がない限り変更は収束する） |
| `failed`    | エラーあり                    | `diagnostic`行で修正対象のファイル・行番号・メッセージを確認する           |
| `skipped`   | ツール未検出などでスキップ    | 通常は無視してよい                                                         |

## 効率的なワークフロー

### 1. 全体実行でsummaryを確認する

```bash
pyfltr run --output-format=jsonl
```

末尾のsummary行で`failed`の有無と`diagnostics`数を確認し、問題がなければ完了する。`run`は前段で自動fixを適用するため、autofixで解消できる違反はここで消える。

### 2. 問題のあるツールだけ再実行する

```bash
pyfltr run --commands=mypy --output-format=jsonl
```

`--commands`で特定ツールに絞ることで出力量を抑えつつ、`diagnostic`行から修正対象を取得する。

エラー内容がよくわからない場合は`--output-format=jsonl`を外して通常のテキスト出力で再実行し、ツールの通常出力を確認するのも有効。

## `--commands`オプション

カンマ区切りで実行するツールを指定する。全サブコマンドで使用可能。

```bash
pyfltr run --commands=mypy,ruff-check --output-format=jsonl
```

以下のエイリアスも使える。

- `format`: 全formatter（pyupgrade、autoflake、isort、black、ruff-format、prettier、cargo-fmt、dotnet-format等）
- `lint`: 全linter（ruff-check、pflake8、mypy、pylint、pyright、ty、markdownlint、textlint等。Rust／dotnet系も含む）
- `test`: 全tester（pytest、vitest、cargo-test、dotnet-test）
- `fast`: fastサブコマンド対象のコマンド

## 主要なCLIオプション

| オプション                        | 説明                                                         |
| --------------------------------- | ------------------------------------------------------------ |
| `--output-format=jsonl`           | LLM向け構造化出力                                            |
| `--commands=<list>`               | 実行ツールをカンマ区切りで指定                               |
| `--no-fix`                        | `run`／`fast`で自動付与されるfixステージを抑止               |
| `--human-readable`                | ツールの構造化出力（JSON等）を無効化し元のテキスト出力を使う |
| `--no-exclude` / `--no-gitignore` | ファイル除外設定を無効化                                     |
| `-j N` / `--jobs N`               | linter/testerの最大並列数（既定: 4）                         |

## 推奨設定への準拠

新規プロジェクトのpyfltr関連設定は、原則として下記の公式推奨例をそのまま採用する。独自の順序やオプション構成は避け、推奨例との差分は必要最小限にとどめる。推奨例は`pyproject.toml`・pre-commitフック・タスクランナー（Makefile／mise.toml）・GitHub Actionsを一貫した構成で揃えており、複数プロジェクト間の差分を抑えて保守コストを下げる狙いがある。

- Pythonプロジェクト: <https://ak110.github.io/pyfltr/guide/recommended/index.md>
- 非Pythonプロジェクト（TypeScript／JS・Rust・.NET）: <https://ak110.github.io/pyfltr/guide/recommended-nonpython/index.md>

既存プロジェクトで推奨例と乖離した設定を見つけた場合も、揃える方向の提案を優先する。

## 詳細情報

pyfltrの設定リファレンス、カスタムコマンドの追加方法、pre-commit連携の設定例などの詳細情報が必要な場合は、`https://ak110.github.io/pyfltr/llms.txt`をWebFetchで取得する。llms.txtにはサブコマンド一覧・対応ツール・設定の基本が含まれており、各ページへのリンクから必要なページだけ個別に取得する。主要なページは以下の構成。

- 設定（基本設定・プリセット・並列実行）: `guide/configuration/index.md`
- ツール別設定（2段階実行・bin-runner・npm系・カスタムコマンド）: `guide/configuration-tools/index.md`
- 推奨設定（Pythonプロジェクト・タスクランナー・CI）: `guide/recommended/index.md`
- 非Python推奨設定（TypeScript／JS・Rust・.NET）: `guide/recommended-nonpython/index.md`
