# レビューイーの品質基準と運用

レビューイー側の品質基準は、`agent-basics:review-standards`の`references/reviewee.md`で定義している。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。すでに取得済みの同じバージョンの本文がある場合は、再取得せずにそのまま使用する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

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

指摘の採否や修正範囲を確定する前に、現行の作業契約と要求の原本を確認し、維持すべき対象、全体への波及影響、および元々の未完了タスクを把握する。作業契約の凍結後に行われた変更や指摘の採否は、レビュー指摘管理表へ記録する。
作業契約への反映手順は`${CLAUDE_PLUGIN_ROOT}/share/work-contract.md`「更新する時点」に従う。`review_contract`の保持と再生成は、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「`review_contract`の生成」に従う。

- 指摘の対象が、自動処理待ちの未完了な既存WIである場合は、事実確認する前に`agent-toolkit:wi-standards`の`references/states-and-dependencies.md`に従って対象を「保留」状態にして保護する。この保留は、要求に同意したことや本文を変更したことを意味するのではなく、内容を比較検討している間に古い要求に基づいて処理が進んでしまうのを防ぐための処置である

## 修正の分担と計画の保持

- 修正作業を複数のエージェントへ委譲する場合は、変更対象のファイルが重複しない指摘群を別々の委譲先へ並行して割り当て、同一ファイルを変更する指摘は1つの担当へ集約する
- 計画ファイルの`## 実施内容`において、`由来`が`ユーザー指示`または`人間由来のWI`となっている行、および`## 変更履歴`の確認への回答に基づく行は、指摘を反映する場合であっても`実施内容`欄の文面を原文のまま維持する（条件の追加、範囲の絞り込み、終了条件の付与なども変更に該当する）。修正案は、そのレビューラウンドを`由来`とする別の行に記録する。内容が衝突する場合は`agent-toolkit/rules/01-agent.md`「確認を要する事項」に従ってユーザーに確認してから書き換える（反映担当エージェントの独自解釈に委ねると、勝手に条件を追加する変更が合意済みとして扱われてしまうためである）。なお、再発防止ルールの改訂において`judgment-details.md`「規定の適用可否」の判定を経ているものについては、この制約の対象外とする

## 同じ指摘の反復

再レビュー時に指摘を受け取った場合は、修正方針を確定する前に`atk review-table show <表の絶対パス> --track <対象track> --format=jsonl`を実行し、これまでの全ラウンドの指摘と応答内容を確認する。
現在のラウンドのサマリーに記載されている過去のラウンドや行番号の情報を手がかりにして、今回の指摘と過去の指摘について、違反とされた規定や発生条件を比較する。

## 実行レビュー反復での記録

実行レビューの反復においてレビューイーとなったエージェント（`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`冒頭）は、レビュー指摘管理表に登録されたすべての指摘に対して、採否、判断根拠、対応内容、および検証証拠を記録する。委譲元は表内の未応答件数を見て再レビューへ進むかどうかを判定するため、返却メッセージの中で対応完了を述べるだけでは記録したことにならない。採用しないと判断した指摘についても、その根拠を必ず記録する。

## 計画の確定内容の保持

計画に基づく実行工程を担当するエージェント（レーン担当、レビュー修正担当、CI修正担当、およびこれらを統括するエージェント）は、修正内容を確定する前に計画ファイルを読み込む。対象は`## 概要`、`## 実施内容`、`## 要件・外部仕様`、`## 変更履歴`とし、計画で確定した目的、方針、採否、対象範囲、除外・維持事項を維持する。実行工程の独自の判断だけでこれらを覆すと、ユーザーと合意していない成果物が作られてしまう。
既存の実装や規範と矛盾が生じた場合は、計画側（`由来`列と`## 変更履歴`）と矛盾する側を導入した変更について、双方の経緯や由来を調査し、`agent-toolkit/rules/01-agent.md`「方針が衝突する場合の優先順位」を適用する。単に計画側が優先されることや、相手側の由来が不明であることだけを理由にユーザー確認しない。元の要求と明示された認可に基づいて、目的と結果を保てる技術的な解決策を確定できるのであれば、自身の担当権限の範囲内で修正する。担当の権限範囲を超える判断が必要な場合は、計画の記述、矛盾箇所、調査した由来、技術的な代替案を委譲元へ報告し、委譲元は目的の変更やユーザー自身の判断が必要な未確定事項についてのみユーザーに確認する。

## round 3以降の方式見直し

修正案の比較や再レビューの適用範囲については、`agent-basics:review-standards`の`references/reviewee.md`「方式見直しと再レビュー」に従う。各指摘の既存の応答ラベルに理由と結論を記録し、要件や認可の変更が必要な場合は`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`のエスカレーション手順へ報告する。

## レビュー指摘管理表への応答

レビュー指摘管理表が指定された場合は、指定された修正対象の`track`に属する行のみを採否判断および更新の対象とする（ただし検証は表全体を対象に行う）。実行レビューでは、`exec-review`という単一のトラックのみを扱う。
既存の行に記録されている応答本文や対応不要の理由は、`show`コマンドが出力する`row-id`で行を特定し、以下の公開操作を実行してデコード済みの全文を読み取る。
`response`は応答本文、`no-response-reason`は対応不要の理由を表し、改行、日本語テキスト、引用符がそのまま保持される。
以下の例は、行IDが1の場合の実行例である。

