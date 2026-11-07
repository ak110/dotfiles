---
name: add-awi-by-user
description: >
  ユーザーが本スキルを明示的に手動起動し、AWI（エージェントワークアイテム）の
  具体化と登録を依頼したときだけ、要件の裏付けと対話による確定を経て登録する。
disable-model-invocation: true
argument-hint: "<要求>"
---

# AWI投入

本スキルは、ユーザーが手動で起動して対話形式でAWIを登録・作成するための機能である。本セッションにおいて対象リポジトリに対して直接編集できる成果物は、WIの登録や更新に限定される。リポジトリ本体への恒久的な変更（コード実装、規範、ドキュメント、設定の変更など）は、すべてAWIとして起草して後続工程へ引き渡す。WIの内容と同期が必要な作業も、登録する要求項目に含める。対象リポジトリの実装作業、実装用worktreeの作成、AWIステータスの`processing`への変更、実装担当エージェントの起動は、後続の`agent-toolkit:process-wi`が担当する。
WI作成工程における入力、責務、完了基準（出口）は、`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に準拠する。本スキルは協調モードで動作する。
このセッションは、ユーザーと追加費用なしに対話できる最後の機会である。
後続で必要になるユーザー判断はUWIの投入と回答待ちを生み、費用と提供時期を大きく悪化させるため、登録時点で分かる論点は今確認する。
確認するのは共通の確認要否判定でユーザー固有と定まる事項に限る。
この場で求められた設計案、回答、ユーザーの選好の確定はこのセッション内で完結させ、後続で行うべき変更要求事項をAWIとして保存する。

手動作成の登録条件は、`agent-toolkit:wi-standards`「起票目的別の調査出口」に従う。
本スキルでは、同条件を満たす調査、対話による確定および登録をメインエージェントが担当する。
URLや文書などの参考資料だけを受け取った場合は、その内容を参考に起動したリポジトリ（起動時のcwdが属するリポジトリ）を改善するAWIの投入要求として扱う。
資料との対応付けは起動したリポジトリから始め、その成果物（コード、規範、文書、設定）が資料の主題をどう扱っているか（記述の有無と正誤）で関係を判定する。主題語がコードに現れるかどうかでは判定しない。
他のリポジトリの見直しは、起動したリポジトリの改善から派生して行う（規範の不足を直す場合に、その規範に従って作られたプロジェクトに同じ問題が無いかを見るなど）。他のリポジトリだけへ投入する場合も、起動したリポジトリの採否と理由を記録する。
資料の目的、目的へ作用する具体的な手法、対象の利用場面と実態を対応付け、どの差分が元の目的へ有効か比較する。
改善の対象は資料が扱う問題と各リポジトリの状況から定める。
資料の全項目との対応を確認した後も、同じ問題に関係する実行経路・制約・利用場面から資料外の改善候補を検討する。
候補の十分性は`agent-basics:writing-standards/references/design-heuristics.md`「問題と手段の比例性」の共通基準で判断する。
名前や目的が似た条文の存在は、同じ利用条件で必要な結果を得られる根拠と分ける。規範・手順による既存充足は`agent-basics:writing-standards/references/agent-documents-basics.md`「既存規範による被覆の判定」で判定する。
採否を左右する主張には、その手法を実装した一次資料・コード・テストを必要な範囲で確認する。
機能差だけで追加を選ばず、対象での必要性と見込める便益から採否を決める。
追加要求があれば併せて扱い、投入先が一意に定まらない場合は手順2で確認する。

起動後の入力は要求として扱い、動作を選ぶモード引数は設けない。目的や範囲を変える選択、要件の曖昧さ、選好、不足する認可は手順2で確認する。
資料に複数の合理的な改善目的がある場合は、関連候補と推奨を示して目指す結果を確認する。
AWI登録の認可はスキル起動で成立しているため、確定した目的内の技術手段は自ら決め、本文全文の提示による一律の登録可否確認と独立した方針検収を挟まず、手順3の意味検収と保存まで進める。
投入前レビューで方針段階から本文段階へ進む場合は、`share/wi-draft-review.parent.md`の`本文確認`を使う。

正式なAWI登録には、対象リポジトリと既存キューの読み取り権限、`atk`コマンド、参照資料が必要である。これらが利用できない環境では、対話によって要求を整理した原稿を作成して返却し、登録前の下書きとして取り扱う。既存項目との比較、保留処理、登録実行、保存結果の検収は、それらの機能が備わっているClaude CodeまたはCodex環境で実施する。要求の文章表現は、[文章の品質基準](references/quality-standards.md)に沿って確認する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したら判断や操作の前に指定節と、その成立に必要な前提・例外を自分で読む。全体が判断に必要な場合は全文を読む。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 参考資料から改善候補を起草・採否判定する前 | `agent-basics:writing-standards/references/design-heuristics.md#「問題と手段の比例性」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-heuristics.md#「問題と手段の比例性」` |
| 実ユーザー発話やWI記入欄の指摘を受領・分類する前 | `../user-confirmation-and-report/references/analysis-operations.md#「ユーザー介入と確認の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/analysis-operations.md#「ユーザー介入と確認の判定」` |
| 観測された問題を起点とする要求で方針を確認する前 | `agent-basics:bugfix/references/causal-analysis.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/causal-analysis.md` |
| 観測された問題を起点とする要求で方針を確認する前 | `agent-toolkit:wi-standards/references/analysis-records.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/analysis-records.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/review-standards/SKILL.md` |
| 考慮漏れの指摘を受けて原因を分析する前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 参考資料と照らして対象リポジトリの不足・未対応の有無を判定する前と、報告や結論への異議を受けて分類する前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 既存の規範・手順で充足しているかを判定する前 | `agent-basics:writing-standards/references/agent-documents-basics.md#「既存規範による被覆の判定」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/agent-documents-basics.md#「既存規範による被覆の判定」` |
| 疑問形の異議を受けて要求単位へ分ける前 | `../user-confirmation-and-report/references/user-utterance.md#「問い・提案・疑問形の扱い」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md#「問い・提案・疑問形の扱い」` |
| 考慮漏れ処置後の方針再確認で論点ごとの確認要否を判定する前 | `../user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 観測された問題を起点とする要求で方針を確認する前 | `agent-basics:bugfix/references/root-cause-actions.md#「対策の選定」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-actions.md#「対策の選定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/references/awi-body.md#「完成条件」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/awi-body.md#「完成条件」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/references/drafting-workflow.md#「直接起草と独立した検収の選択」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/drafting-workflow.md#「直接起草と独立した検収の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/references/drafting-completion.md#「対象集合の仕分け」「確定の判定」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/drafting-completion.md#「対象集合の仕分け」「確定の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/references/draft-semantic-review.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/draft-semantic-review.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/references/user-utterance.md#「成果物の制限」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md#「成果物の制限」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/references/approval-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/approval-scope.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 手順2で質問ラウンドを始める前 | `agent-toolkit:user-confirmation-and-report`の`references/grilling.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/grilling.md` | |
| WI作成の調査に入る前（手順2の1） | `../wi-standards/references/drafting-workflow.md`の冒頭、「出所と観測の取得」「投入条件の判定」「前提と観測の確認」「確定した要件と委譲元の判断」「未確定事項」。`../wi-standards/references/drafting-completion.md`の「採否と実現方式の確定」「実現方式の成立」「対象集合の仕分け」「未確定事項と確認」「確定の判定」「方針と判断の記録との比較」 | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/drafting-workflow.md#冒頭「出所と観測の取得」「投入条件の判定」「前提と観測の確認」「確定した要件と委譲元の判断」「未確定事項」`、`section:agent-toolkit:*/skills/wi-standards/references/drafting-completion.md#「採否と実現方式の確定」「実現方式の成立」「対象集合の仕分け」「未確定事項と確認」「確定の判定」「方針と判断の記録との比較」`、`bash:atk wi submission begin` | |
| 原稿を確定する前 | `../wi-standards/references/drafting-workflow.md`の「本文の起草」「保存前の確認」。`../wi-standards/references/drafting-completion.md`の「投入前の読み直し」。新しい通常AWIでは同資料の「重複判定」、観測された不具合を起点としbugfixを起動して起草する場合は「原因と定義の所有者」も読む | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/drafting-workflow.md#「本文の起草」「保存前の確認」`、`section:agent-toolkit:*/skills/wi-standards/references/drafting-completion.md#「投入前の読み直し」`、`wi-standard-save` | |
| 独立した確認に入る前 | `../wi-standards/references/drafting-workflow.md`の「直接起草と独立した検収の選択」。選んだ手順の起動・検収・差戻しの契約 | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/drafting-workflow.md#「直接起草と独立した検収の選択」`、`wi-standard-save` | |
| 保存操作に入る前 | `../wi-standards/references/drafting-workflow.md`の「投入」。`../wi-standards/references/drafting-completion.md`の「重複判定」（通常AWI）と「投入前の読み直し」の対象集合と除外の確認 | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/drafting-workflow.md#「投入」`、`section:agent-toolkit:*/skills/wi-standards/references/drafting-completion.md#「重複判定」`、`wi-standard-save` | |
| 原稿の意味を確認する前 | `../wi-standards/references/draft-semantic-review.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/draft-semantic-review.md`、`wi-standard-save` | |
| 文脈を分けるためWI投入担当へ委譲する場合 | `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` | |
| 独立した方針検収または本文検収を依頼する前 | `${CLAUDE_PLUGIN_ROOT}/share/wi-draft-review.parent.md` | 判断 | `read:agent-toolkit:*/share/wi-draft-review.parent.md` | |
| 完成条件や採否を決める値を測定する前 | `references/measurement.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/references/measurement.md` | |
| 推奨案や確認の選択肢を提示する前（手順2の2）、質問ラウンドを終える前と原稿を確定する前 | `references/omission-check.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/references/omission-check.md` | |
| 観測された問題の証拠収集・件数集計を始める前 | `agent-basics:bugfix/references/root-cause-scenarios.md`の「母集団と件数を数える層」と`../user-confirmation-and-report/references/user-utterance.md`の「原文の保持と取り込み」 | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-scenarios.md#「母集団と件数を数える層」`、`section:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md#「原文の保持と取り込み」` | |

