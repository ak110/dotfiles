# 変更範囲の検証の対象選定

本書は、変更範囲の検証で実行するチェックやテストを選択するエージェント（計画担当、実装担当、実行レビュー担当、計画を立てずに直接変更するエージェント）に向けて、対象を適切に選定するための判断基準を提供する。
変更範囲の検証では、変更を加えたファイルおよびその直接影響範囲を対象とし、リポジトリ全体の網羅的な検証はCIパイプラインに委ねる。
公開前に行う全体検証については、対象プロジェクトの開発・公開手順と連携して実施する。
直接影響範囲を検証対象から外してしまうと、テストの失敗がpush後のCIで初めて発覚し、修正と再実行の往復作業が増える原因となる。

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
