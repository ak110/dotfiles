---
name: pyfltr-usage
description: >
  pyfltrの使い方・JSONL出力解釈・特定ツール実行・トラブルシューティングのリファレンス。
  コードベース横断の正規表現置換（キーワード書き換え・参照除去など）でgrep/replaceサブコマンドを使う場合も参照する。
---

# pyfltrの使い方

pyfltrは各種コード品質ツール（formatter/linter/tester）を統合的に並列実行するツール。
Python・Rust・.NET・TypeScript/JSなどに対応する。

## 呼び出し方の基本

### 既存プロジェクトでの通常運用

- 通常運用は`uvx pyfltr ...`を使う。
  - v3.8以降、Python系ツール一式（ruff / mypy / pylint / pyright / ty / pytest / uv-sort等）が
    本体依存に統合されたため、`uvx pyfltr`単発で揃う。
  - cwdに`uv.lock`があれば`{command}-runner = "uv"`既定でプロジェクトvenvのツール版が優先される。
- pre-commit hookの`entry:`も`uvx pyfltr fast`に揃える。
  - `uv run`系を使う場合は`--frozen`必須（pre-commitは親環境の`UV_FROZEN`を引き継がないため）。
- pyfltr公式Dockerイメージ（`ghcr.io/ak110/pyfltr:latest`）のCIジョブではイメージ同梱の`pyfltr ci`を直接呼び出す。
- pyfltr自身を開発・検証するときに限り、`uv run pyfltr ...`を使う。

### 新規プロジェクトへの導入

pyfltr関連設定は原則として下記の公式推奨例をそのまま採用し、独自の順序やオプション構成は避ける。
推奨例は`pyproject.toml`・pre-commitフック・タスクランナー・GitHub Actionsを一貫した構成で揃える。
既存プロジェクトで乖離した設定を見つけた場合も、揃える方向の提案を優先する。

- Pythonプロジェクト: <https://ak110.github.io/pyfltr/guide/recommended/index.md>
- 非Pythonプロジェクト（TypeScript／JS・Rust・.NET）: <https://ak110.github.io/pyfltr/guide/recommended-nonpython/index.md>

## サブコマンドの使い分け

用途に応じて以下のフローで選択する。

- コーディングエージェントが呼び出す → `run-for-agent`
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

コミット前検証は対象ファイル・対象ツールを必要に応じて限定する（最終検証はCIに委ねる前提）。
公開インターフェース（関数シグネチャ・型定義・モジュール構造など）を変更した場合は全体で実行する。
末尾のsummary行で`failed`の有無と`diagnostics`数を確認する。

## grep&replace

`pyfltr grep`と`pyfltr replace`は、コードベース横断のキーワード書き換え・参照除去のように
複数ファイルに跨る正規表現置換を扱うサブコマンド。
`pyfltr grep`でマッチを確認し、必要に応じてファイル単位の除外を加えてから`pyfltr replace`で実行する。

代表的なワークフロー:

1. `pyfltr grep PATTERN [paths...]`でマッチを確認する。
   `--output-file=matches.jsonl`を付けると後続の`replace`へ渡せる
2. 誤爆や除外対象が混在する場合は次のいずれかで対象を限定する
    - `pyfltr replace PATTERN REPLACEMENT --exclude-file=path/to/skip.py [paths...]`でファイル単位除外
    - `matches.jsonl`から残したいファイル集合のみに編集した上で
      `pyfltr replace PATTERN REPLACEMENT --from-grep=matches.jsonl`に渡す。
      - `--from-grep`はマッチを含むファイル集合への限定のため、同一ファイル内の一部マッチだけを除外したい場合は適用しない
      - その場合は検索パターン側を限定するか手動編集で対処する
3. 適用前に`--dry-run`または`--show-changes`で差分を確認する
4. 結果に問題があれば`pyfltr replace --list-history`／`--undo ID`で取り消す

オプションの全容は`pyfltr grep --help`／`pyfltr replace --help`で確認する。

## JSONL出力

エージェント環境（`AI_AGENT` / `CODEX_CI` / `CLAUDECODE` / `CURSOR_AGENT`のいずれかが設定された環境）では、
全サブコマンドが既定でJSONL出力になる。stdoutにJSONLのみを書き、テキストログは抑止される。
text出力が必要な場合のみ`--output-format=text`を明示する（環境変数`PYFLTR_OUTPUT_FORMAT=text`でも同等）。
エージェントからの呼び出しは可読性のため`run-for-agent`を推奨する。

> 注記: mypy / pyright / pylint / ty 併用時は同じ型エラーが複数の`diagnostic`行に別ツール名で重複し得る。
> 1件の問題への複数ツール報告として扱い、修正計画を重複させない。
> 単一ツールに限定するには`--commands=mypy`等で指定する。

### messageの切り詰め仕様

`failed`かつ`diagnostics=0`のとき、`command.message`に生出力の抜粋が入る。
切り詰めは「先頭ブロック + `... (truncated)` + 末尾ブロック」のハイブリッド方式である。
`jsonl-message-max-chars`（既定2000文字）を`head : tail = 1 : 4`で配分し、冒頭の要約と末尾のトレースを共に残す。
切り詰め時は`command.truncated`に`{lines, chars, head_chars, tail_chars, archive}`が入る。
`archive`の相対パス（`tools/<command>/output.log`等）を`run_id`配下と組み合わせれば全文を直接参照できる。
全文取得手順は次節「再実行・調査の手段」を参照。

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