## 手順

括弧内は各ステップが守る結果である。

1. `agent-toolkit:wi-standards`を起動する。AWIの適切な粒度、由来の管理、投入および取得の手順は同スキルの基準に従う。ユーザーからの依頼が未完了の既存AWIの更新や既存AWIに対する具体的な懸念を含む場合は、質問や調査をする前にwi-standardsの状態遷移ルールを適用する。`inbox`や`hold`にある項目はステータスを保留にして更新し、`processing`にある項目への変更要求は新しいAWIとして切り出す。
2. `agent-toolkit:user-confirmation-and-report`を起動し、`references/grilling.md`に基づく質問ラウンドを次の順で行う（案の前に、ユーザーが重視する結果と残る判断をそろえる）。要件や結果を変える未確定事項がなくなるまで2の2から2の4を繰り返す。
   1. 調査（案の前提を観測で固める）: 読込表「WI作成の調査に入る前」の資料を読む。明示・合意済みの要件は`../user-confirmation-and-report/references/user-utterance.md`「原文の保持と取り込み」に従い、理由説明の主観的な評価と区別して確定済み要件として扱う。
      - 質問する前に、具体的に分かっている既存AWIがある場合はその本文を取得して比較する。新規主題で未知の既存項目を探す照会は省略する。既存AWIで網羅された要求は確定済みとして扱い、新規の要求や既存要件との不整合を議論する。
      - 引数なしでスキルが起動され、直前の会話の応答で各項目の対応方針がすでに示されている場合は、その内容を登録対象の入力として受け入れ、提示済みの基準でも判断がつかない項目のみを質問する。
      - ユーザーの依頼が範囲を示す表現や網羅的な列挙と、具体例や理由の説明を兼ね備えている場合は、要求単位を確定する前に`agent-toolkit:user-confirmation-and-report`の`references/user-utterance.md`「原文の保持と取り込み」の該当規定を適用する。
      - ユーザーがモデル、ツール、担当エージェント、操作手順などの手段を指定した場合は、`workflow-phases.md`のどの開発工程（WI作成・計画・実行・実行レビュー）向けかを原文から対応付け、現在のセッション向けの指定はWI作成工程だけに適用して後続工程の要件に含めない。後続工程への適用範囲が一意に定まらず要件や完成条件に影響する場合は、工程ごとに確認し、その回答だけを根拠にする。
      - 論文、ベンチマーク結果、他プロジェクトの実績など外部資料を根拠とする改善案を提案・確定する前に、`agent-basics:review-standards`を起動し、外部資料に基づく改善案の評価基準を適用する。
      - 対象リポジトリの外部へ成果を反映させる要求については、反映先ごとの終了状態（コミットまでかプッシュまでか）を`agent-toolkit:wi-standards`の`references/awi-body.md`「完成条件」に基づいて判定する。質問ラウンドで確認するのは、反映先の規範や取得済みの認可では決まらず、ユーザー自身の判断が必要な事項に限定する。
      - 観測された問題（失敗、不具合、未対応など）を起点とする要求では、証拠収集・集計の前に、前掲の「母集団と件数を数える層」「原文の保持と取り込み」に従って問題の主文から母集団を定め、確認UWI、追加修正、ユーザーコメント、是正回答などの現れを先に列挙して、各現れを調べるか調べない理由を記録する。要求の目的や対象範囲は本手順で確認し、対処方針は手順3の作成主体が初期調査、原因分析、類似箇所の見直しで具体的な対策を確定した後に選ぶ。ユーザーへ確認するのは、原因の特定後もユーザー自身の選択が必要な事項に限る。
   2. 考慮漏れ点検（案が過去に重視された結果と継続費用を損なわない）: 推奨案や確認の選択肢を出す前に`references/omission-check.md`の全類型を点検し、(c)と(d)を推奨案に適用する。
   3. 提示と質問（少ない往復で判断を得る）: 求められた案・評価・回答と点検済みの推奨案を提示し、依存するユーザー判断を質問する。回答の深さは`../user-confirmation-and-report/references/user-utterance.md`「問い・提案・疑問形の扱い」に従い、調査結果が要る回答は手順3の調査と根拠の確定後に送る。
   4. 受領（割り込みを不足の信号として扱う）: 回答、追加発話、異議は次の質問の前に「指摘の分類と処置」で処理し、その戻り先から再開する。
   5. 終了（要件と投入先の確定）: 全類型を再点検して終了時報告を出し、要求単位ごとの投入先リポジトリ、種別、趣旨、確定した要件を整理する。
      - 投入先リポジトリを確定した段階で、すべての投入先を`--target-repo`引数に列挙して`atk wi submission begin`を実行する。投入先を追加・変更したら、WI投入担当の稼働状況や原稿の返却状況にかかわらず、未保存の要求の投入先をすべて並べて再実行する（`begin`は対話セッションの投入記録を渡された集合で置き換えるため）。この記録が保持されている間、該当リポジトリを対象とする`atk wi process-loop`は処理を開始せずに投入の完了を待機する。
