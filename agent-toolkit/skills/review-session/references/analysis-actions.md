# 振り返りの判断基準：原因と対策

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文で基準を使う資料の選択・取得時 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文で基準を使う資料の選択・取得時 | `agent-toolkit:user-confirmation-and-report/references/analysis-operations.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/analysis-operations.md` |
| 本文で基準を使う資料の選択・取得時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文で基準を使う資料の選択・取得時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-session/SKILL.md` |
| 本文で基準を使う資料の選択・取得時 | `agent-basics:bugfix/references/root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 本文で基準を使う資料の選択・取得時 | `agent-basics:bugfix/references/root-cause-prevention-requirements.md`、`agent-basics:bugfix/references/root-cause-actions-hierarchy.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-actions-hierarchy.md` |
| 本文で基準を使う資料の選択・取得時 | `agent-basics:bugfix/references/root-cause-scenarios-investigation.md`、`agent-basics:bugfix/references/root-cause-severity-quality.md`、`agent-basics:bugfix/references/root-cause-scenarios-recurrence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-investigation.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-severity-quality.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-recurrence.md` |
| 本文で基準を使う資料の選択・取得時 | `../../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文で基準を使う資料の選択・取得時 | `agent-toolkit:wi-standards/references/analysis-records.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/analysis-records.md` |
| 通読で比較するとき、または規範の未適用と目的逸脱を分析する前 | `agent-toolkit:review-session/references/analysis-operations.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/analysis-operations.md` |
| 効果を記録する前 | `../../wi-standards/references/states-and-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md` |
| 類似見直し前 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 経験を知見として保存・再利用する前 | `agent-basics:writing-standards/references/investigation-experience.md`、`agent-basics:writing-standards/references/agent-documents-basics-placement.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-experience.md`、`read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-placement.md` |

## 原因と対策

検証の困難と採否理由は元要求・証拠へ対応付け、記録前の実行失敗はセッション記録で補う。
採否件数だけで品質を判定せず、個別の検証手順と共通品質基準へ改善先を分け、既存AWIへ渡す。

再発・悪化を問う入力と、記録の中で同種の修正の反復や過去対策との共通構造を認めた場合は、個別候補の採否の前に次の2節で分析単位と比較範囲を確かめる。
`agent-basics:bugfix/references/root-cause-severity-quality.md`「前提の再検討」を適用する。
`agent-basics:bugfix/references/root-cause-scenarios-recurrence.md`「反復した指摘の原因判定」も併用する。
同じ機構への局所修正が続く場合は、入力や症状の違いを越えて、最初の警告、受領後の行動、必要な対応、元作業の結果、対策変更が加えた負担を1つの連鎖として比較する。
ユーザーの再発指摘やファイル名の一致を待たず、通読と変更履歴から共有する前提を評価する。
比較した改善は、観測した事象、次の同種作業を改善する変更、反映先、確認できる結果へ対応付ける。
学んだ判断は既存の索引と主題別記録へ観測日・対象版・出所付きで保存し、対策の登録・実装・配布と実利用効果を区別する。
有用な成功知見、誤った記録の訂正と重複・参照の整理も読込表の経験基準で選ぶ。
同じ条件に有効な保存済み知見は再利用し、新しい教訓や重複するAWIを作ること自体を目標にしない。

通常・batchの両方で、採用済みAWIの効果を、元セッションの実際の操作・結果と比べて確かめる。
見直す項目は、確認待ちの一覧（`atk wi list --state=adopted --effect-check=pending`）と`atk wi grep --state=adopted`の検索から、振り返りの担当が選ぶ。
対象のセッションで問題が続いているか悪化している場合は、その問題を目的にして採用された項目を先に選び、他の項目より前に判定する。
続いている問題には、所要時間の分析で悪化していると判定した巡回の所要、同じ種類の指摘やユーザーの介入の反復、同じ不具合の再発が当たり、問題の種類は所要に限らない。
選んだ項目が変更したファイル（`atk wi show`が示す対応commit）と、対象のセッションでの実際の操作（`atk run-script session-review-evidence`の照会）は、その場で確かめる。

判定では、その項目の目的（H1直下に書かれた、何をどれだけ改善するか）の結果を確かめる。

- 目的の値が採用の前後でどう変わったかを、同じ原因が残っているか、適用の条件が満たされていたか、1回あたりの所要と回数、仕事の量に分けて比べる。
  対象の区間が短くなっていないことだけで効果不足とせず、回数や仕事の量が増えた場合はその原因へ帰属させる（`agent-basics:bugfix/references/root-cause-prevention-requirements.md`「再発防止策の必須性」の区分に従う）。
- その変更が、所要（足した工程、待ち）、複雑さ（足した状態、設定、条文）、不具合（その変更を直すための修正と作業項目）を増やしていないかを、変更履歴とキューの検索で確かめる。

規範以外の仕組みも対応commitと通常操作から判断し、読込や起動だけで効果を確認済みとしない。
判定は「効果を確認」「効果不足（同原因の再発・未適用・範囲不足）」「適用機会なし・観測不能（確認待ち）」「後日の確認不要」とし、適用機会、観測位置、未確認または不要の理由を残す。
目的を達していない項目と、増やした負担が効果を上回ると判定した項目は、効果不足として保存し、是正のAWIを登録する。
是正のAWIは、元の変更をやめる案と単純にする案を先に比べる（`../../wi-standards/references/drafting-completion-decisions.md`「採否と実現方式の確定」の効率改善の規定に従う）。
効果不足は本節の過去対策比較へ渡す。
判定の保存、是正AWIの指定と保存結果の取得は、`../../wi-standards/references/states-and-dependencies-effect.md`「採用済みAWIの効果記録」に従う。
変更を実装したセッションの中で測れる部分（同じ条件での前後の比較）は、そのAWIの完成条件で確かめる。
巡回の所要のように実装の後の巡回でしか測れない部分は、後の振り返りが採用の前の巡回と後の巡回を比べて確かめ、効果確認へ保存する。
先に選んだ項目と判定は、振り返り結果報告の「効果確認」の行に書く。
対応commitや適用機会が不明なら、効果未確認として残す。
通読で既知の対策に対応する作業を認めた場合は、既存記録から比較する。

`analysis-cost.md`「コストの無駄の確認」の採否・根拠を再利用し、品質と費用の判断結果を分けて保持する。
改善機会の見送りは、維持すべき成果、作業量、代替案、採否の比較に対応付ける。
選別した問題と所要分析の律速条件は、発見元の分類・過去事例の有無を問わず`agent-basics:bugfix`を起動して共通手順へ渡す。
選別・見送りは、同スキル起動時の`SKILL.md`の初動基準（QとC・Dの区別、重大度、再発防止策の必須性、不採用根拠）で判断する。
介入の判定は`../../user-confirmation-and-report/references/analysis-operations.md`に従う。
規範の未適用・目的逸脱・反復性は`agent-basics:bugfix/references/root-cause-scenarios.md`で判定する。
類似事例の見直しは`agent-basics:bugfix/references/root-cause-remediation.md`に従う。
処置の階層は`agent-basics:bugfix/references/root-cause-actions.md`に従う。

過去の対応項目は、`candidates.md`の同種記録と、由来や状態を限定しない`atk wi grep --summary-only --state=all --target-repo=<対象リポジトリの絶対パス> --skip-pull -F -- '<語>'`が返す候補から取る。
本文は`atk wi show`で確かめる。
記号を正規表現として扱うときだけ`-F`を外し、`'<Pythonの正規表現>'`を渡す（終了コード1は一致0件の正常終了）。
同種記録は文章の大半の一致で検索し、表現の異なる是正事例を本文検索で補う。
取得記録と今回の状況を同スキルの「過去対策との比較」へ渡す。
比較の根拠は証拠、結論・再取得先は原因分析と振り返り結果報告へ記録する。
反復した問題では、過去の項目名、前回適用した階層、解消されずに残った条件も書く。
未完了AWIへの投入要否は「既存キュー項目との重複確認」で個別に判定する。

振り返り結果報告への記録方針:

過去項目との比較では、今回と過去の目的、事象、原因、適用条件、実装・配布状態、および処理後の観測を対応付ける。
`similar_records`が空の場合も、本文を検索して現物と比較する。
比較結果は、未反映、未適用、範囲不足、適用後の同原因、別原因、効果未確認に分け、根拠の記録位置を残す。
別原因の事象を過去対策の不足と断定せず、適用機会の無い対策は効果未確認とする。
実装・検証と後日効果、検出策の実測と文書試行を区別する。
完成条件が後日の効果観測を明示する項目は、観測条件と再開手段を保持して公開工程の延期adoptへ渡す。
一般の効率改善には長期観測を一律に加えない。

- 共通判定で除外したユーザー介入、WI記入欄の是正、確認事項は、`判定済み`理由の先頭に除外区分を書く。
- 候補の表示と報告の追跡には、`SKILL.md`の候補IDと報告規約を使う。
- 分析後に見送った項目は、欠陥でないもの、原因が未確定のもの、処置を採用しないものを分け、それぞれの根拠を「対策を見送った問題」に書く。
  見送りの根拠は、`agent-basics:bugfix/references/root-cause-prevention-requirements.md`「再発防止策の必須性」が無効とする根拠以外に限る。
  再発防止策なしで完了とするかは`agent-basics:bugfix/references/root-cause-severity-impact.md`「QとC・Dの区別」で判断する。
  品質への影響、重大度、探索範囲と限界、許容する残存リスク、両系統の類似見直し・再発防止策の候補と採否、不採用の理由と費用対効果、反復性の有無を記録する。
  明示要件や硬い制約を満たす手段がない場合は、費用による見送りと区別し、未達として`未確定`の行に書く。
  未検証の仮説や未確定の原因を見送りの理由にする項目では、検証AWIのファイル名か、採否を左右しない理由を併記する（`agent-basics:bugfix/references/root-cause-remediation.md`「類似見直し」の仮説の扱い）。
- 欠陥への恒久処置は通常のAWIとして起草する。
  1件の再発防止策で複数の問題を覆う場合も、それぞれの根本原因と処置の対応を分析記録と対策要求に残す。

起草と原因分析の検収は`../../wi-standards/references/analysis-records.md`「原因分析の記録先」の書式で行う。
対策・回帰防止策・再発防止策の各行を`agent-basics:bugfix/references/root-cause-actions-hierarchy.md`「処置の階層」で区別する。

報告、確認、UWIの選定手段、回答待ちの手順は`agent-toolkit:user-confirmation-and-report`に従う。

### 情報探索の問題

情報探索の証拠、原因、対策は、bugfixスキルの`agent-basics:bugfix/references/root-cause-scenarios-investigation.md`「情報探索の問題」で確定する。
対策要求には、探していた情報、探索した区間、参照した文書の順、定義元、探索を妨げた要因を書く（資料の陳腐化が原因なら、参照した記述、現行仕様との差、判断への影響も書く）。
これらを根拠の絶対パスと記録位置に対応付けて渡す。
AWIの各節の記述分担は`agent-toolkit:wi-standards`、原稿と根拠の検収は`share/add-wi.parent.md`「受領と検収」に従い、振り返り結果報告からAWIのファイル名で直接たどれるようにする。

## 既存キュー項目との重複確認

既存の未完了AWIで覆う場合も登録と今回条件での解消を分け、残る原因条件、処置担当、適用機会と観測結果を「確定した問題と対策」の同じ行へ書く。

完了していない既存のキュー項目との比較は、`../../wi-standards/references/drafting-completion-duplicates.md`「重複判定」に従う。
既存項目の作用機構が、今回の具体案の事象、根本原因、完了条件をすべて覆うかを確かめる。
完全に覆うなら新規投入を見送り、対象のファイル名と根拠を「確定した問題と対策」に書く。
一部しか覆わないときや新しい証拠があるときは、不足分の採否を確定して起草する。
本文の削減、読取手段、hookの発火、会話圧縮の時機などは別々の作用機構なので、同じコスト負担分類でも個別に網羅性を判定する。
過去の対策の有効性と反復の有無は「原因と対策」で判定する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 対策と影響範囲の基準を適用する前 | `agent-basics:bugfix/references/root-cause-actions.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions.md` |
| 場面別の原因分析の基準を適用する前 | `agent-basics:bugfix/references/root-cause-scenarios.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios.md` |
| 振り返りの判断基準：比較して探す観点の基準を適用する前 | `analysis-cost.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/analysis-cost.md` |
