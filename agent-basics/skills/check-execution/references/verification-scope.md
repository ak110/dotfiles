# 変更範囲の検証の対象選定

本書は、変更範囲の検証で実行するチェックやテストを選択するエージェント（計画担当、実装担当、実行レビュー担当、計画を立てずに直接変更するエージェント）に向けて、対象を適切に選定するための判断基準を提供する。
公開先のCIが全体の試験と静的検査を実行する場合、レーン内・統合時・公開前のローカル検証は、変更ファイルと参照をたどって特定した利用側のテスト・lintに限る。
リポジトリ全体・サブプロジェクト全体・パッケージ全体の試験と、CIが実行する全体の静的検査はCIに委ねる。
ファイル単位の選定が難しい場合も、ローカルでは関連する機能のモジュールまでを上限とし、列挙しきれなかった利用側を検証記録へ残す。
個別の類型やプロジェクト手順も、この上限の内側で対象を選ぶ。
CIが無い、または公開先でCIが実行されない場合の全体検証は、プロジェクトの開発・公開手順に従う。
CIが実行しないチェックと、変更ファイルを対象にするcommitフック・横断テストは、プロジェクト手順で選定する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 必要なテストが収集され実行されたことを個別の結果から確かめ、追加検証を選ぶ前 | `diagnostics-results.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/diagnostics-results.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 直接影響範囲の類型・範囲の決め方に関する判断・作業の前 | `verification-scope-selection.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-selection.md` |
| 実行条件と再実行に関する判断・作業の前 | `verification-scope-reruns.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-reruns.md` |