3. wi-standardsにおける要求の同一性基準に基づいて独立した要求を分割する。件数の多寡にかかわらず、メインエージェント自身が調査、対策の確定、起草、検収、および保存までを一貫して担当することを原則とする。
   - バグ対応では投入元が原因分析、類似調査と分類、独立別件の具体化・登録、再発防止方針の選定を登録前に終える。実行側は確定した修正と再発防止策を実装し、有効性を検証する。
   - `agent-toolkit:wi-standards`「起票目的別の調査出口」に従って確定した内容をAWI本文に記載する。
   - ユーザーの原文、情報の出所、確定した要件、および技術的な判断を明確に区別し、取得済みの証拠を再利用する。複数の要求を混同せずに個別管理し、同一要求の更新は同じ作成主体が現在の要件と差分情報に基づいて行う。
   - 登録する要求がない場合も、採否を左右する論点の検討を終え、採用・既存充足・不採用と根拠を既存の調査記録で確かめてから手順4へ進む。資料起点の依頼では、資料の主張ごとに既存の定義元、同じ利用条件で同じ結果を得られる根拠（実装・利用結果）、分類（被覆／採用候補／技術判断による不採用／ユーザーの選好を要する不採用）を対応付けた表を作業記録へ残してから結論を出す。選好を要する不採用は、手順2の質問でユーザーの回答を得てから確定する。未調査で案がない状態と、根拠の薄い候補だけを撤回した状態は、原要求の残る検討へ戻る。ユーザーの訂正で候補を撤回した場合も同じように判定する。
   - 本来の作業に付随する別件の登録など、作業の文脈を分ける明確な理由がある場合は、`add-wi.parent.md`に従って委譲を選択する。判断基準が明確な大量の走査処理には軽量な読み取り探索を利用してよく、起草と保存の最終責任はメインエージェントが保持する。
   - 原稿確定前、登録0件で終える前、候補を不採用にする前に`references/omission-check.md`の全類型を再点検し、原文・原稿・調査根拠との対応と処置を更新する。
   - 検収の構成は`agent-toolkit:wi-standards`の`references/drafting-workflow.md`「直接起草と独立した検収の選択」に従う。原文と反映内容を読み比べて構造を確認し、同スキルの`references/draft-semantic-review.md`に基づいて文脈を再確認する。本スキルでは4条件への該当にかかわらず毎回投入前レビューする。同時に準備した原稿、要求内容、出所、証拠を要求識別子で紐付けて`share/wi-draft-review.parent.md`へ一括して引き渡し、作成主体がレビュー指摘の採否を確定した上で保存する。`要求と出所`には起動したリポジトリ（起動時のcwdが属するリポジトリ）を、`成立性の証拠`には対象リポジトリの選定結果（起動したリポジトリの採否と理由、派生して見直したリポジトリ）を含める。
   - ユーザー自身しか決定できない未確定事項のみを手順2の質問に戻す。委譲した調査に不足がある場合は元の調査担当へ差し戻す。
