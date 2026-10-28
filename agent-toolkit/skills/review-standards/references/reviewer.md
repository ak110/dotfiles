# レビュー担当の品質基準と運用

レビュー担当側の品質基準は、`agent-basics:review-standards`の`references/reviewer.md`で定義している。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。すでに取得済みの同じバージョンの本文がある場合は、再取得せずにそのまま使用する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewer.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/reviewer.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/writing.md#「日本語の書き方」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「日本語の書き方」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/reader-fit-review.parent.md` | 判断 | `read:agent-toolkit:*/share/reader-fit-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/references/origin-and-approval.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-recording.md#「判定の値」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「判定の値」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../check-execution/references/acceptance-scenarios.md` | 判断 | `read:agent-toolkit:*/skills/check-execution/references/acceptance-scenarios.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md#「通常AWIの本文」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「通常AWIの本文」` |
| AWIの新旧形式を判断する前 | `../../wi-standards/references/awi-body.md`の冒頭 | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/awi-body.md#冒頭` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-recording.md#「公開後に成立する条件と任意の判断材料」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「公開後に成立する条件と任意の判断材料」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「レビュー指摘管理表の共通操作」` | 判断 | `section:agent-toolkit:*/skills/review-standards/SKILL.md#「レビュー指摘管理表の共通操作」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewer.md#「適用契約」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewer.md#「適用契約」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewer.md#「基本方針」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewer.md#「基本方針」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewer.md#「初回レビュー」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewer.md#「初回レビュー」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewer.md#「再レビューの場合」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewer.md#「再レビューの場合」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewer.md#「実行レビューの判定」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewer.md#「実行レビューの判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewer.md#「ユーザビリティレビューの判定」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewer.md#「ユーザビリティレビューの判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewer.md#「指摘内容」` | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewer.md#「指摘内容」` |

## 専用レビュータスク契約

受け取った`<役割名>.subagent.md`において、レビュー指摘管理表の絶対パスと`track`が専用レビュータスク契約の入力として明示的に指定されている場合を、「専用レビュータスク契約」と呼ぶ。この契約においてのみレビュー指摘管理表を直接操作し、「完了報告」で定める形式を用いて返却する。表と`track`を受け取った実装担当が行う一般的な妥当性評価や、この契約を受け取らないメインエージェントによる直接レビューは対象外とし、それぞれの評価を求めた工程が定める形式で結果を返す。

専用レビュータスク契約を受け取らないメインエージェントの直接レビューであっても、検証が到達不能な場合は`続行できない理由:`を用いる。具体的には、実機検証、完了条件、または待機手順に基づいて合否を判定する指摘について、対象成果物に許可されたツール、禁止事項、入力前提、通常の状態遷移だけでは検証が物理的に到達不能であると確定した要求を、計画や規範の再設計が必要な問題として報告する場合に適用する。

## 読者別探索

調査対象の分割は、読者別探索の親契約に定められた読取量、共通文脈、想定読者像、品質基準に基づいて行う。各ファイルの比較資料、判定結果、証拠、起動識別子を適切に対応付けた上で、探索結果を検収して指摘を統合する。

文章成果物の日本語については、`agent-basics:writing-standards`の`references/writing.md`「日本語の書き方」を執筆時と共通の基準として適用する。想定読者への適合性や、委譲元・委譲先の役割への適合性については、`${CLAUDE_PLUGIN_ROOT}/share/reader-fit-review.parent.md`に従って読者別探索担当に判定させる。その探索結果の裏付けを検収した上で、指摘を統合する。なお、探索の起動を省略できる差分の条件は同資料が定める。

## 計画と表の優先

計画ファイル、WI、CI記録、および受領した`review_contract`は、実装された差分と独立して比較する。WIで求められている要求が差分に反映されていない場合は種類1の指摘とし、計画ファイルの中で理由を添えて明示的に不採用とされた範囲のみを除外する。設計時に記録された鮮度判断と差分との関係も確認し、現行の内容との食い違い、既存の実装によってすでに充足されている事実、または変更の巻き戻しの危険が観察された項目については、変更履歴を調査する。履歴調査する根拠は、単なる`notice`の表示だけでなく、実際に観察された不一致の事実とする。

