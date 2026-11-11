# 画面の設計とユーザビリティの検収

本書は、エンドユーザーが操作する画面を計画、作成、変更、またはレビューするエージェントが参照する共通基準をまとめたものである。
画面の目的や達成すべき成果は、`quality-principles-qcd.md`「QCDと3段判定」に示された「利用主体と利用場面から仕様と設計を導く原則」に基づいて確定する。
画面設計では、主要な操作フローと既存のデザインシステムを調査し、ユーザーの操作から結果に至る一連の流れを定義する。
既存のデザインシステムとして、プロジェクトに存在するものから設計資料（DESIGN.mdなど）、デザイントークンの定義、コンポーネントカタログ（Storybookなど）、利用できるデザインツールのMCP、ユーザーが示したモックアップや画像を調べる。
存在しない資料やツールは作成・導入を求めず、調べた範囲を設計の根拠と併せて残す。

画面設計に関する基準は、本書（共通方針）と`ui-ux-patterns.md`（主題別の設計）で定め、`ui-ux-web-rules.md`はHTML、CSS、JavaScriptによる実装手段やコードから機械的に判定できる条件を扱う。
この役割分担を「画面基準の3層」と呼ぶ。

参考資料: [frontend-ui-ux-skill](https://github.com/overseek944/frontend-ui-ux-skill)、[ux-designer-skill](https://github.com/szilu/ux-designer-skill)、[Web Interface Guidelines](https://github.com/vercel-labs/web-interface-guidelines)。
これらの要点を日本語の判断基準として再構成した。
具体的な数値基準は末尾の一次資料を典拠としている。
 MIT Licenseの著作権・許諾表示は[第三者表示](../../../THIRD-PARTY-NOTICES.md)を参照。
参考資料: [Impeccableの画面設計基準](https://github.com/pbakaus/impeccable/blob/5e745ace7e151feb2a93d360d0281fe39448b857/skill/reference/craft-floor.md)を日本語へ翻訳・再構成した。
Apache License 2.0の著作権・許諾表示は[第三者表示](../../../THIRD-PARTY-NOTICES.md)を参照。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 機能の正しさを確認するテスト層を選ぶ時 | `testing-selection.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 判断の順序・操作と情報・状態と内容に関する判断・作業の前 | `ui-ux-interaction.md` |
| 視覚と構造・装飾を選ぶ基準に関する判断・作業の前 | `ui-ux-visual.md` |
| アクセシビリティと性能・数値の一次資料に関する判断・作業の前 | `ui-ux-accessibility.md` |
| 完了前の実描画確認に関する判断・作業の前 | `ui-ux-rendering.md` |
