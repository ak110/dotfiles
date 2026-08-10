---
name: pyfltr-usage
description: >
  pyfltrを使う時、JSONL出力を解釈する時、特定ツールを実行する時、トラブルシュートする時に起動する。
  コードベース横断の正規表現置換（キーワード書き換え・参照除去など）でも起動する。
# 編集時の注意点: pyfltr本体の使用方法を変更した場合は本ファイルの更新漏れに注意する。
---

# pyfltrの使い方

本スキルは、pyfltrの使い方に関する知識を提供する。
pyfltrは各種コード品質ツール（formatter/linter/tester）を統合的に並列実行するツール。
Python・Rust・.NET・TypeScript/JSなどに対応する。

## MCP経由の呼び出し

`agent-toolkit`プラグインはpyfltrのMCPサーバーを同梱する。ツールが利用できる環境では、
MCPツールを既定の呼び出し方とする。シェル経由では完了までの時間により出力が失われる場合がある。

検査の実行は`run_for_agent`、横断検索・置換は`grep`・`replace`・`replace_undo`・`replace_history`を使う。
実行履歴の参照は`list_runs`・`show_run`・`show_run_diagnostics`・`show_run_output`、
設定・解決状況は`command_info`・`config`を使う。引数の詳細は各ツールのスキーマを参照する。

- `run_for_agent`は`paths`だけが必須。検査の基準ディレクトリは`work_dir`で指定し、
  省略時はサーバーの起動ディレクトリを使う
- 複数のリポジトリを扱う場合は`work_dir`を明示する。`only_failed`・`from_run`で既存runを参照するときと
  `show_run`系を使うときは、runの`cwd`が対象リポジトリと一致することを確認する。
  `replace_undo`は履歴内の対象パスと現在の解決先を確認し、元の対象を確定できない履歴には使わない
- `config`で利用者global設定を変更する場合は、他の全リポジトリへ影響する変更として扱う
- CLIと既定値が異なる点が2つある。`replace`は試行のみの動作が既定であり、
  実際に書き込むには明示的な指定を要する。`grep`は結果件数の上限が既定で設けられる

## CLI経由の呼び出し

prekフック・CI・`make test`など、シェルから起動する処理ではCLIを使う。
コーディングエージェントがCLIを直接実行する場合、Bashツールのタイムアウト引数を上限まで引き上げて
前景実行する。上限時間内に収まらない場合は、`--commands`で対象を限定して複数回の前景実行へ分ける。

### 既存プロジェクトでの通常運用

- 通常運用は`uvx pyfltr ...`を使う。prek hookの`entry:`も`uvx pyfltr fast`に揃える
  - `uv run`系を使う場合は`--frozen`必須（prekは親環境の`UV_FROZEN`を引き継がないため）
- pyfltr自身を開発・検証するときに限り、`uv run pyfltr ...`を使う
- 同梱ツールの範囲・ランナー解決の既定・公式Dockerイメージ経由の実行方法は版により変わるため、
  末尾「詳細情報」のllms.txtで現行版の仕様を確認する
- formatter・linter・tester・プロジェクト固有のカスタムチェックを含む対応ツール全般を個別に直接起動せず、
  常にpyfltrのサブコマンド経由で実行する。設定で無効化しているツールも直接起動すれば動作するため、
  設定による無効化は直接起動への防御にならない。特定ファイルのみを対象にする場合も同じ形でパスを渡す

### リポジトリ外のファイルを検査する場合

計画ファイルなど対象プロジェクトの外にあるファイルを検査する場合も、
作業ディレクトリは検査設定を持つプロジェクトのルートに固定する。
外部ファイルは絶対パスで渡し、`--allow-external-paths`を併せて指定する。

```sh
uvx pyfltr run --work-dir /path/to/project --allow-external-paths /absolute/path/to/external-file.md
```

外部ファイルの親ディレクトリやユーザーホームを、対象を内部パスに見せる目的で作業ディレクトリへ指定しない。
設定を持たない起点では除外パターンが適用されず、ホーム配下の広域探索が続いて手動中断に至る。
`--commands`で対象ツールを明示した実行の件数が0件の場合は検査成功と扱わず、
作業ディレクトリと対象パスの指定を見直す。

### 新規プロジェクトへの導入

pyfltr関連設定は下記の公式推奨例（`pyproject.toml`・prekフック・タスクランナー・GitHub Actionsの一貫構成）に揃える。
独自の順序・オプション構成や既存プロジェクトの乖離した設定も同様に揃える。

- Pythonプロジェクト: <https://ak110.github.io/pyfltr/guide/recommended/index.md>
- 非Pythonプロジェクト（TypeScript／JS・Rust・.NET）: <https://ak110.github.io/pyfltr/guide/recommended-nonpython/index.md>

## サブコマンドの使い分け

- コーディングエージェントが呼び出す・ローカル開発で手動実行する → `run`
- CI環境で実行する → `ci`（fixステージなし。formatter変更で失敗する）
- prekフックで実行する → `fast`（軽量チェック。mypy/pylint/pytestなど重いツールを除外）

