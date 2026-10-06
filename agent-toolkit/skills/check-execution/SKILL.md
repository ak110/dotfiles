---
name: check-execution
user-invocable: false
description: >
  formatter、linter、testerまたはプロジェクト固有のチェックツールを起動する直前と、
  変更範囲の検証の対象を選ぶ時点（計画の検証コマンドを書くときを含む）に起動する。
---

# formatter・linter・testerの実行

本スキルはformatter、linter、testerおよびプロジェクト固有のチェックツールを起動する主体へ、起動手段の選び方を提供する。
各ツールの受理形式、出力形式、個別の対処は、そのツールのヘルプ、MCPツールのスキーマ、公式ドキュメントに従う。
検証後は「検証結果の診断と警告の判定」を適用し、診断本文と担当差分を使って結果を判定する。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 実装に着手する直前（基準版を記録する時点）と、検証を実行した後に結果を判定する前 | `references/diagnostics.md` |
| 変更範囲の検証で動かすチェックとテストを選ぶ前（計画の検証コマンドを書くとき、実装後に検証する前、実行レビューで直接影響範囲を求めるとき） | `references/verification-scope.md` |

## 変更範囲の検証の対象選定

変更範囲の検証で動かすチェックとテストは、読込表の`references/verification-scope.md`の類型で決める。

## 統合実行ツール経由の起動

- 対象プロジェクトが統合実行ツール（`pyfltr`など）を採用する場合は、formatter、linter、testerおよびプロジェクト固有のチェックツールを個別に直接起動せず、その統合実行ツールのサブコマンド経由で実行する。設定で無効化したツールも直接起動すれば動作するため、設定による無効化は直接起動への防御にならない。特定のファイルだけを対象にする場合も同じ形でパスを渡す。対象プロジェクトの規範がデバッガー、最小再現、環境ごとの原因の特定などの用途で直接起動を認める場合は、その用途に限り直接起動する
- 統合実行ツールがチェック実行用のMCPツールを公開している場合は、そのMCPツールを優先する。pyfltrのチェック実行はMCPの`run`を使う。MCPを利用できず、有限終了する外部チェックの標準出力・標準エラーの全量と終了状態を一体で保持する場合だけ、対象worktreeで`atk run-command [--cwd DIR] [--timeout SECONDS] -- COMMAND [ARG...]`を使う。pipelineまたは複数行codeは`agent-toolkit/rules/02-agent-operations.md`に従ってmanaged-temp内のscriptへ保存し、そのscriptをshellで再解釈せずargvとして渡す
- シェルからCLIを直接実行する場合は、`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`「背景ジョブの起動形（Claude Code）」が定める長時間コマンドの前景実行に従う

## pyfltrの起動形

- 名前が確定したチェックコマンドの有効状態、実行器、実効コマンドライン、実行ファイルの解決結果を調べる場合は、最初に`pyfltr command-info <command> --output-format=jsonl`でそのコマンドの実効設定を取得する。引数と返却フィールドは`pyfltr command-info --help`の説明に従う。未知のコマンド名の探索、pyfltrの導入およびチェックの実行には、それぞれの目的に対応する既存の呼び出し手段（CLI・MCPツールなど）を使う
- pyfltrの起動形は、対象プロジェクトのタスクランナー定義（`Makefile`・`mise.toml`のtasks・`package.json`のscriptsなど）が用いる形へそろえる。この定義を持たない対象プロジェクトでは`uvx pyfltr`を使う
- サブコマンドの使い分け、オプションの受理形式、JSONL出力のレコード種別とフィールドの解釈、失敗ツールの再実行手段、ツール解決の失敗への対処は、`pyfltr <サブコマンド> --help`の出力とMCPツールのスキーマで確認する。これらが扱わない設定リファレンスと新規プロジェクトへの導入手順は<https://ak110.github.io/pyfltr/llms.txt>を取得し、そのページからたどって参照する

## 検証結果の診断と警告の判定

検証した主体は、終了コードと要約に加えて診断本文と警告を読み、変更行への帰属を基準版との差で判定する。基準版の記録、帰属の判定、警告の比較、既存不良と不足の返し方は読込表の`references/diagnostics.md`が定める。
