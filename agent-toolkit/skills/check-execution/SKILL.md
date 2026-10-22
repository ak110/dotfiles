---
name: check-execution
user-invocable: false
description: >
  変更に着手する直前（検証の基準版を記録する時点）、formatter、linter、testerまたはプロジェクト固有の
  チェックツールを起動する直前と、変更範囲の検証の対象を選ぶ時点（計画の検証コマンドを書くとき、
  既存の検証コマンドを再利用して実行する直前を含む）に起動する。
---

# チェックの品質基準と実行・保存

対象選定と結果判定の品質基準は`agent-basics:check-execution`が定義する。本スキルは統合実行、計画検証、実行記録と完了報告への接続を所有する。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

<!-- read-table-source: SKILL-delivery-1.md -->

## 読込表

<!-- read-table-source: SKILL-delivery-2.md -->

| 時点または条件 | 取得する範囲 |
| --- | --- |
| 実装着手直前と検証結果の判定前 | `references/diagnostics.md` |
| 検証対象の選定と計画・保存結果の実行・取得前（実行レビューで直接影響範囲だけを求める場合を除く） | `references/verification-scope.md` |
| 実行レビューで直接影響範囲を求める前 | `references/verification-scope.md`の「直接影響範囲の類型」「範囲の決め方」 |
| 受入シナリオの文書試行・常駐サービスの公開前観測の前 | `references/acceptance-scenarios.md` |
| Claude Codeのシェルから時間を要するCLIを直接起動する前 | `agent-toolkit:delegation`の`references/waiting-and-monitoring.md` |

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 起動時 | `agent-basics:check-execution/SKILL.md` | 起動時 | `skill:check-execution` | |

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
