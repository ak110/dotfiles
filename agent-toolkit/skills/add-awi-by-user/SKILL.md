---
name: add-awi-by-user
description: >
  ユーザーが本スキルを明示的に手動起動し、AWI（エージェントワークアイテム）の
  具体化と登録を依頼したときだけ、要件の裏付けと対話による確定を経て登録する。
disable-model-invocation: true
---

# AWI投入

本スキルは、ユーザーが手動で起動して対話形式でAWIを登録・作成するための機能である。
本セッションにおいて対象リポジトリに対して直接編集できる成果物は、WIの登録や更新に限定される。
リポジトリ本体への恒久的な変更（コード実装、規範、ドキュメント、設定の変更など）は、すべてAWIとして起草して後続工程へ引き渡す。
WIの内容と同期が必要な作業も、登録する要求項目に含める。
対象リポジトリの実装作業、実装用worktreeの作成、AWIステータスの`processing`への変更、実装担当エージェントの起動は、後続の`agent-toolkit:process-wi`が担当する。
WI作成工程における入力、責務、完了基準（出口）は、`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に準拠する。
本スキルは協調モードで動作する。
このセッションは、ユーザーと追加費用なしに対話できる最後の機会である。
後続で必要になるユーザー判断はUWIの投入と回答待ちを生み、費用と提供時期を大きく悪化させるため、登録時点で分かる論点は今確認する。
確認するのは共通の確認要否判定でユーザー固有と定まる事項に限る。
この場で求められた設計案、回答、ユーザーの選好の確定はこのセッション内で完結させ、後続で行うべき変更要求事項をAWIとして保存する。

手動作成の登録条件は、`../wi-standards/references/wi-standards-body.md`「起票目的別の調査出口」に従う。
本スキルでは、同条件を満たす調査、対話による確定および登録をメインエージェントが担当する。
URLや文書などの参考資料だけを受け取った場合は、その内容を参考に起動したリポジトリ（起動時のcwdが属するリポジトリ）を改善するAWIの投入要求として扱う。
資料との対応付けは起動したリポジトリから始め、その成果物（コード、規範、文書、設定）が資料の主題をどう扱っているか（記述の有無と正誤）で関係を判定する。
主題語がコードに現れるかどうかでは判定しない。
他のリポジトリの見直しは、起動したリポジトリの改善から派生して行う（規範の不足を直す場合に、その規範に従って作られたプロジェクトに同じ問題が無いかを見るなど）。
他のリポジトリだけへ投入する場合も、起動したリポジトリの採否と理由を記録する。
資料の目的と具体的な手法を、対象の現在と改善後の操作手順・便益へ対応付ける。
提供機能・消費形態と4方式の比較は、方針予告前から`references/omission-check.md`「資料起点の比較」に従う。
改善の対象は資料が扱う問題と各リポジトリの状況から定める。
資料の全項目との対応を確認した後も、同じ問題に関係する実行経路・制約・利用場面から資料外の改善候補を検討する。
候補の十分性は`agent-basics:writing-standards/references/design-heuristics-proportionality.md`「問題と手段の比例性」の共通基準で判断する。
名前や目的が似た条文の存在は、同じ利用条件で必要な結果を得られる根拠と分ける。
規範・手順による既存充足は`agent-basics:writing-standards/references/agent-documents-basics-ownership.md`「既存規範による被覆の判定」で判定する。
採否を左右する主張には、その手法を実装した一次資料・コード・テストを必要な範囲で確認する。
機能差だけで追加を選ばず、対象での必要性と見込める便益から採否を決める。
追加要求があれば併せて扱い、投入先が一意に定まらない場合は手順2で確認する。

起動後の入力は要求として扱い、動作を選ぶモード引数は設けない。
目的や範囲を変える選択、要件の曖昧さ、選好、不足する認可は手順2で確認する。
範囲を限定せずに資料だけを渡す依頼は、資料の全目的を改善候補とする範囲が確定した依頼として扱い、目的が複数あることや本人の用途が別に書かれていないことだけでは確認せずに調査・比較・起草へ進む。
目的の衝突、依頼本文の限定範囲と資料の目的の不一致、認可の不足など本人の判断が残る場合は、`../user-confirmation-and-report/references/grilling.md`の評価軸の選定に従って確認する。
扱った目的は作業完了報告の`### 自ら確定した判断`へ示す。
AWI登録の認可はスキル起動で成立しているため、確定した目的内の技術手段は自ら決め、本文全文の提示による一律の登録可否確認と独立した方針検収を挟まず、手順3の意味検収と保存まで進める。
投入前レビューで方針段階から本文段階へ進む場合は、`${CLAUDE_PLUGIN_ROOT}/share/wi-draft-review.parent.md`の`本文確認`を使う。

