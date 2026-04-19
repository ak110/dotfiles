---
name: pyfltr-usage
description: >
  pyfltrの使い方・JSONL出力解釈・トラブルシューティングのリファレンス。
  pyfltrの実行結果の解釈に迷った時、特定のツールだけ実行したい時、
  lintエラーの一括自動修正（`pyfltr run`／`pyfltr fast`）を使いたい時、
  サブコマンドの違いを確認したい時、pyfltrの設定やカスタムコマンドを変更したい時に使う。
  `pyfltr`が`CLAUDE.md`や`pyproject.toml`に記載されているプロジェクトで特に有用。
  pyfltrの出力でエラーが出た際、formatterがファイルを変更した際、特定ツールを再実行したい際にも該当する。
---

# pyfltrの使い方

pyfltrは各種コード品質ツール（formatter/linter/tester）を統合的に並列実行するツール。
Python・Rust・.NET・TypeScript/JSなどに対応する。

## サブコマンド

| サブコマンド | 用途 | fixステージ | formatter変更で失敗するか | 出力形式 | 使用場面 |
| -- | -- | -- | -- | -- | -- |
| `ci` | 全チェック実行 | なし | する（exit 1） | text | CI、pre-commit |
| `run` | 全チェック実行 | あり | しない（exit 0） | text | ローカル開発 |
| `fast` | 軽量チェック（mypy/pylint/pytestなど重いものを除外） | あり | しない（exit 0） | text | pre-commitフック |
| `run-for-agent` | `run`と同等をJSONL出力で実行するエイリアス | あり | しない（exit 0） | jsonl | LLMエージェント |

`run`／`fast`／`run-for-agent`は前段で自動fixステージを実行する
（`ruff check --fix` → `ruff format` → `ruff check` のような2段階方式を一般化した仕組み）。
抑止したい場合は`--no-fix`を付ける。`ci`はfixステージを含まないため、修正済みを前提とした検証に使う。
`run-for-agent`は`run --output-format=jsonl`のエイリアスであり、LLMエージェントから呼び出す際に利用する。

## JSONL出力

`--output-format=jsonl`を付けるとLLM向けの構造化出力が得られる。
stdoutにJSONLのみを書き、テキストログは抑止される。
`pyfltr run-for-agent`は`--output-format=jsonl`を暗黙的に付与するエイリアスなので、エージェントからの呼び出しにはこちらを使う。
環境変数`PYFLTR_OUTPUT_FORMAT=jsonl`でも同等の既定値切り替えができ、`ci`など任意のサブコマンドに適用される
（CLIオプションが優先）。

### statusフィールドの意味

| status | 意味 | 対応 |
| -- | -- | -- |
| `succeeded` | 問題なし | 不要 |
| `formatted` | formatterがファイルを変更した | 基本的に再実行不要（formatter/linter間で設定矛盾がない限り変更は収束する） |
| `failed` | エラーあり | `diagnostic`行で修正対象のファイル・行番号・メッセージを確認する |
| `skipped` | ツール未検出などでスキップ | 通常は無視してよい |

## 効率的なワークフロー

### 実行範囲の使い分け

コミット前検証は対象ファイルや対象ツールを必要に応じて絞って実行する（最終検証はCIに委ねる前提）。

```bash
pyfltr run-for-agent --commands=mypy path/to/file
```

`--commands`で特定ツールに絞る／対象ファイルを指定することで出力量を抑えつつ、`diagnostic`行から修正対象を取得する。

公開インターフェース（関数シグネチャ・型定義・モジュール構造など）を変更した場合や、状況全体を把握したい場合は全体で実行する。

```bash
pyfltr run-for-agent
```

末尾のsummary行で`failed`の有無と`diagnostics`数を確認する。
`run-for-agent`は前段で自動fixを適用するため、autofixで解消できる違反はここで消える。

## `--commands`オプション

カンマ区切りで実行するツールを指定する。全サブコマンドで使用可能。

