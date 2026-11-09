# 委譲先の完了報告の受領と検収：受領後の扱い

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 受領した事象を報告・分析の対象に選ぶ前 | `agent-basics:writing-standards/references/quality-principles.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/quality-principles.md` |
| process-wi中の不良・是正要求・改善点を受領した時 | `agent-toolkit:process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/stash-cleanup.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/stash-cleanup.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/exec-review-responses.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/review-loop-coordination-escalation.md` | 判断 | `read:agent-toolkit:*/share/review-loop-coordination-escalation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` |
| 修正後に保存済みの成功した検証結果を再利用する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-reruns.md` |

## 受領後の扱い

1. 保存済みの実行結果（`atk agents wait`の`agent_message_path`、CLIの出力保存先、MCPの結果を保存したファイル、`session-review-evidence`の出力など）を後続の担当者、検査処理、またはCLIへ引き渡す場合は、そのファイルのパスを直接渡す。
   モデルが本文を別ファイルや次のプロンプトへ書き写すと、同一の文章がコンテキスト内へ重複して取り込まれ、書き写しの誤りも発生しやすくなるためである。
   受け取り側は判断に必要な部分のみを読み込み、原本ファイルは後続の参照が終わった後に回収する。

2. 委譲先が稼働している間は、その担当範囲に含まれるファイルを編集しない（並行して編集できる条件は`references/mandatory-rules.md`「作業ツリーの保護」に従う。worktree全体を任せた担当の稼働中は、そのworktreeへ書き込まない）。
   どうしても編集する必要がある場合は、委譲先が終了したことを確認した上で作業ツリーとコミット履歴を点検してから実施する。
   万一競合が発生した場合は、採用する版を確定して統合し、破棄した内容を委譲先へ伝達する。

3. 受け取った事象は、`agent-basics:writing-standards/references/quality-principles-facts.md`「結果の返却」の影響基準で選別する。
   正常回収や反復回数だけを理由とする原因分析・改善項目化は対象外とする。
   旧`想定外事象:`行は履歴として受理し、未達・判断に必要な影響を結果へ残す。

4. 不良・是正要求・改善点を受け取った主体は、起動modeやホスト、階層によらず、process-wi中なら`agent-toolkit:process-wi`の`references/immediate-fix.md`「即時対応」で対処時機を判定する。
   それ以外は`agent-toolkit/share/rules/common/01-agent.md`「完遂と先送り」に従う。
   権限外なら判定材料を委譲元へ渡し、権限内の作業は続ける。
   即時対応では最小の直接修正と正式対応AWIを分け、選定済み要求の正式対応を先送りしない。

5. 完了報告において退避識別子やバックアップ用の複製パスが開示された場合は、その内容も点検し、`agent-toolkit:commit`の`references/stash-cleanup.md`に従って適切に処置する。

6. レビューの指摘事項と、委譲先から追加の対策や残作業について判断を求められた内容の採否は、`agent-toolkit:/share/exec-review-responses.md`を読み込んで確定する。
   元の要求、公開契約、および利用場面からその処理が属する契約上の位置付けを確認し、`agent-toolkit/share/rules/common/01-agent.md`「QCDと3段判定」の必要性判定を適用した上で対策を選定する。
   完成条件の変更や除外へ進むかどうかの判断は、`../../../share/review-loop-coordination-escalation.md`「エスカレーションの事由別の扱い」に従う。
   裏付けを取得できなかった旨が記録されている行の扱いについては同スキルが定める。

   サブエージェントの出力（レビュー指摘や調査結果）は、指定されたレビュー指摘管理表に記録してから修正作業に着手する。
   指摘を追加するエージェントは`track`を指定して記録し、`指摘内容`に実際値、期待値、違反している規範や契約の出典、対象への適用根拠を明記する。
   レビュー対象者は、該当行を一意に特定するキーを用いて応答欄を更新する。
   応答欄の記入時は、事前に`../../../share/exec-review-responses-records.md`「レビュー指摘管理表への応答」を読み、指定された行頭ラベルと構成に従って記述する（ラベル不足時は`atk review-table respond`の実行が失敗する）。
   指摘を採用しない（no）行の`根拠の所在:`には、メインエージェントの判断とユーザーの判断のどちらに基づくかも明記する。

7. 実行中に届いた通知、返却内容（`Agent`ツールの戻り値を含む）や`agents_server`の実行結果に`気付いた改善点:`で始まる行が存在する場合は、受領時点で該当するすべての行を役割に応じて上位のエージェントへ引き渡す。
     箇条書きの項目記号の直後に標識がある行も同じ報告として扱い、引き渡す行は項目記号を除いて標識以降の原文を保つ。
   コード例・引用の中の行は報告として扱わない。
     通知と完了返却の同じ行・対象・観測を同一事象として既存記録へ対応付け、通知時の対処結果を再利用する。
     新しい観測や状況変更だけを追加判定し、完了返却の同行には通知時の対処結果を適用する。
     取得済みの証拠を再利用し、判断に不足する部分だけ補う。
     報告時にはprocess-wi中の対処時機、それ以外のAWI化の要否を必ず判定する。
     process-wi中は`agent-toolkit:process-wi`の`references/immediate-fix.md`「即時対応」で、不要作業を繰り返す具体的な工程や操作と削減量、修正・検証・統合負担を比較し、最小修正・今回追加・次回を確定する。
     報告元の「即時対応不要」だけで追認せず、保存済みAWIの正式名を受領した時点で今回追加の選定反映と担当への追送まで確認する。
     処置の権限と時機は前項に従い、残る分析は証拠とともに引き継ぐ。
     - 規範読込の改善報告: 読込直後に、使わなかった範囲、過大な取得単位、同じ本文の不要な再取得を認めた時点で報告する。
       資料のパス・読んだ契機・利用状況（使った節か不使用）・不要範囲・削減見込み・方法候補を、分かる範囲で短く記す。
       見込みは節や範囲で示し、推定と実測を区別する。
       委譲元は報告者の見立てを根拠と観測条件とともにWI投入担当へ渡す。
       同じ資料の報告は既存項目との被覆を確認してまとめ、資料単位でAWIの要否を判断する。
       処置の時機は前述の即時対応・AWI化の判定を用いる。
     - メインエージェント: 動作モードにかかわらず、次回のユーザー向け発話へ識別子と原文を保持したまま転記し、識別子はインデントを除いた行頭に配置する。
       報告元の情報とメインエージェントが確認できた範囲を引用の外側に補足し、未確認の主張については委譲先からの未検証報告であることが分かる形で明示する。
       作業完了を報告するための報告用UWIは、`agent-toolkit:report-completion`の規約に従って作成する。
     - 委譲先: `agent-toolkit/share/rules/rules-subagent.md`「返却形式の受け渡し」に従って処理する。

8. サブエージェントの動作や運用そのものを分析する場合は、現在稼働しているそのサブエージェントの完了報告も観測対象に含めた上で結論を確定する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 品質判断の原則：事実と推論の基準を適用する前 | `agent-basics:writing-standards/references/quality-principles-facts.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/quality-principles-facts.md` |
