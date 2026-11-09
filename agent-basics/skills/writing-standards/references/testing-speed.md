# テスト方針（全言語共通）：速度

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-scenarios-testing.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-testing.md` |
| 失敗したテストの撤去を判断する前 | `design-heuristics-necessity.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-necessity.md` |

## 速度

- テストケース間の独立性を維持しつつセットアップ処理を適切に共有し、生成するデータ量や外部プロセスの起動回数を必要最小限に抑える。
- 執筆者はテストの追加・変更時に、同一プロセス内処理、契約上不可避の実時間待機、実プロセス・実Gitなどの境界、外部接続・ブラウザー・大規模結合など、必要な仕事に応じた段階を選ぶ。
  プロジェクトが実測に基づく段階別基準を定めている場合はその値と比較し、固有の数値はプロジェクト側で管理する。
  setup、本体、後始末を分け、準備の反復も全体の費用へ含める。
- 基準を超えるケースと重い段階のケースでは、その層でしか検出できない契約違反、最小構成である理由、起動・待機回数を含む個別の所要見込みを確認する。
  レビュー担当は同じ違反を検出できる軽い層への移動、不変の準備の共有、データ量と起動・待機回数の削減を検討し、重い層には固有の違反と代表シナリオを必要最小限残す。
  この判断は`testing-priorities.md`「検証単位の優先順位」と組み合わせ、実境界で保証すべき契約を維持する。
- 結果確認担当は合否にかかわらず個別・全体の実測を基準と比較し、超過の理由と改善の判断を既存の検証記録へ残す。
  理由のない反復は現在の作業範囲と認可内で改善し、必要な仕事に由来する超過も最小化の余地を評価する。
  比較・分析は`../../check-execution/references/diagnostics-results.md`「読む出力」、原因調査は`../../bugfix/references/root-cause-scenarios-testing.md`「所要の原因調査」に従う。
  改善判断の基準とハング検出のタイムアウトを区別し、固定秒数だけでテストを失敗させる機械チェックは設けない。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| テスト方針（全言語共通）：検証単位の優先順位の基準を適用する前 | `testing-priorities.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-priorities.md` |