計画ファイルの記述とレビュー指摘管理表に記録された応答内容が食い違っている場合は、後から記録された応答内容を現在の確定結論とみなす。計画ファイルと実装された差分の食い違いは種類2の指摘とする。計画ファイル自体の体裁、節の構成や整合性、進捗ログなどは指摘の対象外とし、計画ファイルを更新するかどうかは所有者に委ねる（実装着手後は計画ファイルを直接更新しないため、要件変更に対する実装側の応答はレビュー指摘管理表に記録される）。
元の要求の解釈を変更するような応答は、ユーザー自身による判断が未確定である間は現在の結論として採用せず、前述のユーザー確認へ差し戻す。

### WIの完成条件

実行レビュー担当は新形式AWIについて、レビュー起動元が導いた主要完成状態と補足条件の集合の妥当性を検収し、区別して判定する。旧形式は`agent-toolkit:wi-standards`の`references/awi-body.md`冒頭の対応に従って読む。任意節や引用位置の記録を省いた本文も、記載内容から検収する。WIごとの完成条件については、今回の差分によって成立した条件だけでなく、既存の実装によってあらかじめ成立している条件も含めて、条件ごとに観測された客観的証拠と対応付ける。条件が満たされたとみなせるのは、明確な証拠が存在する条件に限られる。なお、リポジトリ全体の検証やCIの成功については、個別のWIの完成条件としては判定せず、公開工程へ引き渡す。

### ユーザー要求単位

ユーザーコメントやUWIの回答が存在するWIでは、元の発言から切り出した個々の要求単位についても達成状況を確認し、完成条件とは別に記録する。`## ユーザー指摘の逐語引用`は、投入元のセッションですでに解決済みの発話であるため（`agent-toolkit:wi-standards`の`references/origin-and-approval.md`）、`## 反映内容と反映先`や`## 完成条件`を正しく解釈するための背景根拠として読み取る。元の発言にある要求が達成されていない場合は、種類1または種類5の指摘とする。

### 割当外・背景

- `割当外`: WI本文または計画に他のWIへの割り当てが記録された要求単位、および割当先のAWIに引き受けの記録がある要求単位は、達成確認の対象から外す。要求内容と割当先の説明から担当範囲を特定する。保存済みの位置指定や引用も引き受けの根拠として読める。引き受けの記録が見当たらない場合は割当先候補を確認し、引用位置の記録がないことだけを理由に実行レビュー担当は再起草を求めない
- `背景`: 過去の観測結果や経緯のみを伝えており、要求、制約、採否、選好、回答を求める問いを含まない単位は、分類の記録（WI本文の`## 反映内容と反映先`、計画ファイルの`根拠`欄、実装着手後はレビュー指摘管理表）と原文の文脈をそれぞれ個別に確認した上で、確かに背景であると認められる場合のみ`背景`と判定する。文の一部に要求が含まれている単位、依頼全体に影響を及ぼす単位、疑問符や過去形、注記のみを根拠とする単位、および解釈が一意に定まらない単位は背景とはみなさない。本来要求を含んでいる単位を分類記録が背景として扱っていた場合は、種類1または種類5の指摘とする
- `割当外`と`背景`に該当する単位は、区分名を付与した`未達`と判定し、それ以外の単位は`達成`、`未達`、`根拠不足`のいずれかで判定する。判定行と根拠欄への具体的な記録方法は、`exec-review-recording.md`「判定の値」に従う

### 公開後に成立する条件と任意の判断材料