正式なAWI登録には、対象リポジトリと既存キューの読み取り権限、`atk`コマンド、参照資料が必要である。
これらが利用できない環境では、対話によって要求を整理した原稿を作成して返却し、登録前の下書きとして取り扱う。
既存項目との比較、保留処理、登録実行、保存結果の検収は、それらの機能が備わっているClaude CodeまたはCodex環境で実施する。
要求の文章表現は、[文章の品質基準](references/quality-standards.md)に沿って確認する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 外部資料に基づく案の提案・採否、資料起点の既存充足を評価する前 | `agent-basics:writing-standards/references/design-heuristics-proportionality.md`、`agent-basics:writing-standards/references/design-heuristics-external-evidence.md` |
| 実ユーザー発話やWI記入欄の指摘を受領・分類する前（操作対象外: ユーザー発話・回答の受領） | `agent-toolkit:user-confirmation-and-report/references/analysis-operations-intervention.md` |
| 観測された問題を起点とする要求で方針を確認する前 | `agent-toolkit:wi-standards/references/analysis-records.md` |
| 観測された問題を起点とする要求で方針を確認する前、考慮漏れの指摘を受けて原因を分析する前、参考資料と照らして対象リポジトリの不足・未対応の有無を判定する前と、報告や結論への異議を受けて分類する前 | `agent-basics:bugfix/SKILL.md` |
| 既存の規範・手順で充足しているかを判定する前 | `agent-basics:writing-standards/references/agent-documents-basics-ownership.md` |
| WIの方式・原稿・操作・実施状況・過去判断への質問を受けた時（平叙文の状況照会を含む） | `../user-confirmation-and-report/references/user-utterance-questions.md` |
| 考慮漏れ処置後の方針再確認で論点ごとの確認要否を判定する前 | `../user-confirmation-and-report/references/user-confirmation-and-report-decisions.md` |
| 対象リポジトリの外部へ成果を反映させる要求の終了状態を判定する前 | `../wi-standards/references/awi-body-completion.md` |
| 成果物をAWIの登録と更新に限る契約を適用する前（操作対象外: ツールと結び付かない判断） | `../user-confirmation-and-report/references/user-utterance-correction.md` |
| 対象リポジトリの外部に対する操作の認可と確認要否を判定する前 | `../user-confirmation-and-report/references/approval-scope.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 手順2で質問ラウンドを始める前 | `agent-toolkit:user-confirmation-and-report`の`references/grilling.md` | | | |
| WI作成の調査に入る前（手順2の1） | `../wi-standards/references/drafting-workflow.md`、`../wi-standards/references/drafting-workflow-investigation.md`、`../wi-standards/references/drafting-workflow-requirements.md`、`../wi-standards/references/drafting-completion-decisions.md`、`../wi-standards/references/drafting-completion-scope.md`、`../wi-standards/references/drafting-completion-confirmation.md`、`../wi-standards/references/drafting-completion-policies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-investigation.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-requirements.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-decisions.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-scope.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-confirmation.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-policies.md`、`bash:atk wi submission begin` | |
| 認可・解釈・観測の裏付けに必要な出所を再取得する時 | `../wi-standards/references/drafting-workflow-evidence.md` | | | |
| 原稿を確定する前 | `../wi-standards/references/drafting-workflow-writing.md`、`../wi-standards/references/drafting-workflow-validation.md`、`../wi-standards/references/drafting-completion-validation.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-writing.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-validation.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-validation.md`、`wi-standard-dry-run`、`wi-standard-commit` | |
| 独立した確認に入る前 | `../wi-standards/references/drafting-workflow-review-route.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-review-route.md`、`wi-standard-self-review` | |
| 保存操作に入る前 | `../wi-standards/references/drafting-workflow-saving.md`、`../wi-standards/references/drafting-completion-duplicates.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-saving.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-duplicates.md`、`wi-standard-commit` | |
| 原稿の意味を確認する前 | `../wi-standards/references/draft-semantic-review.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/draft-semantic-review.md`、`wi-standard-self-review` | |
| 文脈を分けるためWI投入担当へ委譲する場合 | `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md` | 判断 | `delegation`、`read:agent-toolkit:*/share/add-wi.parent.md` | |
| 独立した方針検収または本文検収を依頼する前 | `${CLAUDE_PLUGIN_ROOT}/share/wi-draft-review.parent.md` | 判断 | `delegation`、`delegation-result`、`read:agent-toolkit:*/share/wi-draft-review.parent.md` | |
| 手順3の起草・保存、指摘・追加発話への対応、投入の中止・再開、完成判定の前 | `references/drafting-and-feedback.md` | | | |
| 完成条件や採否を決める値を測定する前 | `references/measurement.md` | | | |
| 参考資料に対応する実物の調査を始める前、起草の方針を決める前（手順2の2。提示・確認の有無と、方針を伝えるかによらない）、質問ラウンドを終える前と原稿を確定する前 | `references/omission-check.md` | | | |
| 観測された問題の証拠収集・件数集計を始める前 | `agent-basics:bugfix/references/causes.md`、`../user-confirmation-and-report/references/user-utterance.md` | | | |

## 手順

不具合・ユーザーの指摘・エラー・hookの誤動作を起点とする要求は、調査前に`agent-basics:bugfix`を起動する。
原稿や既存の要求分類より先に原文から利用主体・開始状態・到達結果を確定する。
bugfixの取得済み本文は再利用し、5つの問いは今回の各事象へ個別に適用する。
WIの起草・投入前検収は、wi-standardsの意味と必要事項に関する品質水準を用いる。

1. `agent-toolkit:wi-standards`を起動する。
   AWIの適切な粒度、由来の管理、投入および取得の手順は同スキルの基準に従う。
   調査を始める前に、起動したリポジトリと既知の投入先を`--target-repo`引数に列挙して`atk wi submission begin`を実行する。
   投入先を追加・変更したら、担当や原稿の状態にかかわらず、調査・採否確認中のリポジトリと未保存の要求の投入先をすべて並べて再実行する（`begin`は集合を置き換え、開始時刻を保つ）。
   記録の保持中は、該当リポジトリを対象とする`atk wi process-loop`が投入の完了を待機する。
   登録0件でも手順4の終了検査と報告用要約の取得まで進む。
   ユーザーからの依頼が未完了の既存AWIの更新や既存AWIに対する具体的な懸念を含む場合は、質問や調査をする前にwi-standardsの状態遷移ルールを適用する。
   `inbox`や`hold`にある項目はステータスを保留にして更新し、`processing`にある項目への変更要求は新しいAWIとして切り出す。
2. `agent-toolkit:user-confirmation-and-report`を起動し、`references/grilling.md`に基づく質問ラウンドを次の順で行う（案の前に、ユーザーが重視する結果と残る判断をそろえる）。
   要件や結果を変える未確定事項がなくなるまで2の2から2の4を繰り返す。
   1. 調査（案の前提を観測で固める）: 読込表「WI作成の調査に入る前」の資料を読む。
      明示・合意済みの要件は`../user-confirmation-and-report/references/user-utterance-incorporation.md`「原文の保持と取り込み」に従い、理由説明の主観的な評価と区別して確定済み要件として扱う。
      - 質問する前に、具体的に分かっている既存AWIがある場合はその本文を取得して比較する。
        ユーザーが持ち込んだ新規主題の未知検索は省略し、作業中に別に観測した運用不具合と既存項目との比較は`../wi-standards/references/drafting-completion-duplicates.md`「重複判定」に従う。
        既存AWIで網羅された要求は確定済みとして扱い、新規の要求や既存要件との不整合を議論する。
      - 引数なしでスキルが起動され、直前の会話の応答で各項目の対応方針がすでに示されている場合は、その内容を登録対象の入力として受け入れ、提示済みの基準でも判断がつかない項目のみを質問する。
      - ユーザーの依頼が範囲を示す表現や網羅的な列挙と、具体例や理由の説明を兼ね備えている場合は、要求単位を確定する前に該当規定を適用する。
        規定は`../user-confirmation-and-report/references/user-utterance-incorporation.md`「原文の保持と取り込み」に従う。
      - ユーザーがモデル、ツール、担当エージェント、操作手順などの手段を指定した場合は、`workflow-phases.md`のどの開発工程（WI作成・計画・実行・実行レビュー）向けかを原文から対応付け、現在のセッション向けの指定はWI作成工程だけに適用して後続工程の要件に含めない。
        後続工程への適用範囲が一意に定まらず要件や完成条件に影響する場合は、工程ごとに確認し、その回答だけを根拠にする。
      - 論文、ベンチマーク結果、他プロジェクトの実績など外部資料を根拠とする改善案を提案・確定する前に、`agent-basics:review-standards`を起動し、外部資料に基づく改善案の評価基準を適用する。
      - 対象リポジトリの外部へ成果を反映させる要求については、反映先ごとの終了状態（コミットまでかプッシュまでか）を`../wi-standards/references/awi-body-completion.md`「完成条件」に基づいて判定する。
        質問ラウンドで確認するのは、反映先の規範や取得済みの認可では決まらず、ユーザー自身の判断が必要な事項に限定する。
      - 観測された問題（失敗、不具合、未対応など）を起点とする要求では、証拠収集・集計の前に、前掲の「場面ごとの確かめ方」「原文の保持と取り込み」に従って問題の主文から母集団を定め、確認UWI、追加修正、ユーザーコメント、是正回答などの現れを先に列挙して、各現れを調べるか調べない理由を記録する。
        要求の目的や対象範囲は本手順で確認し、対処方針は手順3の作成主体が初期調査、原因分析、類似箇所の見直しで具体的な対策を確定した後に選ぶ。
        ユーザーへ確認するのは、原因の特定後もユーザー自身の選択が必要な事項に限る。
   2. 考慮漏れ点検（案が過去に重視された結果と継続費用を損なわない）: 起草の方針を決める前に`references/omission-check.md`の全類型を点検し、(c)と(d)を推奨しない案を含む提示する全ての案へ適用する。
      点検を終えて方針が定まったら、調査を深めて起草する前に、次の4点をユーザーへ短く1回知らせる。
      - 改善の中心に置く成果物と、その理由
      - 継続的な費用（手順、確認、必須の記載）を加える対象
      - 対象から外す成果物と、その理由
      - 実物を調べた成果物の種類と、調べていない種類

      知らせへの返事を待たず、調査と起草を続ける。
      本人固有の質問がある場合は、2の3で質問の前に送る判断材料がこの知らせを兼ねる。
      知らせの後に追加発話が届いた場合は「指摘の分類と処置」で扱う。
   3. 提示と質問（少ない往復で判断を得る）: 特定した課題、依頼どおりの案、種類の違う案、点検済みの推奨を、求められた評価・回答とともに提示し、依存するユーザー判断を質問する。
      回答の深さは`../user-confirmation-and-report/references/user-utterance-questions.md`「問い・提案・疑問形の扱い」に従い、調査結果が要る回答は手順3の調査と根拠の確定後に送る。
   4. 受領（割り込みを不足の信号として扱う）: 回答、追加発話、異議は次の質問の前に「指摘の分類と処置」で処理し、その戻り先から再開する。
   5. 終了（要件と投入先の確定）: 全類型を再点検して終了時報告を出し、要求単位ごとの投入先リポジトリ、種別、趣旨、確定した要件を整理する。
      - 投入先の追加・変更は、手順1の集合更新に従う。
3. `references/drafting-and-feedback.md`「起草と保存」の手順3で、要求を分割・起草し、投入前レビューを受けて保存する。
4. 同資料の手順4で投入作業を終了し、`agent-toolkit:report-completion`で完了を報告する。

## 指摘の分類と処置

指摘・追加発話は`references/drafting-and-feedback.md`「指摘の分類と処置」で扱い、同資料の「セッション境界」を守る。

## 完成条件

`references/drafting-and-feedback.md`「完成条件」を満たしてから終了する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| ユーザー確認とユーザーへの報告の基準を適用する前 | `../user-confirmation-and-report/SKILL.md` |
