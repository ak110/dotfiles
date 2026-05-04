---
name: pyfltr-usage
description: >
  pyfltrの使い方・JSONL出力解釈・特定ツール実行・トラブルシューティングのリファレンス。
---

# pyfltrの使い方

pyfltrは各種コード品質ツール（formatter/linter/tester）を統合的に並列実行するツール。
Python・Rust・.NET・TypeScript/JSなどに対応する。

## 呼び出し方の基本

### 既存プロジェクトでの通常運用

- 通常運用は`uvx pyfltr ...`を使う。
  v3.8以降、Python系ツール一式（ruff / mypy / pylint / pyright / ty / pytest / uv-sort等）が
  本体依存に統合されたため、`uvx pyfltr`単発で揃う。
  cwdに`uv.lock`があれば`{command}-runner = "uv"`既定でプロジェクトvenvのツール版が優先される。
- pre-commit hookの`entry:`も`uvx pyfltr fast`に揃える。
  `uv run`系を使う場合は`--frozen`必須（pre-commitは親環境の`UV_FROZEN`を引き継がないため）。
- pyfltr公式Dockerイメージ（`ghcr.io/ak110/pyfltr:latest`）配下のCIジョブでは、
  `uvx pyfltr ci`ではなくイメージ同梱の`pyfltr ci`を直接呼び出すことを推奨する。
- pyfltr自身を開発・検証するときに限り、`uv run --with-editable=. pyfltr ...`を使う。

### 新規プロジェクトへの導入

pyfltr関連設定は、原則として下記の公式推奨例をそのまま採用する。
独自の順序やオプション構成は避け、推奨例との差分は必要最小限にとどめる。
推奨例は`pyproject.toml`・pre-commitフック・タスクランナー・GitHub Actionsを一貫した構成で揃える。
複数プロジェクト間の差分を抑えて保守コストを下げる目的がある。
既存プロジェクトで推奨例と乖離した設定を見つけた場合も、揃える方向の提案を優先する。

- Pythonプロジェクト: <https://ak110.github.io/pyfltr/guide/recommended/index.md>
- 非Pythonプロジェクト（TypeScript／JS・Rust・.NET）: <https://ak110.github.io/pyfltr/guide/recommended-nonpython/index.md>

## サブコマンドの使い分け

用途に応じて以下のフローで選択する。

- LLMエージェントが呼び出す → `run-for-agent`
- CI環境で実行する → `ci`
- pre-commitフックで実行する → `fast`
- ローカル開発で手動実行する → `run`

| サブコマンド | 用途 | fixステージ | formatter変更で失敗するか |
| -- | -- | -- | -- |
| `ci` | 全チェック実行 | なし | する（exit 1） |
| `run` | 全チェック実行 | あり | しない（exit 0） |
| `fast` | 軽量チェック（mypy/pylint/pytestなど重いツールを除外） | あり | しない（exit 0） |
| `run-for-agent` | `run --output-format=jsonl`のエイリアス | あり | しない（exit 0） |

`run`／`fast`／`run-for-agent`は前段で自動fixステージを実行する。
fixステージは`ruff check --fix`（fix段）→ `ruff format`（formatter段）→ `ruff check`（linter段）
の3段構成を一般化した仕組みである。
抑止したい場合は`--no-fix`を付ける。`ci`はfixステージを含まないため、修正済みを前提とした検証に使う。

## JSONL出力

`--output-format=jsonl`を付けるとLLM向けの構造化出力が得られる。
stdoutにJSONLのみを書き、テキストログは抑止される。
エージェント環境では`AI_AGENT`が常時設定されるため、`--output-format`未指定でも全サブコマンドが既定でjsonl出力になる。
text出力が必要な場合のみ`--output-format=text`を明示する（環境変数`PYFLTR_OUTPUT_FORMAT=text`でも同等）。
エージェントからの呼び出しは可読性のため`run-for-agent`を推奨する。

