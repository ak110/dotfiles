---
name: check-execution
user-invocable: false
description: >
  変更に着手する直前（検証の基準版を記録する時点）、formatter、linter、testerまたはプロジェクト固有の
  チェックツールを起動する直前と、変更範囲の検証の対象を選ぶ時点（計画の検証コマンドを書くときを含む）に起動する。
---

# formatter・linter・testerの実行

本スキルはformatter、linter、testerおよびプロジェクト固有のチェックツールを起動するエージェントへ、起動手段の選び方を提供する。
各ツールの受理形式、出力形式、個別の対処は、そのツールのヘルプ、MCPツールのスキーマ、公式ドキュメントに従う。
検証後は読込表の`references/diagnostics.md`の行に従い、診断本文と担当差分を使って結果を判定する。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 実装に着手する直前（基準版を記録する時点）と、検証を実行した後に結果を判定する前 | `references/diagnostics.md` |
| 変更範囲の検証で動かすチェックとテストを選ぶ前（計画の検証コマンドを書くとき、実装後に検証する前、実行レビューで直接影響範囲を求めるとき） | `references/verification-scope.md` |
| Claude Codeのシェルから完了までに時間を要するCLIを直接起動する前 | `agent-toolkit:delegation`の`references/waiting-and-monitoring.md`「背景ジョブの起動形（Claude Code）」 |
| 受入シナリオ検証を実施する前（エージェント向け文書の改訂を試行で検証する時と、本番の起動単位から起動する対象を公開前に手動で観測する時を含む） | `references/acceptance-scenarios.md` |

## 統合実行ツール経由の起動

- プロジェクトが採用する統合実行ツール（`pyfltr`など）を使い、対象を限定する場合も同じ統合実行ツールへパスを渡す。個別の直接起動は、プロジェクト規範が認めるデバッグ・最小再現・原因特定などの用途に限る。設定による無効化は直接起動を防がない
- チェック実行用MCPを優先し、pyfltrでは`run`を使う。MCPを利用できず有限終了する外部チェックの両出力全量と終了状態を保持する場合、および有限終了する手動観測では、対象worktreeで`atk run-command [--cwd DIR] [--timeout SECONDS] -- COMMAND [ARG...]`を使う。返却JSONの`record_path`が指す保存JSONから実行条件・子終了状態・両出力へ到達できるため、その絶対パスと両出力パスを検証記録へ渡す。保存失敗は非0と診断で確認する。pipeline・複数行codeは`agent-toolkit/rules/02-agent-operations.md`に従いmanaged-temp内のscriptへ保存し、shellで再解釈せずargvとして渡す
- シェルからCLIを直接実行する場合は、`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`「背景ジョブの起動形（Claude Code）」が定める長時間コマンドの前景実行に従う

## pyfltrの起動形

- 名前が確定したチェックの実効設定（有効状態・実行器・コマンドライン・実行ファイルの解決）は、最初に`pyfltr command-info <command> --output-format=jsonl`で取得する。引数と返却欄は`pyfltr command-info --help`に従う。探索・導入・実行にはそれぞれの既存CLI・MCPを使う
- pyfltrの起動形は、対象プロジェクトのタスクランナー定義（`Makefile`・`mise.toml`のtasks・`package.json`のscriptsなど）が用いる形へそろえる。この定義を持たない対象プロジェクトでは`uvx pyfltr`を使う
- 使い分け・入出力形式・失敗時の再実行と解決は`pyfltr <サブコマンド> --help`とMCPスキーマで確認する。未掲載の設定・導入手順は<https://ak110.github.io/pyfltr/llms.txt>からたどる
