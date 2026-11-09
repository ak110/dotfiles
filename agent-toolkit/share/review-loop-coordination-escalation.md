# 実行レビュー反復の調整：エスカレーションの事由別の扱い

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-responses.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-severity-impact.md`、`agent-basics:bugfix/references/root-cause-prevention-verification.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-prevention-verification.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-recording-verdicts.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-verdicts.md` |
| process-wi中に既存不良による続行不能への処置を判断する前 | `agent-toolkit:process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |

## エスカレーションの事由別の扱い

残作業や追加の対策に関して判断要請を受けた場合は、完成条件の変更や除外を判定する前に、元の要求、公開契約、および想定される利用場面に基づいてその処理が本来属する契約を確定する。
追加機構の必要性については、`agent-toolkit/share/rules/common/01-agent.md`「QCDと3段判定」、ならびに`exec-review-responses.md`「レビュー指摘の位置付け」の根拠基準を適用する。
完成条件内に特定の障害名が明記されていないことのみをもって所属を判断しない。
元の契約がその処理を要求していないと一意に確定できる場合は、適用範囲の解釈としてその根拠を同一の担当へ返却する。
障害発生時の品質保証が元契約に含まれている場合は、発生頻度の低さのみを理由に除外せず、下表に示す完成条件の変更・除外の認可基準に基づいて判定する。
調査を経ても契約上の所属が確定しない場合は、`../skills/user-confirmation-and-report/references/user-confirmation-and-report-decisions.md`「確認要否の判定」を適用し、ユーザーのみが決定できる残余事項を確認する。

| 事由 | 委譲元の処置 | 再開位置の記録 |
| --- | --- | --- |
| 計画の条項とWIの明示条件の衝突 | WIの明示条件から契約が一意に定まる場合はWIを優先して確定し、同一のレビューイーへ返却する。人間由来の要求に対する解釈が分かれる場合は、ユーザー確認によって確定させてから返却する | 不要 |
| 選定結果の`担当モデル`とAWI本文のモデル指定の不一致 | 保存済みの選定原本における該当項目の`担当モデル`のみを本文の担当とモデルの組み合わせへ修正し、初回の配分および既存の判断履歴は保持する。`atk run-script pick-wi-check -- <元の選定原本の絶対パス> --work-dir <対象リポジトリの絶対パス>`を実行し、終了コードが0になることを1回で確認する。生成側に未確定のモデルを推測させず、下位担当を追加起動して不一致を補うことは避ける。`実装担当`の値が変更された場合は、引き継ぎ記録と専用リソースを保持したうえで返却元の担当の終了を確認し、同一レーンの回復処理として修正後の値を`model_type`に渡して再起動する。`実行レビュー担当`のみが変更された場合は、同一スレッドへそのまま続行指示を返却する | 不要 |
| 完成条件の変更または除外の判断要請 | 後述の「技術判断で外せる完成条件」に該当する場合は、メインが技術判断と根拠を担当へ返却して記録させる。該当しない場合はユーザー判断の記録を確認し、存在しなければ`agent-toolkit:user-confirmation-and-report`「手段の選択」に従ってユーザー確認を行う。ユーザー判断の出所と受理条件は`agent-toolkit:/share/exec-review-recording.md`「失効の根拠」に従う | 必要 |
| ユーザーの選好、要件または認可範囲の変更 | ユーザー確認して回答を取得し、同一のレビューイーへ返却する | 不要 |
| 外部状態または権限の不足 | 回復手段を確認し、ユーザーによる操作が必要な場合はユーザー確認を実施する。既存不良が原因なら、下記の既存不良の行も適用する | 必要 |
| 同一論点に関する2回目以降の差し替え | 指摘内容と変更実体を突き合わせて変更の意図と影響範囲を確認し、それでも確定できない場合に限りユーザー確認を行ってから修正する。反復回数のみを確認の判断条件にしない（`agent-toolkit:/share/exec-review-responses.md`「同じ指摘の反復」） | 必要 |
| 技術的に解消可能な実装欠陥 | レビューイーが同一工程内で修正を実施する | 不要 |
| 対象リポジトリや配布物のhook・CLI・規範などの既存不良による続行不能 | 担当の回復と欠陥の処置を分け、process-wiの実行中は`../skills/process-wi/references/immediate-fix.md`「即時対応」の要否を判定する。それ以外は`agent-toolkit/share/rules/common/01-agent.md`「完遂と先送り」に従う | 必要 |
| 同一スレッドを継続できない場合 | 後続の担当へ同一の計画ファイル（`~/.claude/plans`配下）、レビュー指摘管理表、および確定済みの判断事項を引き継ぐ。既存不良が原因なら前行の欠陥の処置も判定する | 必要 |

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| process-wi中に既存不良による続行不能への処置を判断する前 | `agent-toolkit:process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |

「再開位置の記録」が「必要」とされている項目では、計画ファイルを`~/.claude/plans`へ保存し、`atk run-script plan-progress --`を実行して`## 進捗ログ`に完了した工程、続行できない理由、および再開位置を記録する。
キュー項目は現在の状態のまま維持する。

