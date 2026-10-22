# 実行レビュー反復の調整

実行レビューの委譲元が、指摘の受領、修正、再レビューおよびエスカレーションを収束まで調整する。
レビューイーは、`agent-toolkit:process-wi`ではそのレーンのレーン担当、`agent-toolkit:plan-mode`のメインによる起動ではメイン、公開工程のCI失敗修正では終端担当が起動する`CI修正担当`とする。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/plan-mode/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.parent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/reviewee.md#「round 3以降の方式見直し」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/reviewee.md#「round 3以降の方式見直し」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.parent.md#「完成条件証拠の受領確認」` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#「完成条件証拠の受領確認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/reviewee.md#「実行レビュー反復での記録」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/reviewee.md#「実行レビュー反復での記録」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/reviewee.md#「同じ指摘の反復」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/reviewee.md#「同じ指摘の反復」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/bugfix/references/root-cause-analysis.md#「反復した指摘の原因判定」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「反復した指摘の原因判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/reviewee.md#「レビュー指摘の位置付け」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/reviewee.md#「レビュー指摘の位置付け」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/SKILL.md#「状態と依存」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「状態と依存」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/SKILL.md#「由来と承認」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「由来と承認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」「類似見直しと再発防止の完了」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」「類似見直しと再発防止の完了」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/exec-review-recording.md#「失効の根拠」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「失効の根拠」` |

## ラウンドの遷移

1. レビュー担当が対象HEADをレビューし、指摘をレビュー指摘管理表へ登録する。指摘がある正常なレビューはcompletedとして修正へ渡す。
2. `${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`の受領手順で、未解決件数0件と達成を要する行の証拠がそろえば収束とする。
3. 指摘があれば、再レビューでは「反復原因の判定」を適用してから、レビューイーへ対応を指示する。レビューイーは現物と比べて全指摘の採否と応答を記録し、採用した指摘の修正、変更範囲の検証および表への証拠記録を済ませる。採用0件も正常な対応完了であり、commitと履歴統合は差分が生じた場合だけ行う（計画ファイルだけの修正は不要）。
4. 同じレビュー担当へ、同じ`開始時のHEAD`（CI記録では`修正系列の開始時のHEAD`）、レビュー基準、初回に受領した`review_contract`（短い直接契約または検証済みYAMLの絶対パス）およびレビュー指摘管理表を渡して再レビューさせる。計画だけから条項を生成した場合は初回の計画を継続する。対象は前回指摘の修正、前回レビューHEADから今回HEADへの修正差分、変更した識別子・契約の直接影響範囲とする。

履歴書換え後は前回と今回の開始時点・HEADの組によるファイル別差分を比較する。
完成条件証拠は毎回全行を新HEADで再判定し、修正が作用しない行も判定維持の理由をevidenceへ書く。
round 3以上の未解決指摘では`agent-toolkit:review-standards`の`references/reviewee.md`「round 3以降の方式見直し」を修正前に適用し、方式を置換した直後の再レビューだけ開始時点からの全差分を対象にする。
修正差分の比較元は前回レビューHEAD、`--review-start`と返却の`レビュー開始時点`は初回から継続する開始時点とする。
履歴書換え後の開始時点には、対応する書換え後のOIDを使う。