4. `atk wi submission end --findings-file <指摘対応記録の絶対パス>`に投入前レビューの返却ファイルを`--review-result`で添えて実行する。検査が不足を示したら記録を補って再実行し、成功すると投入作業のセッション記録が削除される。表示された報告用要約と、出力された`ファイル名`、登録・再利用の結果、検収結果、未達事項を`agent-toolkit:report-completion`の作業完了報告へ集約して送達する。依頼から自明な後続の実行工程の説明は省き、結果と必要な判断材料を伝える。投入成功の要約を別の最終応答にせず、同スキルが定める終了処理まで終える。同期の振り返りは起動しない。

## 指摘の分類と処置

ユーザーからの指摘・追加発話と、独立した方針検収・本文検収の指摘は、受領した時点で次の手順を行う。修正・再評価・再確認へは、分類と処置を終えてから進む。
作業中・完了後の追加発話と従来の`direct`指定を含む全経路に適用し、報告や結論への異議（「〜ということ」「〜無いの」と問う疑問形を含む）も指摘として扱い（疑問形は同資料の同節に従う）、分類と`agent-basics:bugfix`の起動を候補の再評価・修正・確認より先に行う。

1. 要求単位への分解（確定済みの範囲を勝手に縮小しない）: 受領した発話は、文章の書き方に対する指示や行動の是正も含めて要求単位に分解する。単位ごとに、`agent-toolkit:wi-standards`冒頭の要求の同一性基準に基づいて、既存・起草中のAWIへ合流させるか独立した要求として扱うかを判定する。同一ファイル、同一節、同一文章に対する部分的な重複は、編集上の競合として扱う。発話の一部が既存AWIの更新に該当する場合でも、残りの要求単位は個別に判定する。合流させる要求単位は確定済みの作業範囲と比較し、ユーザー発話が範囲の一部のみに言及している場合であっても、確定済みの作業範囲を勝手に縮小しない。明示的に範囲の縮小が指示された場合や意図が曖昧な場合は、現在の範囲を`共通前提`として明示して確認する。
2. 分類（処置の種類を決める）: 要求単位ごとに次のいずれかへ分類する。追加要求という要求管理上の扱いは、この分類の代わりにしない。
   - 考慮漏れ: 原文、取得済みの調査結果、現物、適用中の規範から導けたのに方針へ反映しなかったもの。手順4で処置する。
   - ユーザーだけが持つ新しい情報: 選好、未開示の制約など、登録前の調査では導けなかったもの。要件として反映する。
   - 前提の誤り: 指摘の技術的前提が現物と一致しないもの。根拠と代替案を示す。
   - 採用しない提案: 検討の結果採らない提案。根拠を示す。
