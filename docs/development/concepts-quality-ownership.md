# 方針・意向: 品質基準の独立提供

## 品質基準の独立提供

コード・設計・テスト・レビュー・文章・原因分析・検証の品質基準はagent-basicsを定義元とする。
WI・委譲・承認・記録・工程管理はagent-toolkitが担い、toolkitからbasicsへ依存する。
単独導入ではtoolkitの運用機構を必要としない。
既存の技術別読込表、必要資料、標準ツール、単体HTMLのCDNと依存管理の判断を保つ（2026年10月21日のユーザー回答）。
実行時の規定は両プラグインの各スキル、構造と理由はdesign-agent-basics.mdにある。

規範をどちらのプラグインに置くかは、次の基準で決める（2026年10月28日のユーザー回答）。

- agent-basics: 成果物（報告・回答を含む）と判断の品質基準を置く。
  作る（writing-standards）、確かめる（check-execution）、評価する（review-standards）、原因を分析する（bugfix）を扱い、ユーザー入力を判断材料としてどう評価するかも含む
- agent-toolkit: ユーザーとのやり取りの作法（確認の要否・時機・出し方、応答の順序や態度）と、作業の進め方（WI、委譲、認可、公開、振り返り、ツール運用）を置く
- 迷ったときは「ユーザーや工程がなくても、成果物や判断の良し悪しを決める規定か」を問い、決めるならagent-basicsに置く

この基準により、原因分析・レビュー・文書作成の場面での確認と応答の作法はagent-toolkitの`user-confirmation-and-report`、`review-standards`、`writing-standards`と常時規範`01-agent.md`に置く。
報告・回答の正確さと、ユーザー入力を判断材料として検証する基準はagent-basicsに置く。