前ラウンドの指摘は解決状態と証拠とともに表へ残す。
複数計画の反復では、計画ごとの最後のレビューHEAD・round・表・証拠・返却入力配列を保持する。別計画の認可は`review-contract --generate`で現在OIDへ再生成し、変更後の再レビュー対象は`review-impact`で判定する（`exec-review.parent.md`の入力契約）。
読者別探索の対象と修正範囲は、自計画の判定対象commitの差分とし、他計画のcommitだけが変更した文書はその計画のレビューが判定する。
他計画が書いた記述への候補は、委譲元がその計画のレビュー担当へ1回だけ回し、その担当が同じHEADで読者別探索を終えて判定した結論を採用する。
自計画の変更が参照先・手順・識別子などの前提を変えた場合は、自計画の直接影響範囲として判定する。
受領確認では各返却の`受領確認入力のパス`を`exec-review-evidence-check --batch`へそのまま渡し、複数入力は`--batch`を反復する。process-wiのレーン、メイン自身による計画あり・計画なしの実装で同じ生成済み入力を使う。
各組の必須`external_artifacts`をメイン自身の回答下書きとGit管理外の変更の記録から求めた対象集合と比べ、任意`review_contract`を起動時に渡した契約と比べる。
各組の`reader_fit_review`では、Git管理外の絶対パスごとにsessionsか、前回複製のpreviousを伴う`再点検対象なし`を対応付ける。
`exec-review-evidence-check`が対象集合との対応と省略の成立を構造から確かめる。
委譲元はCLIの成功後に`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「完成条件証拠の受領確認」に従って本文・根拠を検収し、未解決0件を確かめて収束させる。
各組には返却された今回の`review_start`と、2回目以降の`previous_review_start`・`previous_review_head`も保持する。rebaseで今回の開始時点が変わっても前回値を置き換えず、両差分の同一性とファイル別申告を同じ受領確認へ渡す。

## ラウンド番号の保持先

各ラウンドの契約も保持し、再レビューの`previous_review_contract`を前ラウンドの契約と比べる。現在の契約へ更新した後も、前回の契約を前回の開始時点・HEADとともに維持する。

レビュー指摘管理表に同じ系統で記録された最大ラウンド番号を出所とする。初回は1、再レビューは最大値+1とし、同じラウンドは1つの表の1つの番号で表す。

## 応答済みの判定

レビューイーは`agent-toolkit:review-standards`の`references/reviewee.md`「実行レビュー反復での記録」に従って全指摘への応答を記録する。委譲元は`atk review-table show <表の絶対パス> --format=summary --round <対応したround>`の`unanswered_count`が0件の場合だけ再レビューへ進む。

## 反復原因の判定

再レビューの指摘を受領して修正指示を確定する前に、`atk review-table show <表の絶対パス> --track <対象track> --format=jsonl`で過去の指摘・応答と今回の指摘を読む。
現在roundのサマリーが示す過去roundと行は、その読取への到達情報として使う。
`agent-toolkit:review-standards`の`references/reviewee.md`「同じ指摘の反復」に従い、違反規定と発生条件の一致、修正不足、新しい要件、レビュー契約の不足を判定してから追送する。
完成条件の一致や件数だけから同じ指摘と判定しない。
同一性が成立した場合は局所修正の前に`agent-toolkit:bugfix`を起動し、`references/root-cause-analysis.md`「反復した指摘の原因判定」で原因を確定する。
設計の維持・置換を比べた根拠と採否を表の応答へ記録させる。
新しい要件または認可範囲の変更に当たる結果はエスカレーションへ送る。

## エスカレーションの事由別の扱い

残っている処理や追加対策についての判断要請を受けたら、完成条件の変更・除外を判定する前に、元の要求・公開契約・利用場面から処理が属する契約を確定する。追加機構の必要性には`agent-toolkit/rules/01-agent.md`「QCDと3段判定」と、`agent-toolkit:review-standards`の`references/reviewee.md`「レビュー指摘の位置付け」の裏付けを適用する。完成条件に障害の名前が無いことだけでは所属を決めない。
原契約がその処理を要求しないと一意に定まれば、適用範囲の解釈として根拠を同じ担当へ返す。障害時の保証が原契約に含まれる場合は、その保証を発生頻度だけで外さず、次の表の完成条件の変更・除外の認可で判定する。所属の解釈が調査後も確定しなければ、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」で残るユーザーだけが決められる事項を確認する。

| 事由 | 委譲元の処置 | 再開位置の記録 |
| --- | --- | --- |
| 計画の条項とWIの明示条件の衝突 | WIの明示条件から契約が一意に定まればWIを優先して確定し、同じレビューイーへ返す。人間由来の要求の解釈が分かれる場合はユーザー確認で確定してから返す | 不要 |
| 選定結果の`担当モデル`とAWI本文のモデル指定の不一致 | 保存済みの選定原本の当該項目の`担当モデル`だけを本文の担当とモデルの組へ直し、初回配分と既存の判断履歴は保つ。`atk run-script pick-wi-check -- <元の選定原本の絶対パス> --work-dir <対象リポジトリの絶対パス>`の終了コード0を1回で確かめる。生成側に未確定のモデルを推測させず、下位担当の追加起動で不一致を補わない。`実装担当`の値が変わった場合は引き継ぎ記録と専用資源を保持して返却した担当の終端を確かめ、同じレーンの回復として直した値を`model_type`へ渡して起動し直す。`実行レビュー担当`だけが変わった場合は同じthreadへ続行を返す | 不要 |
| 完成条件を外して残りをadoptする判断要請 | 後掲「技術判断で外せる完成条件」に当たれば、メインが技術判断で採否を確定し、判断と根拠を同じ担当へ返して記録させる（記録先は同節。実装着手後の形は`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`の完成条件を変更・除外する判断の通知の箇条）。当たらなければ、`agent-toolkit:review-standards`の`references/exec-review-recording.md`「統合時の完成条件判定」の失効の受理条件を確かめる。否定したユーザー判断（同書「失効の根拠」）があれば要求単位と不採用の根拠の対応を確かめて返す。メイン自身の会話に否定の発話があれば、対象の逐語内容と必要な時点・文脈を返す（出所の確定は後掲のとおり担当が行う）。どちらも無ければ`agent-toolkit:user-confirmation-and-report`「手段の選択」に従ってユーザー確認する | 必要 |
| ユーザーの選好、要件または認可範囲の変更 | ユーザー確認で回答を得て同じレビューイーへ返す | 不要 |
| 外部状態または権限の不足 | 回復手段を確かめ、ユーザー操作が必要ならユーザー確認する | 必要 |
| 同じ論点の2回目以降の差し替え | 指摘と現物を比べて意図と変更範囲を確かめ、なお確定できない場合だけユーザー確認してから修正する。回数だけを確認の条件にしない（`agent-toolkit:review-standards`の`references/reviewee.md`「同じ指摘の反復」） | 必要 |
| 技術的に解消できる実装欠陥 | レビューイーが同じ工程で修正する | 不要 |
| 同じthreadを継続できない | 後続担当へ同じ計画（`~/.claude/plans`内）、レビュー指摘管理表および確定済み判断を渡す | 必要 |

`再開位置の記録`が`必要`の行では計画ファイルを`~/.claude/plans`へ残し、`atk run-script plan-progress --`で`## 進捗ログ`へ完了工程、続行できない理由および再開位置を書く。キュー項目は現在の状態のまま保つ。

