# 事象単位の調査の委譲

本書はバグ対応、CI失敗対応、振り返りの事実収集で、事象ごとの観測事実の収集を独立した担当へ委譲するエージェントへ、委譲の単位と渡す情報を定める。拡張原因分析の対象外の不良でも委譲の前に読む。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/delegation/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/base-contract.md#「引き継ぎ記録先」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/base-contract.md#「引き継ぎ記録先」` |
| 調査担当の返却を受け取った後に検収する時 | `../../../share/defect-investigation.subagent.md#「出力」` | 判断 | `section:agent-toolkit:*/share/defect-investigation.subagent.md#「出力」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「直接的原因と事象時点の確定」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「直接的原因と事象時点の確定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis.md` |

## 事象単位の並列調査委譲

- 初回は症状か失敗jobを単位に分け、報告後に同一原因を統合し、複数原因を分割する
- 独立した文脈の担当は、委譲元の仮説を知らないまま調査できる。ユーザー介入に由来する事象は委譲する（直接的原因が観測で自明な単一の介入では任意）。独立した事象が2件以上あれば事象ごとに並列で委譲する。それ以外の単一事象は任意とする
- `agent-toolkit:delegation`に従い、委譲プロンプトへは事象、期待する契約、実際の結果、発生条件、ログと資料の絶対パス、対象commit、再現手順の7項目だけを渡す。委譲元の仮説、原因候補の実装箇所、修正案、他事象の調査結果、類似見直しの母集団、調査範囲の限定は渡さない（渡すと調査がその仮説の検証へ狭まる）
- 報告された推定と対策案は候補として確かめ、原因と処置は委譲元が確定する

## 既存不良の調査担当の起動と受領

```text
起動対象: defect-investigation.subagent.md
```

自分の作業で導入していない既存不良を、AWIを投入せずに見つけたエージェントが元の文脈で直す場合に、調査担当の起動と検収へ適用する。文脈を分けるのは調査だけとし、修正は見つけたエージェントが行う。
AWIとして投入する用途（`agent-toolkit:process-wi`の即時対応、次のセッションへ回す不良など）は`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`を使う。
委譲先の手順と返却形式は`${CLAUDE_PLUGIN_ROOT}/share/defect-investigation.subagent.md`が定める（所在参照）。

### 起動方法

独立した不良が複数ある場合は、不良ごとに1件起動する（`agent-toolkit:delegation`の`references/routing.md`の独立した文脈による事実収集）。
`agents_server`の`start`を次の引数で呼ぶ。

| 引数 | 値 |
| --- | --- |
| `cwd` | 見つけたエージェントの作業ディレクトリの絶対パス |
| `subagent_md_path` | `defect-investigation` |
| `extra_params` | 「渡す入力」の各項目 |

### 渡す入力

- `対象の不良`: `agent-toolkit:bugfix`の`references/investigation-delegation.md`「事象単位の並列調査委譲」の7項目（事象、期待する契約、実際の結果、症状を観測した所在を含む発生条件、ログと資料の絶対パス、対象commit、再現手順）。見つけたエージェントの仮説、原因の候補、修正案は同節に従い入力から除く
- `引き継ぎ記録先`: 省略と継続の扱いは`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う

### 受領と検収

返却の形式は`${CLAUDE_PLUGIN_ROOT}/share/defect-investigation.subagent.md`の`## 出力`が定める。次を確かめる。

- `状態`が`completed`なら直接的原因、根本原因、類似見直し、対策、横展開処置および再発防止策の各項目が値を持つ
- 完了した返却の直接的原因・根本原因・母集団・処置の根拠を、現物から独立に検収する。合否基準は`agent-basics:bugfix/references/root-cause-analysis.md`「直接的原因と事象時点の確定」と`references/root-cause-analysis.md`の次の節が定める。対象の節は「原因分析の品質確認」「QとC・Dの区別」「類似見直し」「処置の階層」「再発防止策の必須性」「類似見直しと再発防止の完了」「再発防止としてのテスト」である。本書ではこの基準を再定義しない。`拡張原因分析`が`不要`の返却も同じ基準で検収する
- `続行できない理由`が認識の違いで要件または結果を変える未確定事項なら、ユーザー確認へ送る

検収した処置に従い、見つけたエージェントが元の文脈で修正する。検収の不足や、修正中に調査の前提と矛盾する観測が出た場合は、同じ調査担当へ不足や観測を示して補完させる。