> 注記: mypy / pyright / pylint / ty を併用していると、同じ型エラーが複数の`diagnostic`行に
> 別ツール名で重複出力されることがある。
> 1件の問題に対する複数ツールの報告として扱い、修正計画を重複させない。
> 単一ツールに絞って実行したい場合は`--commands=mypy`等で指定する。

### messageの切り詰め仕様

`failed`かつ`diagnostics=0`のとき、`command.message`に生出力の抜粋が入る。
切り詰めは「先頭ブロック + `... (truncated)` + 末尾ブロック」のハイブリッド方式で、
`jsonl-message-max-chars`（既定2000文字）を`head : tail = 1 : 4`で配分する。
冒頭にエラー要約を表示するツール（editorconfig-checker等）と末尾にスタックトレースを表示するツール（pytest／mypy等）の双方を救う。

切り詰めが起きると`command.truncated`に`{lines, chars, head_chars, tail_chars, archive}`が入る。
`archive`にはアーカイブ内の相対パスが入る。
具体的には`tools/<command>/output.log`または`tools/<command>/diagnostics.jsonl`の形式で、
`run_id`配下のアーカイブディレクトリと組み合わせれば直接参照できる。
`show-run`サブコマンドを介した取得手順は次節「再実行・調査の手段」を参照。

### messages[].fixフィールドの値

`failed`の`messages[]`には各違反ごとに`fix`フィールドが付くことがある。値の意味は以下の通り。

| 値 | 意味 |
| --- | --- |
| `safe` | 自動fixが安全（副作用が予測可能） |
| `unsafe` | 自動fixが可能だが意図と異なる修正になる可能性がある |
| `suggested` | 自動fixの候補があるが適用は手動判断 |
| `none` | ツールが自動fixを提供しない（手動修正が必要） |
| 省略 | ツールがfix情報を提供していない（手動修正が必要） |

`safe`／`unsafe`／`suggested`が並ぶ違反は`run-for-agent`の自動fixステージで解消される場合が多い。
`none`または省略の違反は内容に応じて手動修正する。

### 再実行・調査の手段

失敗ツールの再実行や全文ログの取得には以下の3手段がある。状況に応じて使い分ける。

#### command.retry_command で失敗ファイルだけ再実行

`run-for-agent`のJSONL出力では、失敗した`command`レコードに`retry_command`フィールドが入る。
失敗ファイルだけに絞った再実行コマンドが文字列として格納されているため、そのままシェルで実行できる。
特定の失敗ツール1件のみを素早く再現したい場合に最も軽量。

#### --only-failed で失敗ツール全体を再実行

`uvx pyfltr run-for-agent --only-failed`で、直前runの失敗ツール・失敗ファイルのみをまとめて再実行する。
個別に`retry_command`をコピーする手間を省きたい場合に使う。
参照runを明示する場合は`--from-run RUN_ID`を併用する（前方一致または`latest`を指定可）。

#### show-run でアーカイブから全文ログを取得

`message`が切り詰められた場合や、確定したrunを後から再確認したい場合は実行アーカイブから取得する。
`header.run_id`または`summary`の前後に出る`run_id`を控えておく。

```bash
# 単一ツールの output.log 全文を表示
uvx pyfltr show-run RUN_ID --commands=TOOL --output

# 複数ツールの diagnostics.jsonl をまとめて表示
uvx pyfltr show-run RUN_ID --commands=mypy,ruff-check
```

`--commands`はカンマ区切りで複数指定可（旧 `--tool` は廃止）。
`--output`との併用は単一ツール指定のみ許容される。最新runを参照する場合は`RUN_ID`に`latest`を指定できる。

### statusフィールドの意味

| status | 意味 | 対応 |
| -- | -- | -- |
| `succeeded` | 問題なし | 不要 |
| `formatted` | formatterがファイルを変更した | `ci`では失敗扱い／`run`系では再実行不要（補足参照） |
| `failed` | エラーあり | `diagnostic`行で修正対象のファイル・行番号・メッセージを確認する |
| `resolution_failed` | ツール起動コマンドの解決失敗（`bin-runner`/`js-runner`未提供等） | 「bin-runner未提供環境」節を参照 |
| `skipped` | ツール未検出などでスキップ | 通常は無視してよい |