```text
atk review-table show <表の絶対パス> --row-id 1 --field response
atk review-table show <表の絶対パス> --row-id 1 --field no-response-reason
```

応答の記録は、`atk review-table respond <表の絶対パス> --row-id <ROW_ID>`の形式で実行する。`<ROW_ID>`には、`atk review-table show <表の絶対パス>`で確認した`row-id`を指定する。指摘に対応する場合は`--response-file <対応内容PATH>`を、対応不要とする場合は`--no-response-reason-file <理由PATH>`を指定する。`--response-file`と`--no-response-reason-file`を同時に指定できず、どちらを指定したかによって対応の要否が決まる。互換性のための指定方法として`--round <ラウンド番号> --track <区分>`で行を指定する場合、同じ`round`と`track`の中で行を一意に特定できなければ、`--location-file`と`--issue-file`も併せて指定する。
互換指定で使用する両ファイルには、デコード済みの指摘箇所と指摘内容を記述する。表の各セルはJSON文字列として保存されているため、raw TSVの値をそのまま渡すと一致する行が0件になってしまう。渡す値は、`atk review-table show <表の絶対パス> --track <レビュー区分> --round <ラウンド番号> --format=jsonl`の出力から取得する。

応答本文には、以下の各ラベルを行頭に配置し、同じ行に続けて内容を記述する。ラベルが欠けている場合や、ラベルの内容が空である場合、`respond`コマンドはエラーとなり記録に失敗する。対応する場合の応答本文には、以下の4つの項目を含める。

- `違反を確認した規定:`指摘で違反が示された規定、契約、または要件と、その所在
- `同じ規定の検索:`次のレビュー担当者が修正の網羅性を判断できるようにするための検索母集団、修正対象の結論、証拠の再取得先。検索方法、候補となった箇所と除外した理由、検索結果が0件だった場合の再検討プロセスを証拠として残す
- `採用する修正範囲:`実際に修正した箇所と、その範囲の修正によって違反が解消する理由
- `採用しない修正方針:`指摘で提案された手段や検討した案のうち採用しなかったものと、その理由。該当するものがない場合は「なし」と明記する

対応不要とする場合の本文には、以下のラベルを含める。

- `根拠の所在:`不採用と判断した根拠となる結論、および記録や現物の再取得先（パスや位置情報）。調査のプロセスは証拠として残す。根拠をすぐに入手できない行については、「修正要否の立証」で定める証拠が不足している範囲と、それを取得するための具体的な手段を記述する

引数の渡し方や保存本文の検証ルールについては、本スキルの`SKILL.md`「レビュー指摘管理表の共通操作」に従う。

## 完了報告

採否、修正方針、対応結果、および検証結果は、指定されたレビュー指摘管理表および成果物へ記録する。委譲元へ返すメッセージの文面や返却値の形式については、該当する委譲元の`<役割名>.subagent.md`の規定に従う。委譲先以外のエージェントは、本書の判定基準のみを適用して自身の作業工程を進める。

再レビューを優先する条件や、機械的な修正・詳細な実装の再レビューを省略できる条件は、`agent-basics:review-standards`の`references/reviewee.md`「方式見直しと再レビュー」の定義に従う。完成条件証拠で「未達」または「根拠不足」となった条件について再判定が必要な修正は、優先条件として扱う。低リスク判定を追加の省略ルートとして利用する場合に限り、作業開始時点からのすべての変更パスを以下のコマンドで判定する。

判定は、対象worktree上で`atk run-script agent-doc-changes -- --repo <対象worktree> --review-risk <開始時点> <HEAD>`を実行して確認する。
変更されたすべてのパスが、エージェント向け文書に該当しない`.md`または`.md.tmpl`ファイルである場合は「低リスク」、それ以外の場合は「通常」と判定される。Gitの管理外の変更を受け取ったラウンドは「通常」として扱う。
単なる回答の下書きを作成しただけでは、リスク段階を変更しない。
再レビューを省略する場合であっても、変更範囲の確認と`agent-basics:review-standards`の`references/reviewee.md`「公開可能性の検証」を実施し、採否、応答、および検証証拠をレビュー指摘管理表へ記録する。
通常段階で要件や仕様に関わる変更が含まれるラウンドにおいて、コード、設定、制御フロー、公開契約、期待値、実装方針を変更する修正や、判断に迷う修正をした場合は、必ず再レビューへ進む。

## レビュー指摘の位置付け

品質の定義については、`agent-basics:review-standards`の`references/reviewee.md`「レビュー指摘の位置付け」に従う。

## 指摘発生時の扱い

品質の定義については、`agent-basics:review-standards`の`references/reviewee.md`「指摘発生時の扱い」に従う。

## 改善効果を指定する要求の扱い

品質の定義については、`agent-basics:review-standards`の`references/reviewee.md`「改善効果を指定する要求の扱い」に従う。

## 恒久ルールへの反映判定

品質の定義については、`agent-basics:review-standards`の`references/reviewee.md`「恒久ルールへの反映判定」に従う。

## 修正要否の立証

品質の定義については、`agent-basics:review-standards`の`references/reviewee.md`「修正要否の立証」に従う。

## 安全な修正

品質の定義については、`agent-basics:review-standards`の`references/reviewee.md`「安全な修正」に従う。

## 公開可能性の検証

品質の定義については、`agent-basics:review-standards`の`references/reviewee.md`「公開可能性の検証」に従う。