```bash
pyfltr run-for-agent --commands=mypy,ruff-check
```

以下のエイリアスも使える。

- `format`: 全formatter（pre-commit、ruff-format、prettier、uv-sort、shfmt、cargo-fmt、dotnet-format等）
- `lint`: 全linter（ruff-check、mypy、pylint、pyright、ty、markdownlint、textlint等。Rust／dotnet系も含む）
- `test`: 全tester（pytest、vitest、cargo-test、dotnet-test等）
- `fast`: fastサブコマンド対象のコマンド

## 主要なCLIオプション

| オプション | 説明 |
| -- | -- |
| `--output-format=jsonl` | LLM向け構造化出力 |
| `--commands=<list>` | 実行ツールをカンマ区切りで指定 |
| `--no-fix` | `run`／`fast`で自動付与されるfixステージを抑止 |
| `--fail-fast` | 1ツールでもエラーが出た時点で残りを打ち切る |
| `--only-failed` | 直前runの失敗ツール・失敗ファイルのみ再実行する |
| `--from-run <RUN_ID>` | `--only-failed`の参照runを明示指定（前方一致・`latest`対応） |
| `--no-cache` | ファイルhashキャッシュを無効化する |
| `--human-readable` | ツールの構造化出力（JSON等）を無効化し元のテキスト出力を使う |
| `--no-exclude` / `--no-gitignore` | ファイル除外設定を無効化 |

## トラブルシューティング

- エラー内容が`diagnostic`行だけでは把握しづらい場合、`run-for-agent`の代わりに`run`コマンドで
  通常のテキスト出力を得てツールの生出力を確認する
- `status=formatted`直後に同じ違反が残る場合、formatterとlinterの設定矛盾が疑われる。
  `pyproject.toml`の`[tool.ruff-format]`と`[tool.ruff-check]`を突き合わせて再実行する
- `--no-fix`で自動fixを止めた状態で`run`/`fast`を実行すると、autofixで解消できる違反が`diagnostic`に残ることがある。
  意図的に抑止する場合以外は付けずに実行する
- 特定ツールのみ再実行したい場合は`--commands=<ツール名>`で対象を絞る（全体再実行より早く原因切り分けできる）

## 推奨設定への準拠

新規プロジェクトのpyfltr関連設定は、原則として下記の公式推奨例をそのまま採用する。
独自の順序やオプション構成は避け、推奨例との差分は必要最小限にとどめる。
推奨例は`pyproject.toml`・pre-commitフック・タスクランナー・GitHub Actionsを一貫した構成で揃える。
（タスクランナー：Makefileやmiseなど）
複数プロジェクト間の差分を抑えて保守コストを下げる目的がある。

- Pythonプロジェクト: <https://ak110.github.io/pyfltr/guide/recommended/index.md>
- 非Pythonプロジェクト（TypeScript／JS・Rust・.NET）: <https://ak110.github.io/pyfltr/guide/recommended-nonpython/index.md>

既存プロジェクトで推奨例と乖離した設定を見つけた場合も、揃える方向の提案を優先する。

## 詳細情報

pyfltrの設定リファレンス、カスタムコマンドの追加方法、pre-commit連携の設定例などの詳細情報が必要な場合は、
[llms.txt](https://ak110.github.io/pyfltr/llms.txt)をWebFetchで取得する。
llms.txtにはサブコマンド一覧・対応ツール・設定の基本が含まれており、各ページへのリンクから必要なページだけ個別に取得する。
主要なページは以下の構成。

- 設定（基本設定・プリセット・並列実行）: `guide/configuration/index.md`
- ツール別設定（直接実行 / js-runner / bin-runnerのカテゴリ別設定・2段階実行・カスタムコマンド）:
  `guide/configuration-tools/index.md`
- 推奨設定（Pythonプロジェクト・タスクランナー・CI）: `guide/recommended/index.md`
- 非Python推奨設定（TypeScript／JS・Rust・.NET）: `guide/recommended-nonpython/index.md`