3. 介入評価（ユーザーの手を煩わせた原因を見逃さない）: 分類とは別に、既存判断を是正した介入を`../user-confirmation-and-report/references/analysis-operations.md`「ユーザー介入と確認の判定」で評価する。
4. 考慮漏れの処置（同じ仕組みの漏れを防ぐ）: `agent-basics:bugfix`を起動して原因を分析し、同じ仕組みで漏れうる観点を全方針で見直す。再発防止策を検討してから方針へ反映する。
   再発防止策は本スキルの成果物制限に従い、`## 原因分析`付きの別AWIとして起草し、元のAWIと一緒に登録する。
   投入先は対策の対象を所有するリポジトリとする。
   投入先が元の投入先と異なる場合は、手順2の5に従い、全投入先を指定して`atk wi submission begin`を再実行する。
   確認の往復中も投入作業のセッション記録を維持する。
5. 反映と再確認（新しい判断が必要な点だけを確認する）: 考慮漏れの処置後に方針を再確認する場合も、論点ごとに`agent-toolkit:user-confirmation-and-report`「確認要否の判定」を適用する。既存の方針や技術とQCDで決まる論点は自ら確定して報告し、選好を要する論点だけを確認する。
6. 記録（確認経緯と再発防止策を追跡できるようにする）: 受領した実ユーザー発話（自由記述を含む確認回答を含み、提示した選択肢をそのまま選んだ回答を除く）と投入前レビューの指摘ごとに、指摘対応記録へ項目を残す。
   指摘でない依頼・回答には理由を残す。指摘には前述の分類、分類根拠、採否、処置結果を残し、考慮漏れには原因分析と再発防止策の採否・引渡し先も残す。
   再発防止策を採用しない場合も両系統の候補・費用比較・残存リスクを原因分析へ残し、既存のレビューで回収したことだけを不採用の理由にしない。
   記録の形式は`atk wi submission end --help`に従う。考慮漏れか新しい情報かの判断と処置の十分性はメインと投入前レビューが検収し、`end`は対応と必須欄の欠落だけを検査する。