失敗ツールの再実行や全文ログ取得には3手段がある。

- `command.retry_command`: JSONL出力の失敗`command`レコードに入る、失敗ファイル限定の再実行コマンド文字列
  - そのままシェルで実行でき、特定の失敗ツール1件の再現に最も軽量
- `--only-failed`: 直前runの失敗ツール・失敗ファイルのみまとめて再実行する
  - 参照runは`--from-run RUN_ID`で明示できる（前方一致または`latest`）
- `show-run`: 切り詰められた`message`や確定済みrunを実行アーカイブから取得する
  - `header.run_id`または`summary`前後の`run_id`を控えて指定する

```bash
uvx pyfltr show-run RUN_ID --commands=TOOL --output    # 単一ツールのoutput.log全文
uvx pyfltr show-run RUN_ID --commands=mypy,ruff-check  # 複数ツールのdiagnostics.jsonl
```

`--commands`はカンマ区切りで複数指定可、`--output`併用は単一ツール指定のみ、`RUN_ID`に`latest`で最新runを参照する。

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

## 主要なCLIオプション

| オプション | 説明 |
| -- | -- |
| `--commands=<list>` | 実行ツールをカンマ区切りで指定（全サブコマンド共通、対象ファイル限定も併用可） |
| `--no-fix` | `run`／`fast`で自動付与されるfixステージを抑止 |
| `--fail-fast` | 1ツールでもエラーが出た時点で残りを打ち切る |
| `--only-failed` | 直前runの失敗ツール・失敗ファイルのみ再実行する |
| `--from-run <RUN_ID>` | `--only-failed`の参照runを明示指定（前方一致・`latest`対応） |
| `--no-cache` | ファイルhashキャッシュを無効化する |
| `--human-readable` | ツールの構造化出力（JSON等）を無効化し元のテキスト出力を使う |
| `--no-exclude` / `--no-gitignore` | ファイル除外設定を無効化 |

`--commands`にはエイリアスも指定できる。

- `format`: 全formatter（pre-commit、ruff-format、prettier、uv-sort、shfmt、cargo-fmt、dotnet-format等）
- `lint`: 全linter（ruff-check、mypy、pylint、pyright、ty、markdownlint、textlint等。Rust／dotnet系も含む）
- `test`: 全tester（pytest、vitest、cargo-test、dotnet-test等）
- `fast`: fastサブコマンド対象のコマンド

## トラブルシューティング

- エラー内容が`diagnostic`行だけでは把握しづらい場合、
  `uvx pyfltr run --output-format=text`等でテキスト出力を得てツールの生出力を確認する
- `--no-fix`で自動fixを止めた状態で`run`/`fast`を実行すると、autofixで解消できる違反が`diagnostic`に残ることがある。
  意図的に抑止する場合以外は付けずに実行する
- bin-runner未提供環境（Windows等でmise経由バイナリを提供しないツール、shellcheck・shfmtなど）:
  - 対象ファイルが0件のときは解決処理を省略するため`skipped`で通過し、対象がある状態で失敗すると`resolution_failed`が出る
  - 回避策は`bin-runner`を`direct`に切り替えてシステムのバイナリを使うか、当該ツールを`{tool} = false`で無効化する
- 特定ツールの解決状況（enable/runner/executable）は`uvx pyfltr command-info --check <tool>`で即座に確認できる
  - mise経由ツールでは `mise install` / `mise trust` の副作用が発生し得る点に注意する
- 特定ディレクトリが`extend-exclude`等で除外されると`uvx pyfltr run-for-agent`の検査対象から外れる
  - 除外を一時的に無視するには`--no-exclude`を使う（例: `uvx pyfltr run-for-agent --no-exclude path/to/file`）
- コマンド実行のタイムアウトは`pyproject.toml`の`[tool.pyfltr]`配下で調整できる
  - `command-timeout`: グローバル既定値、秒単位。既定600秒、`0`で無効化
  - `{command}-timeout`: per-tool値、`-1`で未設定sentinel・グローバル値にフォールバック、`0`で当該per-toolを無効化
  - ハング由来の停止はJSONLの`command.hints`の`status.timeout`注記で識別できる

## 詳細情報

設定リファレンス・カスタムコマンドの追加方法・pre-commit連携の詳細が必要な場合は、
[llms.txt](https://ak110.github.io/pyfltr/llms.txt)をWebFetchで取得し、各ページへのリンクから個別に取得する。
主要なページは以下の構成。

- 設定（基本設定・プリセット・並列実行）: `guide/configuration/index.md`
- ツール別設定（直接実行 / js-runner / bin-runnerのカテゴリ別設定・2段階実行・カスタムコマンド）:
  `guide/configuration-tools/index.md`
- 推奨設定（Pythonプロジェクト・タスクランナー・CI）: `guide/recommended/index.md`
- 非Python推奨設定（TypeScript／JS・Rust・.NET）: `guide/recommended-nonpython/index.md`

カスタムコマンドでは`{command}-severity`（`"error"` / `"warning"`）と`{command}-hints`（文字列配列）を指定できる。
`severity = "warning"`はパイプラインを止めずに警告通知する用途に使う。
`{command}-path`・`{command}-args`・`{command}-fix-args`に含まれる`~`はsubprocess起動直前にホームディレクトリへ展開される。
