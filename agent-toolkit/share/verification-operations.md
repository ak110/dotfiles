# 検証対象の品質基準と記録操作

検証対象の選定に関する品質基準の定義元は、`agent-basics:check-execution/references/verification-scope.md`である。
呼び出し元の読込条件を引き継ぎ、本書の運用条件と条件に該当する正式な資料の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

検証対象を選定する際は、`../skills/wi-standards/references/awi-body-completion.md`「完成条件」に定められた自己観測の原則と未確認範囲の扱いを適用する。
また、公開前に行う全体検証については、`agent-toolkit:commit`の`references/publish.md`「検証とCI」の規定に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 検証の開始前と未コミット結果の再利用前 | `verification-evidence-observation.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-observation.md` |
| 変更範囲の検証対象と直接影響範囲を選ぶ前 | `agent-basics:check-execution/references/verification-scope.md`、`agent-basics:check-execution/references/verification-scope-selection.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope.md`、`read:agent-basics:*/skills/check-execution/references/verification-scope-selection.md` |
| 完成条件の観測手段と未確認範囲を判定する前 | `../skills/wi-standards/references/awi-body-completion.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-completion.md` |
| 公開前の全体検証を選ぶ前 | `agent-toolkit:commit/references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` |
| 検証コマンドを起動する前 | `check-execution-operations-launch.md` | 判断 | `read:agent-toolkit:*/share/check-execution-operations-launch.md` |
| 計画の検証表を作成・訂正・引き継ぐ前 | `../skills/write-plan/references/plan-file-standards-verification.md` | 判断 | `read:agent-toolkit:*/skills/write-plan/references/plan-file-standards-verification.md` |
| 保存済みの検証結果を再利用する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-reruns.md` |

## 実行のしかた

検証の開始前に、`verification-evidence-observation.md`「検証時本文の保存」に従って取得HEAD・変更状態と対象本文を確保する。
保存済み結果の再利用では、その保存物から復元した本文と現在の対象を比較し、履歴・入力・環境など本文以外の条件も確認する。

- 計画した検証は`../skills/write-plan/references/plan-file-standards-verification.md`「計画した検証の実行」に従う。
  計画がなければ引き継ぎ記録のコマンドを使い、同じ実行・判定基準を適用する。
  検証表の書式と訂正・引き継ぎは同書「検証と公開工程」が定める。
- 保存済み結果は、`check-execution-operations-output.md`「保存した実行出力の取得」に従って読み、取得時のHEAD、cwd、終了コードと対応付ける。
  取得できない出力や未記録の終了状態は成功の根拠にしない。
- 先行して取得した成功結果と、修正後の限定再実行の結果を組み合わせる場合は、品質基準の定義元にある「実行条件と再実行」に従って各成功集合の適用可否を判定し、完全なテスト名、取得バージョン、および合否結果を要求事項へ対応付ける。
- テスト結果を完成条件と突き合わせる際は、pytestであれば`-v`オプションを指定し、vitest、Playwright、dotnet testなどではテスト名と実行結果が1件ずつ出力されるレポート形式を用いて実行し、その出力に含まれるテスト名と結果行を根拠として利用する。
  静的解析などの診断結果は、`check-execution-operations-output.md`「保存した実行出力の取得」に従い、対象版の公開契約に合う方法で保存ログから読み取る。
  プログレス表示のドット（点）や成功総数のカウント表示のみに基づいて個別のテストの合否を判定することは避ける。

## 直接影響範囲の類型

品質基準の定義は、`agent-basics:check-execution/references/verification-scope-selection.md`「直接影響範囲の類型」を適用する。

## 範囲の決め方

品質基準の定義は、`agent-basics:check-execution/references/verification-scope-selection.md`「範囲の決め方」を適用する。

## レーンの検証基準版

レーンで変更に着手する直前に取得したHEADを検証の基準版として保持し、保存した実行結果の取得時点と対応付ける。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 検証ツールの実行と保存出力の取得：保存した実行出力の取得の基準を適用する前 | `check-execution-operations-output.md` | 判断 | `read:agent-toolkit:*/share/check-execution-operations-output.md` |
