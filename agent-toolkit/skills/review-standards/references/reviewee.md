# レビューイーの品質基準と運用

品質の定義元は`agent-basics:review-standards`の`references/reviewee.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。取得済みの同版本文は再取得せず使う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewee.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/reviewee.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/work-contract.md#「更新する時点」` | 判断 | `section:agent-toolkit:*/share/work-contract.md#「更新する時点」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-review.parent.md#%E3%80%8C%60review_contract%60%E3%81%AE%E7%94%9F%E6%88%90%E3%80%8D` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#%E3%80%8C%60review_contract%60%E3%81%AE%E7%94%9F%E6%88%90%E3%80%8D` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/references/states-and-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/judgment-details.md#「規定の適用可否」` | 判断 | `section:agent-basics:*/skills/review-standards/references/judgment-details.md#「規定の適用可否」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/review-loop-coordination.md#冒頭` | 判断 | `section:agent-toolkit:*/share/review-loop-coordination.md#冒頭` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewee.md#「方式見直しと再レビュー」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewee.md#「方式見直しと再レビュー」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/review-loop-coordination.md` | 判断 | `read:agent-toolkit:*/share/review-loop-coordination.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「レビュー指摘管理表の共通操作」` | 判断 | `section:agent-toolkit:*/skills/review-standards/SKILL.md#「レビュー指摘管理表の共通操作」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewee.md#「公開可能性の検証」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewee.md#「公開可能性の検証」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewee.md#「レビュー指摘の位置付け」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewee.md#「レビュー指摘の位置付け」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewee.md#「指摘発生時の扱い」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewee.md#「指摘発生時の扱い」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewee.md#「改善効果を指定する要求の扱い」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewee.md#「改善効果を指定する要求の扱い」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewee.md#「恒久ルールへの反映判定」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewee.md#「恒久ルールへの反映判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewee.md#「修正要否の立証」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewee.md#「修正要否の立証」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewee.md#「安全な修正」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewee.md#「安全な修正」` |

## ユーザー指摘の受領

指摘の採否と修正範囲を確定する前に現行の作業契約と要求の原本を参照し、保持対象・全体への波及・元の未完了作業を確かめる。凍結後の変更と採否はレビュー指摘管理表へ記録する。
作業契約への反映は`${CLAUDE_PLUGIN_ROOT}/share/work-contract.md`「更新する時点」に従う。`review_contract`の保持と再生成は`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「`review_contract`の生成」に従う。

- 指摘の対象が自動処理を待つ未終端の既存WIなら、事実確認より先に`agent-toolkit:wi-standards`の`references/states-and-dependencies.md`の保留で保護する。保留は同意でも本文の変更でもなく、比較の間に古い要求が処理されるのを防ぐ操作である

## 修正の分担と計画の保持

- 是正を委譲する場合は、変更ファイルが交わらない指摘群を別々の委譲先へ並行して割り当て、同じファイルを変更する指摘は1件の担当へまとめる
- 計画ファイルの`## 実施内容`で`由来`が`ユーザー指示`か`人間由来のWI`の行と、`## 変更履歴`の確認への回答に基づく行は、指摘の反映でも`実施内容`欄の文面を原文のまま保つ（条件の追加、範囲の限定、終了条件の付加も変更に当たる）。是正案はそのラウンドを`由来`とする別行へ記録し、衝突は`agent-toolkit/rules/01-agent.md`の`確認を要する事項`に従ってユーザー確認してから書き換える（反映するエージェントの解釈に委ねると、条件を付加する変更が合意の範囲内として確定してしまう）。再発防止の条文の改訂で`judgment-details.md`「規定の適用可否」の判定を経たものは本項の対象外とする

## 同じ指摘の反復

再レビューの指摘受領時は、修正方針を確定する前に`atk review-table show <表の絶対パス> --track <対象track> --format=jsonl`で全ラウンドの指摘と応答を読む。
現在roundのサマリーにある過去roundと行の情報を到達の手掛かりにし、今回の指摘との違反規定・発生条件を比較する。

## 実行レビュー反復での記録

実行レビューの反復でレビューイーとなったエージェント（`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`冒頭）はレビュー指摘管理表の全指摘へ採否、根拠、対応内容および検証証拠を記録する。委譲元は表の未応答件数で再レビューへ進むかを判定するため、返却文で対応済みと述べても記録の代わりには数えられない。採用しない指摘にも根拠を記録する。

## 計画の確定内容の保持

計画に基づく実行工程（レーン担当、レビュー修正担当、CI修正担当とこれらを統括するエージェント）では、修正を確定する前に計画ファイルの`## 概要`、`## 実施内容`、`## 要件・外部仕様`および`## 変更履歴`を読み、計画が確定した目的、方針、採否、対象範囲、除外・保持を維持する。実行工程の判断だけで反転させると、ユーザーが合意していない成果物が確定する。
既存実装や規範と矛盾する場合は、計画側（`由来`列と`## 変更履歴`）と対立側を導入した変更の双方の由来を調べ、`agent-toolkit/rules/01-agent.md`「方針が衝突する場合の優先順位」を適用する。計画側が上位であることや由来不明だけを確認の理由にせず、原要求と明示認可から目的と結果を保つ技術解決を確定できれば担当の認可範囲で是正する。担当境界を超える判断は、計画の記述、対立箇所、調べた由来と技術案を委譲元へ返し、委譲元は目的側の変更やユーザーだけの未確定値が残る部分だけを確認する。

## round 3以降の方式見直し