完成条件の判断をUWIによって待機する場合は、`../skills/wi-standards/references/wi-standards-states.md`「状態と依存」の承認型UWIと元項目の規定に従って元AWI全体をそのUWIへの依存で待たせる。
UWIに対象項目のファイル名、対象の詳細説明、必要な回答内容、および回答によって分岐する結果を記録し、回答済みUWIの取り込み処理へ接続する。
回答後の再開工程は計画ファイルの進捗ログに記録し、計画ファイルが存在しない場合は`atk wi edit <元AWIのファイル名> --append --body-file <絶対パス> --target-repo=<対象リポジトリ>`を実行して元AWIの本文へ追記する。
ユーザーの回答が得られる前に失効を承認して統合工程へ進めることは避け、回答を待たずに着手可能な他項目を並行して進める。
この回答待ちは、前述したキュー項目を変更しない再開記録とも、すべての要求を不採用とする却下（reject）とも区別して扱う。

回答が得られた後は、既存のレビュー指摘管理表とラウンド番号を引き継いで処理を再開する。
担当内部で解決可能な指摘管理表への登録漏れ、判定行の記載不備、もしくは検証失敗については、同一の担当へ自己修正を促す。
エスカレーションとして扱う対象は、担当内部では補完できない判断、認可、入力情報、または外部環境の不足に限定する。

## 技術判断で外せる完成条件

完成条件の変更または除外は、以下の条件をすべて満たす場合に技術判断で見直せる。
対応する要求の由来と、変更する観測方法・判定水準の由来を区別する。
条件を満たさない場合はユーザー判断の記録を探索し、記録が存在しない場合はユーザー確認を実施する。

- 変更または除外する部分がユーザー自身の指定ではなく、エージェントによる具体化であること。
  対応する要求が明示由来でも、その目的、対象範囲、期待結果、明示された検証水準を維持すること。
- 条件の変更または除外が、`agent-toolkit/share/rules/common/01-agent.md`「協調と自律」に定められた「ユーザーが確定した判断の変更」に該当しないこと。
- ユーザーのみが保持する判断情報を必要としないこと。
  基準は`../skills/user-confirmation-and-report/references/user-confirmation-and-report-decisions.md`「確認要否の判定」の3に従う。
  資料の不足については、同節の2において技術判断の対象外と整理されたものに限る。
- 重大度に応じた類似見直しおよび再発防止に不可欠な結果と品質を維持すること。
  基準は次の資料に従う。

  - `agent-basics:bugfix/references/root-cause-severity-impact.md`「QとC・Dの区別」
  - `agent-basics:bugfix/references/root-cause-prevention-verification.md`「類似見直しと再発防止の完了」
  確認手段は同じ必要水準を満たす別の手段へ変更できる。

この判定は完成条件の意味に対する実質的な評価である。
`source`を持つAWIで目的達成に不要なエージェント起草条件は、レーン担当が上記の境界を確認して除外し、その判断を記録して報告する。
それ以外の変更・除外の判断要請はメインエージェントが判定する。
統合時にはメインエージェントが記録と境界を確認する。
変更・除外の根拠として、エージェントによる具体化であることに加え、上記すべての条件を満たすことを示す。

記録の保存先は判断した時点で選択する。
計画の確定前は対象AWIの実施内容の根拠欄へ、実装着手後はレビュー指摘管理表へ、旧条件、新条件または除外範囲、保持する要求、判断主体と根拠を記録する。
計画なしでレーン担当が不要条件を除外する場合は引き継ぎ記録へ記録する。
技術的制約による変更ではその制約も記録する。
レーン担当の除外は、計画確定後の進捗ログと`review_contract`にも記録し、AWIの処理結果へ反映する。
変更後の条件は観測結果に基づいて判定する。
対話中の判断については、対象発話の逐語内容、ならびに同一発話の反復と区別するための時点および文脈情報を、その証拠を利用する担当へ引き渡す。
会話証拠の解決、既取得の出所の再利用、取得失敗時の明示指定、失効根拠の書式とユーザー由来の受理条件は、`exec-review-recording-verdicts.md`「失効の根拠」に従う。
メインエージェントは出所の抽出処理の完了を待たずに回答を返却する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| ユーザー確認とユーザーへの報告の基準を適用する前 | `../skills/user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| AWIとUWIの共通規範の基準を適用する前 | `../skills/wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