サブコマンドを省略して`uvx pyfltr <path>`のように呼び出すとexit 2の即時エラー終了となる。
ファイル個別実行も`uvx pyfltr run <files>`の形でサブコマンドを必ず指定する。

`run`／`fast`は通常ステージに先立ってfixステージを実行する
（エージェント検出時の`run`は`run-for-agent`と同等に振る舞う。`run-for-agent`は互換用途の別名）。
`--no-fix`が抑止するのはfixステージだけであり、通常ステージのformatterは対象ファイルを書き換えることがある。

コミット前検証は対象ファイル・対象ツールを必要に応じて限定する（最終検証はCIに委ねる前提）。
公開インターフェース（関数シグネチャ・型定義・モジュール構造など）を変更した場合は全体で実行する。
末尾のsummary行で`failed`の有無と`diagnostics`数を確認する。
所要時間の計測・現状把握・調査を目的とする実行では、`--commands`で必要な非formatterへ限定し`--no-fix`を併用する。

## grep&replace

`pyfltr grep`と`pyfltr replace`は、コードベース横断のキーワード書き換え・参照除去のように
複数ファイルに跨る正規表現置換を扱うサブコマンド。
`pyfltr grep`でマッチを確認し、必要に応じてファイル単位の除外を加えてから`pyfltr replace`で実行する。

対象の限定・差分確認・取り消しの各オプションは`pyfltr grep --help`／`pyfltr replace --help`で確認する。
`--from-grep`はマッチを含むファイル集合への限定であり、同一ファイル内の一部マッチだけを除外したい場合は
検索パターン側を限定するか手動編集で対処する。

`-U`（multiline）オプション使用時は`.`が改行をまたぐため、単一行を想定するパターンには
`.`の代わりに`[^\n]`を使う（例: `^(\s*[-*]\s[^\n]+?)。$`）。

## JSONL出力

エージェント環境（`AI_AGENT` / `CODEX_CI` / `CLAUDECODE` / `CURSOR_AGENT`のいずれかが設定された環境）では、
全サブコマンドが既定でJSONL出力になる。stdoutにJSONLのみを書き、テキストログは抑止される。
text出力が必要な場合のみ`--output-format=text`を明示する（環境変数`PYFLTR_OUTPUT_FORMAT=text`でも同等）。

- mypy / pyright / pylint / ty併用時は同じ型エラーが複数の`diagnostic`行に別ツール名で重複し得るため、
  1件の問題への複数ツール報告として扱い修正計画を重複させない（単一ツールに限定するには`--commands=mypy`等を指定する）
- `failed`かつ`diagnostics=0`のとき`command.message`に生出力の抜粋が入る。切り詰め時は
  `command.truncated.archive`の相対パスと`run_id`から全文を参照できる（取得手順は次節）
- `messages[].fix`は自動fix可否を示す（`safe`/`unsafe`/`suggested`は自動fix候補あり、
  `none`または省略は手動修正が必要）
- `formatted`は`run`系では正常終了するため看過されやすい。実行後は`git diff`で変更内容を必ず確認してからコミットする。
  繰り返しても消えない場合はformatter/linter間の設定矛盾を疑う
- `resolution_failed`はツール起動コマンドの解決失敗を示す（「トラブルシューティング」節参照）

### 再実行・調査の手段

失敗ツールの再実行や全文ログ取得には3手段がある。指定方法の詳細は各サブコマンドの`--help`で確認する。

- `command.retry_command`: JSONL出力の失敗`command`レコードに入る、失敗ファイル限定の再実行コマンド文字列
- `--only-failed`: 直前runの失敗ツール・失敗ファイルのみまとめて再実行する
  （参照runは`--from-run RUN_ID`で明示できる。前方一致または`latest`）
- `show-run`: 切り詰められた`message`や確定済みrunを実行アーカイブから取得する

## トラブルシューティング

- bin-runner未提供環境（Windows等でmise経由バイナリを提供しないツール、shellcheck・shfmtなど）では
  対象がある状態で失敗すると`resolution_failed`が出る。
  回避策は`bin-runner`を`direct`に切り替えてシステムのバイナリを使うか、当該ツールを`{tool} = false`で無効化する
- 特定ツールの解決状況（enable/runner/executable）は`uvx pyfltr command-info --check <tool>`で確認できる
- コマンド実行のタイムアウトは`pyproject.toml`の`[tool.pyfltr]`配下（`command-timeout`・`{command}-timeout`）で調整できる。
  ハング由来の停止はJSONLの`command.hints`の`status.timeout`注記で識別できる
- CIとローカルでmiseが解決するツール版が異なる場合がある。`{command}-version`の一時指定でCI側の版を固定すると、
  CI固有の指摘をローカルで再現できる

## 詳細情報

CLIオプションの全容・設定リファレンス・カスタムコマンドの追加方法・prek連携の詳細は
[llms.txt](https://ak110.github.io/pyfltr/llms.txt)をWebFetchで取得し、各ページへのリンクから個別に取得する。
