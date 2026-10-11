# 既存不良の調査担当の起動と受領

```text
起動対象: defect-investigation.subagent.md
```

自分の作業で導入していない既存不良を、AWIを投入せずに見つけたエージェントが元の文脈で直す場合に、調査担当の起動と検収へ適用する。文脈を分けるのは調査だけとし、修正は見つけたエージェントが行う。
AWIとして投入する用途（`agent-toolkit:process-wi`の即時対応、次のセッションへ回す不良など）は`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`を使う。
委譲先の手順と返却形式は`${CLAUDE_PLUGIN_ROOT}/share/defect-investigation.subagent.md`が定める。

## 起動方法

独立した不良が複数ある場合は、不良ごとに1件起動する（`agent-toolkit:delegation`の`references/routing.md`の独立した文脈による事実収集）。
`agents_server`の`start`を次の引数で呼ぶ。

| 引数 | 値 |
| --- | --- |
| `cwd` | 見つけたエージェントの作業ディレクトリの絶対パス |
| `subagent_md_path` | `defect-investigation` |
| `extra_params` | 「渡す入力」の各項目 |

## 渡す入力

- `対象の不良`: `agent-toolkit:bugfix`の`references/investigation-delegation.md`「事象単位の並列調査委譲」の7項目（事象、期待する契約、実際の結果、症状を観測した所在を含む発生条件、ログと資料の絶対パス、対象commit、再現手順）。見つけたエージェントの仮説、原因の候補、修正案は同節に従い渡さない
- `引き継ぎ記録先`: 省略と継続の扱いは`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う

## 受領と検収

返却の形式は`defect-investigation.subagent.md`の`## 出力`が定める。次を確かめる。

- `状態`が`completed`なら直接的原因、類似見直し、対策、横展開処置および再発防止策の各項目が値を持ち、該当なしの項目はその根拠を持つ
- 完了した返却の直接的原因・母集団・処置の根拠を、現物から独立に検収する。判定は`agent-toolkit:bugfix`の`SKILL.md`「直接的原因と事象時点の確定」と`references/root-cause-analysis.md`「原因分析の品質確認」「類似見直し」「再発防止策の必須性」「再発防止としてのテスト」を使い、本書で合否基準を再定義しない
- `拡張原因分析`が`不要`の返却も同スキルの局所対応の契約で検収する。`根本原因`が`なし`であることと、直接的原因から行う類似見直し・処置の不足は分けて判定する
- `続行できない理由`が認識の違いで要件または結果を変える未確定事項なら、ユーザー確認へ送る

検収した処置に従い、見つけたエージェントが元の文脈で修正する。検収の不足や、修正中に調査の前提と矛盾する観測が出た場合は、同じ調査担当へ不足や観測を示して補完させる。
