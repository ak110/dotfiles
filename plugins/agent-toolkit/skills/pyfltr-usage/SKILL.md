---
name: pyfltr-usage
description: pyfltrの使い方・JSONL出力解釈・トラブルシューティングのリファレンス。pyfltrの実行結果の解釈に迷ったとき、特定のツールだけ実行したいとき、lintエラーの一括自動修正（`pyfltr fix`）を使いたいとき、サブコマンドの違いを確認したいとき、pyfltrの設定やカスタムコマンドを変更したいときに使う。`pyfltr`が`CLAUDE.md`や`pyproject.toml`に記載されているプロジェクトで特に有用。pyfltrの出力でエラーが出たとき、formatterがファイルを変更したとき、特定ツールの再実行が必要なときにも参照する。
user-invocable: true
---

# pyfltrの使い方

pyfltrは各種コード品質ツール（formatter/linter/tester）を統合的に並列実行するツール。Python・Rust・.NET・TypeScript/JSなどに対応する。

## サブコマンド

| サブコマンド | 用途 | formatterの変更で失敗するか | 主な使用場面 |
| --- | --- | --- | --- |
| `ci`（既定） | 全チェック実行 | する（exit 1） | CI、pre-commit |
| `run` | 全チェック実行 | しない（exit 0） | ローカル開発 |
| `fast` | 軽量チェック（mypy/pylint/pytest除外） | しない（exit 0） | pre-commitフック |
| `fix` | linterの自動修正 | 対象外 | lintエラーの一括修正 |

`fix`サブコマンドはlinterのautofix機能（ruff-check、textlint、markdownlint、eslint、biome、cargo-clippy）を順次実行する。formatterは対象外（通常実行で常に書き込みモードで動作するため）。

## JSONL出力

`--output-format=jsonl`を付けるとLLM向けの構造化出力が得られる。stdoutにJSONLのみを書き、テキストログは抑止される。

### レコード種別

出力は3種別のレコードからなり、`diag` → `tool` → `summary`の順で出力される。

```json
{"kind":"diag","tool":"mypy","file":"src/a.py","line":42,"col":5,"msg":"Incompatible return value type"}
{"kind":"tool","tool":"mypy","type":"linter","status":"failed","files":12,"elapsed":0.8,"diags":1,"rc":1}
{"kind":"tool","tool":"black","type":"formatter","status":"formatted","files":12,"elapsed":0.3,"diags":0,"rc":1}
{"kind":"summary","total":2,"succeeded":0,"formatted":1,"failed":1,"skipped":0,"diags":1,"exit":1}
```

- `diag`: 個々の診断。`col`は抽出できた場合のみ含まれる
- `tool`: ツールごとの実行結果。`status == "failed"`かつ`diags == 0`のときのみ`message`フィールドに出力末尾（30行/2000文字の短い方）が含まれる。`rc`は`skipped`では省略される
- `summary`: 全体集計（常に末尾1行）。`tail -1`で取得できる

### statusフィールドの意味

| status | 意味 | 対応 |
| --- | --- | --- |
| `succeeded` | 問題なし | 不要 |
| `formatted` | formatterがファイルを変更した | 基本的に再実行不要（formatter/linter間で設定矛盾がない限り変更は収束する） |
| `failed` | エラーあり | `diag`行で修正対象のファイル・行番号・メッセージを確認する |
| `skipped` | ツール未検出などでスキップ | 通常は無視してよい |

## 効率的なワークフロー

### 1. 全体実行でsummaryを確認する

```bash
pyfltr run --output-format=jsonl
```

末尾のsummary行で`failed`の有無と`diags`数を確認し、問題がなければ完了する。

### 2. 問題のあるツールだけ再実行する

```bash
pyfltr run --commands=mypy --output-format=jsonl
```

`--commands`で特定ツールに絞ることで出力量を抑えつつ、`diag`行から修正対象を取得する。

エラー内容がよくわからない場合は`--output-format=jsonl`を外して通常のテキスト出力で再実行し、ツールの通常出力を確認するのも有効。

### 3. lintエラーが多数ある場合は自動修正を試す

```bash
pyfltr fix
```

ruff-check、textlint等のautofix対応ツールが順次実行される。自動修正で解消しない違反は手動対応が必要になる。

## `--commands`オプション

カンマ区切りで実行するツールを指定する。全サブコマンドで使用可能。

```bash
pyfltr run --commands=mypy,ruff-check --output-format=jsonl
```

以下のエイリアスも使える。

- `format`: 全formatter（pyupgrade、autoflake、isort、black、ruff-format、prettier、cargo-fmt、dotnet-format等）
- `lint`: 全linter（ruff-check、pflake8、mypy、pylint、pyright、ty、markdownlint、textlint等。Rust/dotnet系も含む）
- `test`: 全tester（pytest、vitest、cargo-test、dotnet-test）
- `fast`: fastサブコマンド対象のコマンド

## 主要なCLIオプション

| オプション | 説明 |
| --- | --- |
| `--output-format=jsonl` | LLM向け構造化出力 |
| `--commands=<list>` | 実行ツールをカンマ区切りで指定 |
| `-j N` / `--jobs N` | linter/testerの最大並列数（既定: 4） |

## 詳細情報

pyfltrの設定リファレンス、カスタムコマンドの追加方法、pre-commit連携の設定例などの詳細情報が必要な場合は、`https://ak110.github.io/pyfltr/llms.txt`をWebFetchで取得する。llms.txtにはサブコマンド一覧・対応ツール・設定の基本が含まれており、各ページへのリンクから必要なページだけ個別に取得する。主要なページは以下の構成。

- 設定（基本設定・プリセット・並列実行）: `guide/configuration/index.md`
- ツール別設定（2段階実行・bin-runner・npm系・カスタムコマンド）: `guide/configuration-tools/index.md`
- 推奨設定（Pythonプロジェクト・タスクランナー・CI）: `guide/recommended/index.md`
- 非Python推奨設定（TypeScript/JS・Rust・.NET）: `guide/recommended-nonpython/index.md`
