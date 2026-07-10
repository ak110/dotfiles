# フィードバックの計画グルーピング

`agent-toolkit:apply-feedback`ステップ4「検討結果の提示と適用」から参照する、
計画ファイル分割・統合の詳細規定を定める。

## 判定方式

計画のグルーピングはfeedbackファイルのfrontmatter`plan_group`フィールド（任意の文字列）で
明示的に制御する。処理時点でのファイル件数・変更対象ファイル数・本文内容パターンによる
動的判定は行わない。

- `plan_group`が設定されていないfeedbackは、それぞれ独立した1計画として扱う（既定）
- `plan_group`に同一の値を持つfeedback群（同一`target_repo`内に限る）は、
  1計画へ統合する
- `plan_group`の値は投入時（`agent-toolkit:add-feedback`スキル）または
  投入後の`atk fb edit`操作で設定する。値の命名基準は
  `agent-toolkit:add-feedback`スキル「plan_groupの設定基準」節に従う

## 適用時の留意点

- 1計画1コミットの原則は`plan_group`によるグルーピング後の計画単位で維持する
- 境界近接ファイル（`agent-toolkit:agent-standards`「文書サイズ上限」節が定める200〜219行）への
  追記・縮減が同一計画内で複数箇所に及ぶ場合も、グルーピング自体は`plan_group`のみで決定する。
  境界近接対応（縮減対象の追加選定・追記文言の圧縮）は計画本文側の対応として個別に処理する
- 分割・統合の判断はエージェント自律で決定せず、feedback投入時に確定した`plan_group`値に従う。
  処理段階での`AskUserQuestion`は発行しない

## 関連規定と合流点

- 分割は同一セッション内での計画ファイル分離を指し、
  `agent-toolkit/rules/01-agent.md`「セッション分割・別計画化は禁止する」節の対象外
- 計画ごとの実装委譲先・push・後始末の合流点は`agent-toolkit:apply-feedback-finish`スキル本文へ集約する
