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

- 確定した計画の変更範囲の検証は、計画の`## 検証`（訂正済み検証表があればその表）の各コマンドを対象worktreeで順に実行する。実行前に、コマンドの対象が実差分を含むことを確かめる。1件が失敗しても残りを実行し、各コマンドの終了コードと出力を読んで判定する。検証行・訂正済み検証表の書式と引き継ぎ、pyfltrの完了の検収は`agent-toolkit:plan-mode`の`references/plan-file-standards.md`「検証と終端工程」に従う
- プロジェクトが採用する統合実行ツール（`pyfltr`など）を使い、対象を限定する場合も同じツールへパスを渡す。個別ツールの直接起動はデバッグ・最小再現・原因特定などプロジェクト規範が認める用途に限る
- チェック実行用MCPを優先し、pyfltrでは`run`を使う。MCPを使えない外部チェックと手動観測はハーネスの標準実行・背景実行・待機機能で実行する。別の担当へ全量を渡す場合は、自分のセッションのmanaged-tempへ標準出力と標準エラーを分けて保存し、終了コードと取得時の`git rev-parse HEAD`を`検証結果`へ書く
- 同じOSアカウントの検証を同時に1つへ限る実行枠に参加する検証は、他の検証の終了を待ってから処理を始める。参加する検証は、pyfltrを起動する全ての手段（CLI、MCPの`run`、pyfltrを呼ぶタスクランナー、commit hookを含む`git commit`）と、プロジェクトが実行枠へ参加させるpytestプラグインを読み込むpytestである。次の起動形と3分類はこの集合だけへ適用する
  - シェルから起動する場合は、ツール自身の上限（待ち時間を除き処理だけに働く）を使い、`timeout <秒>`の前置を省く。背景実行するときは、ホストの背景実行の時間上限を最大値か、他の検証の待ちを見込んだ十分な値にする。待機中はpyfltrの`execution-slot`警告か、pytestプラグインの待機の通知が標準エラーへ1回出る
  - 実行中の検証（待機中を含む）は、同じ実行を保持した識別子と公開待機機能で回収する。同じ範囲の起動は1つに保つ
  - ホストの上限で打ち切られた検証は、標準エラーの待機の通知の有無と最後の行から、待機中の打切りか処理中の打切りかを区別する。待機中の打切りは処理が始まっていないため、同じ対象集合を初回の実行として起動し直す
  - 処理中の打切りは`references/diagnostics.md`の診断と再実行の規則に従い、処理の上限は待ち時間と独立に保つ
  - 保持済みの結果と検証の対象集合は、3分類のどの場合も元のまま使う
- 実際のrun_id、保存した出力と手動観測は、完了報告の送達前確認へ渡す。報告する結果と保存物の対応は`agent-toolkit:completion-report`の`references/report-formats.md`「検証結果と実行記録」が定める

## pyfltrの起動形

- 名前が確定したチェックの実効設定（有効状態・実行器・コマンドライン・実行ファイルの解決）は、最初に`pyfltr command-info <command> --output-format=jsonl`で取得する
- 渡した対象ファイルとcwdは実行集合と一致しないことがある。モノレポでは子プロジェクトへ展開し、設定（pytestの`pytest-always-targets`など）は対象外のファイルを加える。起動前に実効設定と設定ファイルから実行集合を見積もり、実行後は保存結果のsubproject別の件数で確かめる。同じ内容へ同じ集合を重ねる起動は避け、失敗対象だけの再確認には`--only-failed`か`commands`の限定を使う
- 起動形は対象プロジェクトのタスクランナー定義（`Makefile`・`mise.toml`のtasks・`package.json`のscriptsなど）が用いる形へそろえ、定義が無ければ`uvx pyfltr`を使う
- 使い分け・入出力形式・失敗時の再実行は`pyfltr <サブコマンド> --help`とMCPスキーマで確認する。未掲載の設定・導入手順は<https://ak110.github.io/pyfltr/llms.txt>からたどる

## 保存した実行出力の取得

保存前に、生成元の公開指定から用途に合う出力形式と対象を選ぶ。
保存済みpyfltrログの本文を読むときは`pyfltr show-run <run_id> --commands <コマンド名> --output --output-format=text`を使い、表示のためだけのJSON抽出を省く。
構造化診断には`show-run --commands <コマンド名> --output-format=jsonl`を使い、JSONは`jq`で読む。試験結果は`-v`付きの出力の完全なテスト名と結果の行を要求へ対応付ける。
生成元の表示指定が無い用途や、結果の関連付け・意味の判定は、本文表示の取得と分けて扱う。