7. 戻り先: 質問ラウンド中は手順2の2、原稿の確定・検収中は手順3の再点検、完了報告後は手順2の1から再開し、独立した要求は手順3の担当選定に戻す。

本文検収の指摘では、指摘ごとの分類、採否、考慮漏れへの処置結果と登録予定の別AWIを示し、完了報告には確定した判断結果を再利用する。
作業完了報告には、ユーザーが確認経緯と再発防止策を追跡できるよう、`atk wi submission end`が表示した報告用要約（確認の往復回数、指摘ごとの分類、再発防止策の採否と登録先）を省略せず記載する。Claude Codeでは、要約の行が最終応答に無い場合にStop hookが再報告を求める。

## セッション境界

成果物をAWIの登録と更新に限定する契約には、`agent-toolkit:user-confirmation-and-report`の`references/user-utterance.md`「成果物の制限」を適用する。

ユーザーによる中止や依頼の取り下げで投入作業を中止する場合は、保存せずに必ず`atk wi submission end --abort`を実行する。完了後に追加の発話を受けて投入を再開する場合は、投入先リポジトリの確定時に手順2の5の記録処理を再度行い、再開した投入が完了した際は手順4、再度中止となった際は`--abort`で`atk wi submission end`を実行する。
既存AWIの更新と判断した場合は、調査に着手する前に手順1の保留処理を適用し、手順3の同一作成主体が、現行の本文、確定した変更内容、および原文テキストを対象の`ファイル名`に紐付けて更新する。
スキル起動後に届く「反映して」「修正して」という指示や、再発防止・作業完遂の要求は、すべてAWI本文への反映指示として取り扱う。成果物制限の例外として許可される操作は次のものに限る。登録用の一時入力ファイルの作成、作成した一時ディレクトリの`rm -r -- <作成した絶対パス>`による後始末、対象リポジトリの規範が同一工程内で求めている追跡用ファイルへの記載である。また、完成条件や採否判定に用いる測定処理と測定のための可逆な一時変更（`references/measurement.md`）も含まれる。

対象リポジトリの外部に対する操作の依頼範囲、認可の有無、確認要否は、`agent-toolkit/rules/01-agent.md`「完遂と先送り」に従う。あわせて`agent-toolkit:user-confirmation-and-report`「確認要否の判定」・同スキルの`references/approval-scope.md`にも従う。委譲する場合は、起動するスキル名、対象リポジトリ内の恒久変更をAWIに限定する制限範囲、対象外となる通常の判断基準、セッション終了条件を委譲プロンプトに明記する。

## 完成条件

- 登録時点の調査で判明する、要件や結果を左右するユーザー固有の事項について回答を得ていること。処理時に初めて判明する事項だけが、残す理由とともに本文へ記録されていること。
- 登録されたすべてのAWIが、`agent-toolkit:wi-standards`「起票目的別の調査出口」を満たしていること。
- 必要な認識合わせを完了し、手順3の作成主体が要求の性質に応じた適切な検収を経て要求を登録していること。
- 登録0件で終える場合は、資料の主張ごとの対応表と分類が作業記録にあり、選好を要する不採用にユーザーの回答を得ていること。
- `atk wi submission end --findings-file`の検査が成功し、表示された報告用要約を作業完了報告へ記載していること。
- 全類型の点検結果と処置が質問ラウンドの終了時報告に示され、原稿確定前の再点検、意味検収、機械チェック、投入前レビューの合格と保存結果の検収を完了していること。
- 対象リポジトリの実装ファイル群および`processing`ステータスのAWIが、スキル起動前の状態のまま保持されていること。
