# 検証対象の品質基準と記録操作

検証対象の選定に関する品質基準の定義元は、`agent-basics:check-execution/references/verification-scope.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。すでに同一バージョンの本文を取得している場合は、再取得せずにその内容を再利用する。

検証対象を選定する際は、`agent-toolkit:wi-standards`の`references/awi-body.md`「完成条件」に定められた自己観測の原則と未確認範囲の扱いを適用する。また、公開前に行う全体検証については、`agent-toolkit:commit`の`references/publish.md`「検証とCI」の規定に従う。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/verification-scope.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:wi-standards/references/awi-body.md#「完成条件」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/awi-body.md#「完成条件」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/references/publish.md#「検証とCI」` | 判断 | `section:agent-toolkit:*/skills/commit/references/publish.md#「検証とCI」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/check-execution-operations.md#「統合実行ツール経由の起動」` | 判断 | `section:agent-toolkit:*/share/check-execution-operations.md#「統合実行ツール経由の起動」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:plan-mode/references/plan-file-standards.md#「検証と公開工程」` | 判断 | `section:agent-toolkit:*/skills/plan-mode/references/plan-file-standards.md#「検証と公開工程」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/verification-scope.md#「直接影響範囲の類型」` | 判断 | `section:agent-basics:*/skills/check-execution/references/verification-scope.md#「直接影響範囲の類型」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/references/verification-scope.md#「範囲の決め方」` | 判断 | `section:agent-basics:*/skills/check-execution/references/verification-scope.md#「範囲の決め方」` |
| 保存済みの検証結果を再利用する前 | `agent-basics:check-execution/references/verification-scope.md#「実行条件と再実行」` | 判断 | `section:agent-basics:*/skills/check-execution/references/verification-scope.md#「実行条件と再実行」` |

## 実行のしかた

- 計画ファイルで確定した検証行は、`agent-toolkit:plan-mode/references/plan-file-standards.md`「計画した検証の実行」に従って各コマンドを順に実行し、コマンドごとの終了コードと出力の要点を確認する。計画凍結後に作成された訂正済み検証表を引き継いでいる場合は、その表に記載されたコマンド群を実行する。訂正表の書式、保持方法、および引き継ぎ手順は、`agent-toolkit:plan-mode`の`references/plan-file-standards.md`「検証と公開工程」に従う。
- すでに保存されている実行結果を確認する際は、子プロセスを不要に再実行せず保存ログを直接読み取る。保存された標準出力と標準エラー出力は`sed -n`コマンドやファイル読み取りツールで参照し、JSONデータは`jq`コマンドを用いて読み取った上で、取得時点のHEADコミット、カレントディレクトリ（cwd）、および終了コードを同一記録から確認する。読み取り失敗、データの欠落、または終了コードが未記録の状態を成功とみなすことは避ける。
- 先行して取得した成功結果と、修正後の限定再実行の結果を組み合わせる場合は、品質基準の定義元にある「実行条件と再実行」に従って各成功集合の適用可否を判定し、完全なテスト名、取得バージョン、および合否結果を要求事項へ対応付ける。
- テスト結果を完成条件と突き合わせる際は、pytestであれば`-v`オプションを指定し、vitest、Playwright、dotnet testなどではテスト名と実行結果が1件ずつ出力されるレポート形式を用いて実行し、その出力に含まれるテスト名と結果行を根拠として利用する。静的解析などの診断結果は、`check-execution-operations.md`「保存した実行出力の取得」に従い、対象版の公開契約に合う方法で保存ログから読み取る。プログレス表示のドット（点）や成功総数のカウント表示のみに基づいて個別のテストの合否を判定することは避ける。

## 直接影響範囲の類型

品質基準の定義は、`agent-basics:check-execution/references/verification-scope.md`「直接影響範囲の類型」を適用する。

## 範囲の決め方

品質基準の定義は、`agent-basics:check-execution/references/verification-scope.md`「範囲の決め方」を適用する。

## レーンの検証基準版

レーンで変更に着手する直前に取得したHEADを検証の基準版として保持し、保存した実行結果の取得時点と対応付ける。
