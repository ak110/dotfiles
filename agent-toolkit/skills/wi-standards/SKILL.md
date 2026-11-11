---
name: wi-standards
user-invocable: false
description: >
  AWIまたはUWIの起草、読解、採否、状態判断および投入時、または対象リポジトリと
  private-notesを区別してGit操作するときに起動し、人間向けキュー項目の
  共通契約と通常の投入手順を提供する。
  手動投入専用のagent-toolkit:add-awi-by-userを明示起動した場合を除き、まず本スキルを起動する。
---

# AWIとUWIの共通規範

`atk wi submit`の独立受付を受け取った実行は、読込表の投入セッション契約から開始する。
ユーザーがWIを読む場面は、UWIへの回答、AWIの内容確認、処理後の結果確認の3つである。
本文の先頭には、それぞれの場面で知りたいことに答える人間向けの説明を置き、その後にエージェント向けの詳細を続ける。
AWIの冒頭に書く内容は`references/awi-body-content.md`「節の構成と分担」で定める。
目的、変更の要点、変更後の結果を短く示す。
UWIは`references/uwi-format.md`「構成」に従い、処理後は実際に何をどう変えたかを先に示す。
文章表現は`agent-basics:writing-standards/references/writing-common.md`「文章の共通規定」に従う。

ユーザーが読む部分は、AWIのタイトルとH1直下、UWIの本文、`adopt`や`reject`のメモ、および報告用UWIの冒頭である。
タイトルと冒頭を読むだけで目的・処置・結果が分かる状態を合格とする。
UWIでは問い・理由・推奨する回答を、処理後であれば実際の変更と結果をこの基準で判定する。
起草者、検収者および読者別探索担当は、ユーザーがすでに知っている語彙から理解できるかを読み取り、説明不足な語や多義的な語をすべて指摘する。
内部の役割・工程・識別子についても、その箇所で指示対象の種類と役割が分かるように説明する。
一般語を内部の呼称として使う場合や、未説明の別の語へ言い換えた場合も同様の基準で判定する。
保存前の確認と冒頭だけの先行判定は、通常WIでは`references/draft-semantic-review.md`、報告用UWIでは`../report-completion/references/report-formats-uwi.md`「報告用UWIの確認と保存」が定める。
報告用は同スキルが定義するものに限り、`--question-type=post-approval`だけでは区別しない。

