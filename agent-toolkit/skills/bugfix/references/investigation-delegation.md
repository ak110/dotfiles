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
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 調査担当の返却の各欄を検収する時 | `root-cause-analysis.md#「QとC・Dの区別」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` |
| 調査担当の返却の類似見直しと処置を検収する時 | `agent-basics:bugfix/references/root-cause-remediation.md#「類似見直し」「処置の階層」「再発防止策の必須性」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-remediation.md#「類似見直し」「処置の階層」「再発防止策の必須性」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「直接的原因と事象時点の確定」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「直接的原因と事象時点の確定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis.md` |

## 調査の進め方と委譲時の入力

- 調査を依頼された担当自身が、似た対象から順に調べ、得た知見を後続の対象へ再利用することを通常の進め方とする。広い所在探索は読み取り専用の`explore`へ分けてよい
- 委譲するかは`agent-toolkit:delegation`の総費用比較で決め、起動・規範と背景の重複読込・指示作成・検収・結論の再構築を含めて有利な場合に限り、似た対象をまとめて渡す。事象の件数や独立性だけで担当を分割しない。報告後は原因が共通する対象を統合し、異なる原因を区別して整理する
- 独立した文脈へ渡す入力は、事象、期待する契約、実際の結果、発生条件、ログや資料の絶対パス、対象コミット、および再現手順の7項目とする。除外するのは委譲元の仮説・原因候補・修正案や、委譲元の推測で調査範囲を限定する指示である。担当内で得た調査結果や知見は、後続対象の調査へ再利用してよい
- 調査担当から報告された推定や対策案は有力な候補として検証し、最終的な原因の特定と処置の決定は委譲元エージェントが行う

## 既存不良の調査担当の起動と受領

```text
起動対象: defect-investigation.subagent.md
```

自身の作業によって持ち込まれたものではない既存の不具合について、AWIを起草せずに不具合を発見したエージェント自身が元のコンテキストで修正する場合に、調査担当の起動および受領検収の手順を適用する。コンテキストを分離するのは調査工程のみとし、実際の修正作業は不具合を発見したエージェント自身が担当する。
AWIとして登録して処理する場合（`agent-toolkit:process-wi`における即時対応や、次のセッションへ引き継ぐ不具合など）は、`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`の手順を用いる。
委譲先の手順は、`${CLAUDE_PLUGIN_ROOT}/share/defect-investigation.subagent.md`に定められている（所在参照）。返却形式は本書「調査担当の返却契約」に従う。

### 起動方法

前節の総費用比較で委譲を選んだ場合、似た不具合をまとめて調査担当へ渡す。件数による固定分割は行わない。
`agents_server`の`start`ツールを以下の引数で呼び出す。

| 引数 | 値 |
| --- | --- |
| `cwd` | 不具合を発見したエージェントの作業ディレクトリの絶対パス |
| `subagent_md_path` | `defect-investigation` |
| `extra_params` | 後述の「渡す入力」の各項目 |

### 渡す入力

- `対象の不良`: `agent-toolkit:bugfix`の`references/investigation-delegation.md`「調査の進め方と委譲時の入力」に定める7項目（事象、期待する契約、実際の結果、症状を観測した所在を含む発生条件、ログと関連資料の絶対パス、対象コミット、再現手順）。発見側エージェントの仮説、原因候補、および修正案は同節の規定に従って入力から除外する
- `引き継ぎ記録先`: 省略時の扱いおよび継続時の運用は、`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う

### 受領と検収

返却される形式は、本書「調査担当の返却契約」の定めに従う。以下の項目を確認する。

- `状態`が`completed`となっている場合、直接的原因、根本原因、類似見直し、対策、横展開処置、および再発防止策の各項目に具体的な値が記入されていることを確認する
- 調査完了の返却に含まれる直接的原因、根本原因、母集団、ならびに各種処置の根拠と適用条件について、返却に示された`分析記録`の記載箇所およびコードやログの現物と突き合わせて検収する。同一の探索や分析をはじめからやり直すことは避け、情報の不足や内容の不一致がある場合は同一の調査担当者へ差し戻して追究させる。合否の判定基準は、`agent-basics:bugfix/references/root-cause-analysis.md`「直接的原因と事象時点の確定」に従う。同資料の「原因分析の品質確認」「QとC・Dの区別」「類似見直し」「処置の階層」「再発防止策の必須性」「類似見直しと再発防止の完了」「再発防止としてのテスト」の各節も適用する。本書ではこれらの品質基準を重複して定義しない。`拡張原因分析`が`不要`とされた返却についても同一の基準で検収する
- `続行できない理由`として報告された内容が、認識の齟齬によって要件や期待結果の変更を伴う未確定事項である場合は、ユーザーへの確認事項としてエスカレーションする

検収に合格した処置内容に従い、不具合を発見したエージェント自身が元の作業コンテキストにおいて修正作業を実施する。検収時に不足が見つかった場合や、修正作業中に調査の前提と矛盾する新たな観測事実が得られた場合は、同一の調査担当者へその不足や観測内容をフィードバックして分析を補完させる。

<!-- return-contract:defect-investigation:start -->
<!-- 生成元: share/defect-investigation.subagent.md「出力」。手動編集しない。 -->
### 調査担当の返却契約

調査が完了したら、次の形式のみを返却する。

```text
状態: completed
分析記録: <詳細な分析を保存したファイルの絶対パス>
拡張原因分析: 要 | 不要
直接的原因: <観測で確定した直接的原因と根拠>
根本原因: <確定した根本原因（破られた不変条件か判断規則）と根拠>
類似見直し: <QとC・Dの区別と重大度の結果、探索範囲・限界・許容する残存リスク、根本原因から導いた母集団と該当対象の一覧、独立走査を適用した場合の担当識別子と起点問題の摘出の有無。各対象の判定根拠、探索方法、履歴の期間と対象外とした範囲、未摘出の原因と見直し経過は分析記録の箇所で示す>
対策: <変更する対象、変更の内容および修正後に確かめる観測>
横展開処置: <類似見直しで該当とした各対象への処置>
再発防止策: <処置と、その処置が作用する根本原因の単位>
```

各欄の値は、`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」に基づいて判定する。`agent-basics:bugfix/references/root-cause-remediation.md`「類似見直し」「処置の階層」「再発防止策の必須性」も適用する。`拡張原因分析`の要否にかかわらず、すべての欄を同一の基準で記載する。
類似見直しの該当件数が0件の場合や、採用すべき再発防止策がないと判断した場合も、同書が求める記録内容を該当欄に記載する。

続行不能と追加報告行は、共通契約`../../../share/rules-subagent.md`「返却形式の受け渡し」に従う。
<!-- return-contract:defect-investigation:end -->