完成条件の判断をUWIで待つ場合は、`agent-toolkit:wi-standards`「状態と依存」に従い元AWI全体を保留する。UWIへ元項目のファイル名、対象の説明、必要な回答と回答で変わる結果を記録し、回答済みUWIの取り込みへ接続する。解除条件と再開工程は計画の進捗ログへ残し、計画が無ければ`atk wi edit <元AWIのファイル名> --append --body-file <絶対パス> --target-repo=<対象リポジトリ>`で元AWI本文へ追記する。回答前は失効を承認して統合へ渡さず、回答を待たずに進められる他項目を続ける。この保留は前段のキュー項目を変えない再開記録とも、全要求を不採用にするrejectとも区別する。

回答後は既存のレビュー指摘管理表とラウンド番号を継続する。
担当内で解消できる表登録・証拠記入・確認の失敗は同じ担当へ自己修正を求める（未作成の完成条件証拠は担当が生成する出力先であり、不足には当たらない）。エスカレーションとして扱うのは、担当では補えない判断・認可・入力・外部状態の不足だけとする。

## 技術判断で外せる完成条件

frontmatterに`source`を持つエージェント由来のWIの完成条件のうち、次の全てを満たすものはメインの技術判断で外せる。満たさない条件はユーザー判断の記録を探し、無ければユーザー確認する。

- 完成条件が対応する要求単位は、全て`agent-toolkit:wi-standards`「由来と承認」の明示由来（逐語引用、ユーザーコメント、UWI回答、出所付き対話回答）以外である
- 外すことが`agent-toolkit/rules/01-agent.md`「協調と自律」のユーザーが確定した判断の変更の対象外である
- `agent-toolkit:user-confirmation-and-report`「確認要否の判定」の3のユーザーだけが持つ値（選好、認可、外部の状態、手元に無い資料）を必要としない
- 重大度に応じた類似見直しと再発防止に必須の処置（`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」「類似見直しと再発防止の完了」）を判定する条件ではない。軽微な問題の探索終了は同書で判断し、ユーザーが明示した完成条件は保持する

判定は条件の意味の判断であり、判断要請を受けたメインと、統合時の完成条件判定で記録を読むメインが行う。外す理由には、エージェント由来であることに加えて上記の全条件の充足を要する。

記録先は判断時点で選ぶ。計画確定前は対象AWIの実施内容の根拠欄へ対象AWI・対象条件・`メインの技術判断`・外す根拠を記録し、初回レビューと統合はその記録をそのまま受理する。実装着手後は計画凍結を保ち、レビュー指摘管理表へ記録する。
会話中の判断は、対象発話の逐語内容と反復を区別する時点・文脈を、その証拠を使う担当へ渡す。`agents_server`の委譲先は`atk run-script session-review-evidence -- --delegation-record root --user-events`で最上位の会話を解決するため、メイン自身の識別子とtranscriptの絶対パスの手渡しを省く。メインは出所の抽出を待たずに回答する。取得済みの出所ファイルと`record`・`line`、明示された別会話の識別子か記録パスがあれば、担当はそれらを優先する。
解決が終了コード2で失敗した場合は、担当が対象会話の識別子か記録パスをメインから受け取り、既存の`--claude-session-id`・`--codex-thread-id`・`--transcript`のいずれかで再実行する。`Agent`ツールなど解決を確認していない起動方法でも明示値を渡す。
保存済みの指定組は`atk run-script session-review-evidence -- --user-events-file <元の出力の絶対パス> --user-event-at <record>:<line>`で読む。根拠には元ファイルと元のrecord・lineを使う。失効の根拠の書式とユーザー由来の受理条件は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「失効の根拠」に従う。