比較と再レビュー範囲は`agent-basics:review-standards`の`references/reviewee.md`「方式見直しと再レビュー」を適用する。各指摘の既存の応答ラベルへ理由と結論を記録し、要件・認可の変更が必要なら`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`のエスカレーションへ返す。

## レビュー指摘管理表への応答

レビュー指摘管理表が指定された場合は、渡された修正対象の`track`集合に属する行だけを採否判断と更新の対象とする（検証は表全体が対象）。実行レビューでは`exec-review`の単一trackに限る。
既存行の応答本文と対応不要理由は、`show`が返す`row-id`で行を選び、次の公開操作でデコード済みの全文を読む。
`response`は応答本文、`no-response-reason`は対応不要理由を指し、改行・日本語・引用符を保持する。
次の例は行IDが1の場合を示す。

```text
atk review-table show <表の絶対パス> --row-id 1 --field response
atk review-table show <表の絶対パス> --row-id 1 --field no-response-reason
```

応答は`atk review-table respond <表の絶対パス> --row-id <ROW_ID>`の形式で実行し、`<ROW_ID>`には`atk review-table show <表の絶対パス>`が出力した`row-id`を渡す。対応要では`--response-file <対応内容PATH>`を、対応不要では`--no-response-reason-file <理由PATH>`を添える。`--response-file`と`--no-response-reason-file`は併用できず、どちらを渡したかが対応要否を決める。互換の指定として`--round <ラウンド番号> --track <区分>`で行を指定する場合、同じ`round`と`track`で行を一意に特定できなければ`--location-file`と`--issue-file`も指定する。
互換指定の両ファイルにはデコード後の指摘箇所と指摘内容を書く。表のセルはJSON文字列で保存されるため、raw TSVの値をそのまま渡すと一致する行が0件になる。値は`atk review-table show <表の絶対パス> --track <レビュー区分> --round <ラウンド番号> --format=jsonl`の出力から取る。

応答本文は次のラベルを行頭に置き、同じ行に内容を書く。ラベルが欠けた本文や内容が空のラベルは`respond`が記録せずに失敗する。対応要の本文は次の4つを持つ。

- `違反を確認した規定:` 指摘が違反を示した規定、契約または要件と、その所在
- `同じ規定の検索:` 次のレビュー担当が修正の被覆を判断するための母集団、修正対象の結論と証拠の再取得先。検索方法、候補と除外理由、0件の再検討の過程は証拠へ残す
- `採用する修正範囲:` 修正した箇所と、その範囲で違反が解消する理由
- `採用しない修正方針:` 指摘が示した手段や検討した案のうち採らなかったものと、その理由。無い場合は無いと書く

対応不要の本文は次のラベルを持つ。

- `根拠の所在:` 不採用の根拠の結論と記録・現物の再取得先（パスと位置）。探索の過程は証拠へ残す。根拠を取得できない行では、「修正要否の立証」が定める証拠が不足する範囲と取得する手段を書く

値の渡し方と保存本文の検証は本スキルの`SKILL.md`「レビュー指摘管理表の共通操作」に従う。

## 完了報告

採否、修正方針、対応結果および検証結果は、指定されたレビュー指摘管理表と成果物へ記録する。委譲元へ返す文面と返却値の排他性は、その委譲の`<役割名>.subagent.md`が定める。委譲先でないエージェントは本書の判定だけを適用して自身の工程を進める。

再レビューの優先条件と機械的修正・詳細実装の省略条件は`agent-basics:review-standards`の`references/reviewee.md`「方式見直しと再レビュー」を適用する。WI証拠の`wi_conditions`か`user_requirements`に未達・証拠不足の再判定を要する修正は優先条件として扱う。
同品質基準の低リスク分類を使う場合は、開始時点からの全変更パスが以下の分類で低リスクのときに省略できる。

段階は対象worktreeで`atk run-script agent-doc-changes -- --repo <対象worktree> --review-risk <開始時点> <HEAD>`により求める。
全変更パスがエージェント向け文書に該当しない`.md`・`.md.tmpl`なら低リスク、それ以外は通常であり、Git管理外の変更を受領したラウンドは通常として扱う。
回答下書きだけでは段階を変えない。
省略する場合も変更範囲と`agent-basics:review-standards`の`references/reviewee.md`「公開可能性の検証」を実行し、採否・応答・検証証拠を管理表へ記録する。
通常段階の要件・仕様を含むラウンドで、コード・設定・制御フロー・公開契約・期待値・実装方針を変える修正と判断に迷う修正は再レビューへ進む。

## レビュー指摘の位置付け

品質の定義は`agent-basics:review-standards`の`references/reviewee.md`「レビュー指摘の位置付け」を適用する。

## 指摘発生時の扱い

品質の定義は`agent-basics:review-standards`の`references/reviewee.md`「指摘発生時の扱い」を適用する。

## 改善効果を指定する要求の扱い

品質の定義は`agent-basics:review-standards`の`references/reviewee.md`「改善効果を指定する要求の扱い」を適用する。

## 恒久ルールへの反映判定

品質の定義は`agent-basics:review-standards`の`references/reviewee.md`「恒久ルールへの反映判定」を適用する。

## 修正要否の立証

品質の定義は`agent-basics:review-standards`の`references/reviewee.md`「修正要否の立証」を適用する。

## 安全な修正

品質の定義は`agent-basics:review-standards`の`references/reviewee.md`「安全な修正」を適用する。

## 公開可能性の検証

品質の定義は`agent-basics:review-standards`の`references/reviewee.md`「公開可能性の検証」を適用する。