- バージョン更新を完成条件に含むWIは、作業レーンの数にかかわらず、公開工程の完了処理担当がバージョン番号と派生するmanifestを更新して検収する。そのため、実行レビューの時点ではレーンの差分だけで達成を求めず、`根拠不足`と判定して保留付き採用（延期`adopt`）へ引き渡す。公開工程で更新する予定を記載した計画ファイルの記述は、達成の証拠とはみなさない
- リポジトリ全体の検証やCIの成功については、公開工程判定（`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`）に従う。公開、配布、実ホストへの反映が完了するまで実際に観測できない完成条件については、隔離環境での再現や部分的な実行結果を`達成`の根拠とせず、`根拠不足`と判定する。後続工程で実施予定である旨を記しただけの記述も、証拠とはみなさない。ただし、条件が指定する観測手段を用いてその結果自体を直接確認した記録がある行（例えば、対象HEADに含まれるバージョン更新の成果物を直接確認した行など）のみは、この扱いの例外とする
- 公開後にのみ観測可能な条件を`根拠不足`と判定する前に、提示された代替観測が受入シナリオ検証（`agent-toolkit:check-execution`の`references/acceptance-scenarios.md`）の起動条件を満たしているかを確認する。公開前に再現可能な起動条件（起動単位の設定、常駐する子プロセスや外部接続を生成する設定など）を満たしていない代替観測は、観測のやり直しを求める指摘として記録する
- 行全体が`agent-toolkit:wi-standards`「通常AWIの本文」に定める任意の判断材料であると明記されている完成条件（冒頭が「任意の判断材料」などで始まる行）は、観測結果が存在しない場合は`根拠不足`と判定する。この行は統合時にそのまま受理されるため、指摘としては登録しない。ただし、観測結果が存在し、かつ条件を満たしていない場合は`未達`として指摘する。行の一部のみが任意の判断材料である場合は、その部分を除外した条件に基づいて合否を判定する
- これらの行に関する具体的な根拠の記述方法は、`exec-review-recording.md`「公開後に成立する条件と任意の判断材料」に従う

### ユーザビリティレビューとの並行

画面に関する差分が存在する場合、同じ対象HEADに対して独立したユーザビリティレビューが並行して実施される。実行レビューの実施中にその結果がまだ到着していない場合でも、種類5の指摘を登録せず、並行中である旨を報告する。統合する時点で、対象HEADに対する合格判定、または描画手段が存在しないことを示す未確定の記録が存在しない場合に限り、種類5の指摘とする。

## 完了報告

専用レビュータスク契約では、`atk review-table add`を用いて指摘を記録する。コマンド形式は`atk review-table add <表の絶対パス> --round <ラウンド番号> --track <区分> --level <指摘レベル> --location-file <指摘箇所PATH> --issue-file <指摘内容PATH>`とする。引数の渡し方や保存本文の検証ルールは、本スキルの`SKILL.md`「レビュー指摘管理表の共通操作」に従う。

返却メッセージは以下の形式で記述する。

- 正常終了時は、`指摘件数: <非負整数>`と`要件仕様件数: <非負整数>`（そのラウンドで登録した`指摘レベル`が`要件`または`仕様`である件数）の2行を返し、指摘の本文は含めない。`想定外事象:`と`気付いた改善点:`の行は、`agent-toolkit/share/rules-subagent.md`「返却形式の受け渡し」に従って末尾に追加する
- 基準となる文書を読み取る際に切り詰めの通知を受けた場合のみ、続けて`切り詰め: <文書の絶対パス> <全文読解の成否>`の1行を記述する（`全文到達`、または`未到達`とその範囲）。この行が存在しないことは、切り詰めの通知を受けなかったことを意味する
- 処理を続行できない場合は、`agent-toolkit/share/rules-subagent.md`「返却形式の受け渡し」の区分と理由を返す。指摘内容、検証の網羅性、検証結果、および変更の要約は、レビュー指摘管理表側に保持される

## 適用契約

品質の定義については、`agent-basics:review-standards`の`references/reviewer.md`「適用契約」に従う。

## 基本方針

品質の定義については、`agent-basics:review-standards`の`references/reviewer.md`「基本方針」に従う。

## 初回レビュー

品質の定義については、`agent-basics:review-standards`の`references/reviewer.md`「初回レビュー」に従う。

## 再レビューの場合

品質の定義については、`agent-basics:review-standards`の`references/reviewer.md`「再レビューの場合」に従う。

## 実行レビューの判定

品質の定義については、`agent-basics:review-standards`の`references/reviewer.md`「実行レビューの判定」に従う。

agent-toolkitの計画ファイルでは、同節の「計画の検証欄」は計画の`## 検証`にある`変更範囲の検証`行、計画がない場合の検証コマンドは引き継ぎ記録の検証コマンド、リファクタリングの検討結果は計画の`### リファクタリング`を指す。CI失敗の調査記録はCI記録の各項目（「原因分析結果」「公開契約」を含む）を指す。進捗の記録は計画の`## 進捗ログ`である。

## ユーザビリティレビューの判定

品質の定義については、`agent-basics:review-standards`の`references/reviewer.md`「ユーザビリティレビューの判定」に従う。

## 指摘内容

品質の定義については、`agent-basics:review-standards`の`references/reviewer.md`「指摘内容」に従う。
