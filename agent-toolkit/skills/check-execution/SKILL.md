---
name: check-execution
user-invocable: false
description: >
  変更に着手する直前（検証の基準版を記録する時点）、formatter、linter、testerまたはプロジェクト固有の
  チェックツールを起動する直前と、変更範囲の検証の対象を選ぶ時点（計画の検証コマンドを書くとき、
  既存の検証コマンドを再利用して実行する直前を含む）に起動する。
---

# formatter・linter・testerの実行

本スキルはformatter、linter、testerおよびプロジェクト固有のチェックツールを起動するエージェントへ、起動手段の選び方を提供する。
各ツールの受理形式と出力形式は、そのツールのヘルプ、MCPツールのスキーマ、公式ドキュメントに従う。

チェックを動かすには対象の実行環境とCLIかチェック用MCP、保存結果を判定するにはその記録の読取手段が必要である。実行手段が無い場合は、提示された保存結果の判定と検証対象・コマンドの選定までを行い、未実行のチェックを明示する。新しい検証と実行記録の保存は、対象を実行できるClaude CodeまたはCodexで行う。結果の説明には[文章の品質基準](references/quality-standards.md)を使う。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 実装に着手する直前（基準版を記録する時点）と、検証を実行した後に結果を判定する前 | `references/diagnostics.md` |
| 変更範囲の検証で動かすチェックとテストを選ぶ前（計画の検証コマンドを書くとき、実装後に検証する前、計画の検証行・前回の実行・CI記録の検証コマンドを再利用して実行する前、実行レビューで直接影響範囲を求めるとき） | `references/verification-scope.md` |
| Claude Codeのシェルから完了までに時間を要するCLIを直接起動する前 | `agent-toolkit:delegation`の`references/waiting-and-monitoring.md` |
| 受入シナリオ検証を実施する前（文書の試行、操作・故障注入・自動終了失敗が使う資源の所有と共有範囲を判定する時、本番の起動単位からの公開前観測を含む） | `references/acceptance-scenarios.md` |

## 統合実行ツール経由の起動

- 確定した計画の変更範囲の検証は`atk run-script plan-verify -- --plan <絶対パス> --worktree <絶対パス> --timeout <正の有限秒数> --summary-only`で実行する。訂正済み検証表があれば`--verification-plan <訂正済み検証表の絶対パス>`を加える。先に同じ入力の`--list`で全対象と順序が実差分を含むことを確かめる。失敗・timeout後も全件を実行するため、集約終了コードに加えて結果まとめの各行の`state`・`failure`、各record_pathと両出力を読んで判定する。検証行・訂正済み検証表の書式と引き継ぎ、pyfltrのJSONL指定と完了の検収は`agent-toolkit:plan-mode`の`references/plan-file-standards.md`「検証と終端工程」に従う
- プロジェクトが採用する統合実行ツール（`pyfltr`など）を使い、対象を限定する場合も同じツールへパスを渡す。個別ツールの直接起動はデバッグ・最小再現・原因特定などプロジェクト規範が認める用途に限る
- チェック実行用MCPを優先し、pyfltrでは`run`を使う。MCPを使えない外部チェックや手動観測で両出力と終了状態を残す場合は、`atk run-command [--cwd DIR] [--timeout SECONDS] -- COMMAND [ARG...]`を使う。返却JSONの`record_path`（実行条件・子終了状態・両出力へ到達できる保存JSON）の絶対パスを検証記録へ渡す
- 実際のrun_id・record_path、保存した手動観測は、完了報告の送達前確認へ渡す。報告する結果と保存物の対応は`agent-toolkit:completion-report`の`references/report-formats.md`「検証結果と実行記録」が定める

## pyfltrの起動形

- 名前が確定したチェックの実効設定（有効状態・実行器・コマンドライン・実行ファイルの解決）は、最初に`pyfltr command-info <command> --output-format=jsonl`で取得する
- 起動形は対象プロジェクトのタスクランナー定義（`Makefile`・`mise.toml`のtasks・`package.json`のscriptsなど）が用いる形へそろえ、定義が無ければ`uvx pyfltr`を使う
- 使い分け・入出力形式・失敗時の再実行は`pyfltr <サブコマンド> --help`とMCPスキーマで確認する。未掲載の設定・導入手順は<https://ak110.github.io/pyfltr/llms.txt>からたどる

## 保存した実行出力の取得

保存前に、生成元の公開指定から用途に合う出力形式と対象を選ぶ。
保存済みpyfltrログの本文を読むときは`pyfltr show-run <run_id> --commands <コマンド名> --output --output-format=text`を使い、表示のためだけのJSON抽出を省く。
構造化診断には`show-run --commands <コマンド名> --output-format=jsonl`を、試験結果の関連付けには`verification-record`の結果ファイル入力と`--list-results`を使う。
生成元の表示指定が無い用途や、結果の関連付け・意味の判定は、本文表示の取得と分けて扱う。