AWIの投入は、後続の`process-wi`で処理するエージェントへの作業依頼である。
WIの起草・検収は、`agent-basics:review-standards`「文章成果物の実害」の作業項目の品質水準を適用し、意味と必要事項に集中する。
最終成果物へ転記するときの要件・逐語文言の保持と品質基準の適用は、`references/notation-rules.md`「チェックの対象範囲」に従う。
UWIの投入は、ユーザーへの判断・資料提供・操作・報告内容の確認の依頼であり、報告用UWIもユーザーに内容の確認を依頼する項目である。
次の作業を進めるにあたってユーザーの判断・資料・操作が必要な場面などでの登録要否は、`agent-toolkit:user-confirmation-and-report`「手段の選択」に従う。
UWIには、回答が得られるまで元の作業を再開・確定できない事前承認型と、最善と判断した対応を実施したうえで可否を尋ねる事後承認型がある。
WI作成工程における入力、責務および出口は、`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。
自律モードでのユーザーへの報告と回答はUWIへ記録し、UWIの保存をもって伝達の完了とする（保存する条件と手段は`agent-toolkit:user-confirmation-and-report`「手段の選択」を参照）。
別項目の完了待ちや時間経過待ちはUWIとして扱わず、依存関係や冷却時間で管理する。
WIの投入は、次回以降の`agent-toolkit:process-wi`が処理する要求をキューへ保存する操作であり、通常は投入元のセッションの処理対象には加えない。
同一実行への追加は、`../process-wi/references/lanes-goals.md`「明示された目標からの実行」の必要作業と、`../process-wi/references/immediate-fix.md`「即時対応」で今回追加と判定した正式改善AWIを例外とする。
現在の依頼と承認の範囲で必要な是正はそのセッション内で完遂し、後続の独立した作業要求が残る場合だけ別途WIを投入する。
事象を新しい改善要求へ変える前に、品質・結果やユーザーの判断への影響、または観測した無視できない費用を確かめる。
正常に回収した切り詰めや判断に影響しない数値差だけを起票しない。
未達条件と未回収の入力は保持し、必要な是正と、形式に従って残す分析・点検記録を区別する。
対象リポジトリはAWIの対象となる実装リポジトリを指し、private-notesはキューを保持するリポジトリを指す。
WIはユーザーの手元に残す記録であるため、外部の読者が参照する成果物にこれらの識別子を記載することは避ける（`agent-basics:writing-standards/references/writing-common.md`「文章の共通規定」）。

キューの取得・保存・状態変更するには、対象リポジトリとprivate-notesへのアクセス権、`atk`コマンド、および必要な委譲手段が必要である。
これらを利用できない環境では、提示されたWIの読解と本文原稿の整理までに留め、取得していない既存項目や保存状態は未確認として扱う。
正式な投入・更新・終了処理は、該当機能を利用できるClaude CodeまたはCodexで実施する。
本文の説明は[文章の品質基準](references/quality-standards.md)に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| WIから最終成果物へ転記する前 | `references/notation-rules.md` | | |
| WIの起草・投入前検収の品質水準を適用する前 | `agent-basics:review-standards/SKILL.md` | | |
| 報告用UWIを除くWIの意味確認で、この資料を選択して取得する時 | `references/draft-semantic-review.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/draft-semantic-review.md`、`wi-standard-save` |
| 規範・基準・手順による既存の被覆を判定する前 | `agent-basics:writing-standards/references/agent-documents-basics-ownership.md` | | |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| run-skillの起動入力に独立WI受付があるとき | `../../share/wi-submission.md` | | | |
| 対象リポジトリとprivate-notesを扱う前 | `references/repository-scope.md` | | | |
| 委譲先が`atk wi`でキュー項目の状態を変更する場合 | `references/subagent-queue-ops.md` | | | |
| 通常AWIを起草する前 | `references/awi-body.md`、`references/awi-body-content.md`、`references/awi-body-evidence.md`、`references/awi-body-completion.md`、`references/awi-body-placement.md` | | | |
| 通常AWIの要求を読解・採否判定する前 | `references/awi-body.md`、`references/awi-body-content.md`、`references/awi-body-completion.md` | | | |
| 観測した不具合・ユーザーの指摘・エラー・hookの誤動作を起点とするWIの調査に入る前 | `agent-basics:bugfix/SKILL.md`、`agent-toolkit:wi-standards/references/awi-body-evidence.md` | | | |
| AWIの逐語引用を取り込む前 | `references/awi-body-quotation.md` | | | |
| 外部の既存挙動の再現を完成条件へ採る前 | `references/awi-body-external.md` | | | |
| 報告用UWIを除くWI作成の調査に入る前 | `references/drafting-workflow.md`、`references/drafting-workflow-investigation.md`、`references/drafting-workflow-requirements.md`、`references/drafting-completion-decisions.md`、`references/drafting-completion-scope.md`、`references/drafting-completion-confirmation.md`、`references/drafting-completion-policies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-investigation.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-requirements.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-decisions.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-scope.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-confirmation.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-policies.md`、`bash:atk wi submission begin` | |
| 認可・解釈・観測の裏付けに必要な出所を再取得する時 | `references/drafting-workflow-evidence.md` | | | |
| 報告用UWIを除く原稿を確定する前 | `references/drafting-workflow-writing.md`、`references/drafting-workflow-validation.md`、`references/drafting-completion-validation.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-writing.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-validation.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-validation.md`、`wi-standard-save` | |
| 報告用UWIを除くWIの独立した確認に入る前 | `references/drafting-workflow-review-route.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-review-route.md`、`wi-standard-save` | |
| 報告用UWIを除くWIの保存操作に入る前 | `references/drafting-workflow-saving.md`、`references/drafting-completion-duplicates.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-saving.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-duplicates.md`、`wi-standard-save` | |
| 報告用UWIを除く投入済みWIへ着手する前の対象集合と除外の確認 | `references/drafting-completion-validation.md` | | | |
| report-completionが定義する報告用UWIの原稿確定・意味確認・保存に入る前 | `../report-completion/references/report-formats-uwi.md`、`references/uwi-format.md` | | | |
| UWIを起草する場合 | `references/uwi-format.md` | | | |
| WIの調査・要件分類・由来の検収に入る前 | `references/origin-and-approval-origin.md`、`references/origin-and-approval-comments.md` | | | |
| 逐語引用を扱う前 | `references/origin-and-approval-quotation.md` | | | |
| WIの状態、状態遷移、依存、冷却時間、保留と終了を扱う前。既存WIの更新・修復・採否の見直しが必要と判明した時点や、既存WIに対する具体的な懸念を受けた時点（調査や認識合わせより先） | `references/states-and-dependencies.md` | | | |
| 複数リポジトリへ投入する場合、および同じ成果の整合を要求する複数のAWIを登録する場合 | `references/cross-repository-submission.md` | | | |
| 同じ対象リポジトリの複数項目を一括取得する場合 | `references/wi-show-bulk.md` | | | |
| 通常AWIの投入、または投入済みAWIの内容の更新を、WI投入担当へ委譲するか自分で起草するかを決める前 | `../../share/add-wi-parent-launch.md` | | | |
| WI投入担当を起動する前（委譲すると決めた後） | `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`。起動・受領の契約として全文を読む | | | |

本文は`agent-basics:writing-standards`に従い、引き継いだエージェントが対象と期待する結果を正確に理解して完了まで作業を進められる粒度で書く。
AWIの粒度は要求の同一性に基づいて判断する。
同じユーザー目的・成果に向けた小規模変更は極力1件へまとめる。
技術上の話題、手段、編集箇所の違いだけで分けると、AWIごとに調査・計画・検証・レビューとレーン起動の費用が繰り返されるためである。
既存・起草中のAWIの目的、対象、完成条件の具体化や変更は、該当AWIの更新として扱う。
元の不具合を解消するために必要な追随修正は元AWIへ含め、類似見直しで発見した独立した別件は根本原因を共有していても別AWIへ引き渡す。
元の事象への寄与が判明した対象は元の修正範囲へ戻す。
是正対象を所有するリポジトリが異なる場合は、`../../share/add-wi-parent-inputs.md`「起動前の前提」に従って投入先との組み合わせへ展開する。
1件のAWI内でのバグの扱い単位は、`agent-toolkit:wi-standards/references/analysis-records.md`「原因分析の記録先」に従う。
これら以外の独立した要求は、別の目的を持ち、単独で採否・実装・完成判定が成立する場合に別AWIとする。
同じファイル、節、文を編集することは統合の理由とせず、独立性を判定したうえでの書込競合として扱う。
独立した要求はそれぞれの目的を維持し、先行成果が必須でない組み合わせであれば、どちらの順序で実施してもその時点の成果物に適用して完了できる本文構成にする。
先行成果の必要性は`references/states-and-dependencies.md`で、既存項目による包含関係は`references/drafting-completion-duplicates.md`「重複判定」でそれぞれ個別に判定する。
反映内容と反映先の調査は投入前に完了させる。
記述を短縮する際は、重複表現や判断に影響しない記述を削ることで行い、略語は本文または参照先で定義してから使用する。
文字数や行数の機械的な上限は設けない。

## 工程で参照する運用資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| AWIの原因分析を起草する前、計画ファイル（バグ）を作成する前 | `agent-toolkit:wi-standards/references/analysis-records.md` |
| エージェント向け文書を新しく作る時、新しい規定を追記する時、指示文の改訂が必要と確定した時と、文書の記述量を管理する時 | `agent-toolkit:wi-standards/references/agent-documents-additions.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 通常AWIの本文に関する判断・作業の前 | `references/wi-standards-body.md` |
| 由来と承認に関する判断・作業の前 | `references/wi-standards-origin.md` |
| 状態と依存に関する判断・作業の前 | `references/wi-standards-states.md` |
| 事後承認型UWIに関する判断・作業の前 | `references/wi-standards-post-approval.md` |
| 投入と取得に関する判断・作業の前 | `references/wi-standards-operations.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| ユーザー確認とユーザーへの報告の基準を適用する前 | `../user-confirmation-and-report/SKILL.md` |
