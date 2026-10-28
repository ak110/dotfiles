# 事象単位の調査の委譲

本書は、バグ対応、CI失敗対応、または振り返りにおける事実収集において、事象ごとの客観的な観測事実の収集を独立した担当者へ委譲するエージェント向けに、委譲の単位と伝達すべき情報を定める。拡張原因分析の対象外となる不具合であっても、委譲する前に本書を確認する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

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

- 初回の調査では、症状や失敗したジョブを単位として委譲を分割する。各担当からの報告を受けた後、同一原因であると判明したものは統合し、複数の原因が含まれていたものは分割して整理する
- 独立したコンテキストを持つ調査担当者は、委譲元の先入観や仮説に影響されずに調査できる。ユーザー介入に起因する事象は原則として調査を委譲する（直接的原因が観測から自明な単一の介入事象であれば、委譲するかどうかは任意とする）。独立した事象が2件以上存在する場合は、事象ごとに並列で委譲する。それ以外の単一事象を委譲するかどうかは任意とする
- `agent-toolkit:delegation`に従い、委譲プロンプトには事象、期待する契約、実際の結果、発生条件、ログや資料の絶対パス、対象コミット、および再現手順の7項目のみを渡す。委譲元が想定している仮説、原因候補と目される実装箇所、修正案、他の事象の調査結果、類似見直しの母集団、あるいは調査範囲をあらかじめ限定する指示などは渡さない（これらを渡すと、調査担当者がその仮説の検証だけに視野を狭めてしまうためである）
- 調査担当から報告された推定や対策案は有力な候補として検証し、最終的な原因の特定と処置の決定は委譲元エージェントが行う

## 既存不良の調査担当の起動と受領

```text
起動対象: defect-investigation.subagent.md
```

自身の作業によって持ち込まれたものではない既存の不具合について、AWIを起草せずに不具合を発見したエージェント自身が元のコンテキストで修正する場合に、調査担当の起動および受領検収の手順を適用する。コンテキストを分離するのは調査工程のみとし、実際の修正作業は不具合を発見したエージェント自身が担当する。
AWIとして登録して処理する場合（`agent-toolkit:process-wi`における即時対応や、次のセッションへ引き継ぐ不具合など）は、`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`の手順を用いる。
委譲先の手順と返却形式の詳細は、`${CLAUDE_PLUGIN_ROOT}/share/defect-investigation.subagent.md`に定められている。

### 起動方法

独立した不具合が複数存在する場合は、不具合ごとに1件ずつ調査担当を起動する（`agent-toolkit:delegation`の`references/routing.md`における「独立した文脈による事実収集」の原則に基づく）。
`agents_server`の`start`ツールを以下の引数で呼び出す。

| 引数 | 値 |
| --- | --- |
| `cwd` | 不具合を発見したエージェントの作業ディレクトリの絶対パス |
| `subagent_md_path` | `defect-investigation` |
| `extra_params` | 後述の「渡す入力」の各項目 |

### 渡す入力

- `対象の不良`: `agent-toolkit:bugfix`の`references/investigation-delegation.md`「事象単位の並列調査委譲」に定める7項目（事象、期待する契約、実際の結果、症状を観測した所在を含む発生条件、ログと関連資料の絶対パス、対象コミット、再現手順）。発見側エージェントの仮説、原因候補、および修正案は同節の規定に従って入力から除外する
- `引き継ぎ記録先`: 省略時の扱いおよび継続時の運用は、`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う

### 受領と検収

返却される形式は、`${CLAUDE_PLUGIN_ROOT}/share/defect-investigation.subagent.md`の`## 出力`の定めに従う。以下の項目を確認する。

- `状態`が`completed`となっている場合、直接的原因、根本原因、類似見直し、対策、横展開処置、および再発防止策の各項目に具体的な値が記入されていることを確認する
- 調査完了の返却に含まれる直接的原因、根本原因、母集団、ならびに各種処置の根拠と適用条件について、返却に示された`分析記録`の記載箇所およびコードやログの現物と突き合わせて検収する。同一の探索や分析をはじめからやり直すことは避け、情報の不足や内容の不一致がある場合は同一の調査担当者へ差し戻して追究させる。合否の判定基準は、`agent-basics:bugfix/references/root-cause-analysis.md`「直接的原因と事象時点の確定」に従う。同資料の「原因分析の品質確認」「QとC・Dの区別」「類似見直し」「処置の階層」「再発防止策の必須性」「類似見直しと再発防止の完了」「再発防止としてのテスト」の各節も適用する。本書ではこれらの品質基準を重複して定義しない。`拡張原因分析`が`不要`とされた返却についても同一の基準で検収する
- `続行できない理由`として報告された内容が、認識の齟齬によって要件や期待結果の変更を伴う未確定事項である場合は、ユーザーへの確認事項としてエスカレーションする

検収に合格した処置内容に従い、不具合を発見したエージェント自身が元の作業コンテキストにおいて修正作業を実施する。検収時に不足が見つかった場合や、修正作業中に調査の前提と矛盾する新たな観測事実が得られた場合は、同一の調査担当者へその不足や観測内容をフィードバックして分析を補完させる。