- `formatted`は`run`系では正常終了するため看過されやすい。実行後は`git diff`で変更内容を必ず確認してからコミットする
- `formatted`が`run`系の繰り返しでも消えない場合はformatter/linter間の設定矛盾を疑い、
  `pyproject.toml`の`[tool.ruff-format]`と`[tool.ruff-check]`を突き合わせる

## 効率的なワークフロー

### 実行範囲の使い分け

コミット前検証は対象ファイルや対象ツールを必要に応じて絞って実行する（最終検証はCIに委ねる前提）。

```bash
uvx pyfltr run-for-agent --commands=mypy path/to/file
```

`--commands`で特定ツールに絞る／対象ファイルを指定することで出力量を抑えつつ、`diagnostic`行から修正対象を取得する。

公開インターフェース（関数シグネチャ・型定義・モジュール構造など）を変更した場合や、状況全体を把握したい場合は全体で実行する。

```bash
uvx pyfltr run-for-agent
```

末尾のsummary行で`failed`の有無と`diagnostics`数を確認する。
`run-for-agent`は前段で自動fixを適用するため、autofixで解消できる違反はここで消える。

## `--commands`オプション

カンマ区切りで実行するツールを指定する。全サブコマンドで使用可能。

```bash
uvx pyfltr run-for-agent --commands=mypy,ruff-check
```

以下のエイリアスも使える。

- `format`: 全formatter（pre-commit、ruff-format、prettier、uv-sort、shfmt、cargo-fmt、dotnet-format等）
- `lint`: 全linter（ruff-check、mypy、pylint、pyright、ty、markdownlint、textlint等。Rust／dotnet系も含む）
- `test`: 全tester（pytest、vitest、cargo-test、dotnet-test等）
- `fast`: fastサブコマンド対象のコマンド

## 主要なCLIオプション

| オプション | 説明 |
| -- | -- |
| `--commands=<list>` | 実行ツールをカンマ区切りで指定 |
| `--no-fix` | `run`／`fast`で自動付与されるfixステージを抑止 |
| `--fail-fast` | 1ツールでもエラーが出た時点で残りを打ち切る |
| `--only-failed` | 直前runの失敗ツール・失敗ファイルのみ再実行する |
| `--from-run <RUN_ID>` | `--only-failed`の参照runを明示指定（前方一致・`latest`対応） |
| `--no-cache` | ファイルhashキャッシュを無効化する |
| `--human-readable` | ツールの構造化出力（JSON等）を無効化し元のテキスト出力を使う |
| `--no-exclude` / `--no-gitignore` | ファイル除外設定を無効化 |

## トラブルシューティング

- エラー内容が`diagnostic`行だけでは把握しづらい場合、
  `uvx pyfltr run --output-format=text`等でテキスト出力を得てツールの生出力を確認する
- `--no-fix`で自動fixを止めた状態で`run`/`fast`を実行すると、autofixで解消できる違反が`diagnostic`に残ることがある。
  意図的に抑止する場合以外は付けずに実行する
- 特定ツールのみ再実行したい場合は`--commands=<ツール名>`で対象を絞る（全体再実行より早く原因切り分けできる）
- bin-runner未提供環境（Windows等でmise経由バイナリを提供しないツール、shellcheck・shfmtなど）:
  対象ファイルが0件のときは解決処理自体を省略するため`skipped`で通過する。
  対象ファイルがある状態で解決に失敗した場合は`resolution_failed`が出る。
  回避策は`bin-runner`を`direct`に切り替えてシステムにインストール済みのバイナリを使うか、
  当該ツールを`{tool} = false`で無効化する
- 特定ツールの解決状況（enable/runner/executable）を実機で即座に確認したい場合は
  `uvx pyfltr command-info --check <tool>`を使う。
  mise経由ツールでは `mise install` / `mise trust` の副作用が発生し得る点に注意する

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
