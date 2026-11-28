# 作業完了報告とセッションの完了処理

目的: メインが成果の反映・回収・公開を終えてから事実どおりに報告し、ユーザーが報告だけで結果と残る判断を把握できるようにする。
成果物の検収を終えたら、最終報告を書く前に本書を読み、反映、回収、判断の保存、公開、報告の確定、報告用UWI、送達と終了を一続きに行う手順書である。
最後に元要求・成果・未回収対象・本人へ届ける結果を照合し、完了の判定を全文取得の記録で代用しない。
結果は不都合なものも含めて報告し、`agent-basics:writing-standards/references/quality-principles-facts.md`「結果の返却」に従う。

## 適用の判定

- 適用する: 実装、計画、WIの投入などの成果物を確定した作業を、メインが完了した時。
  中断、担当の交代、後続担当の新しい成果物の有無によらず、元の作業が適用対象なら未完了の完了処理を引き継ぐ。
- 適用しない: 成果物の確定を伴わない単発の調査回答、単純な事実回答、確認質問、途中報告、待機と失敗の報告、委譲先が委譲元へ返す報告。
- 常駐が起動した統括は、その実行の報告を所有する。
  統括から明示的に委譲されたリポジトリ担当は、成果と残工程を統括へ返す。
  この担当除外は役割文書で判断する。
- プロジェクト規範が本手順を省くと定める場合は、その規範に従って結果を報告する。
- 反映、公開、UWIの保存、セッションの終了を操作できない環境では、得た成果と確認した証拠で結果を説明し、未実施の反映・公開・保存を区別して書く。
  残る操作は、それを実行できるClaude CodeかCodexへ引き継ぐ。

本文の取得と会話圧縮後の再取得は`agent-basics:writing-standards/references/read-method.md`に従う。

## 条件と行動

| 条件 | 行動 |
| --- | --- |
| 振り返り結果報告、AWI投入結果報告を書く | `agent-toolkit:atk-mode/references/report-formats-retrospective.md` |
| 自律モードで報告用UWIを書く | `agent-toolkit:atk-mode/references/report-formats.md`「報告用UWIのフォーマット」と`agent-toolkit:atk-mode/references/uwi-format.md` |
| 独立したWI投入を受け付けて投入元の終了を判定する | `agent-toolkit:atk-mode/playbooks/wi-submission.md` |
| 中止・置換・待機・技術的不成立を記録する | `agent-toolkit:atk-mode/references/termination-evidence.md` |

## 完了の到達水準

「完了」は対象全体と既知の問題への対応が済み、ユーザーの操作が必要な事項だけが残る状態を指す。
代表箇所だけの変更は途中経過として報告する。
既知の不具合と未達の明示要件は、未達事項として区別して書く。
環境の制約で確かめられない範囲は、実施できる検証を終えたうえで、未確認の範囲と理由を報告する。
個別のUWIとユーザーへの操作依頼は、`agent-toolkit:atk-mode/references/delivery.md`「個別UWI」に従う。

現行の作業契約（`agent-toolkit:atk-mode/references/work-contract.md`）の各要求と残る完成条件を、「証拠」「明示的な取り下げや置換」「ユーザーの対応が必要な未完了事項」のいずれかへ対応付ける。
未処理の追加入力があれば統合し、残る元の工程を終えてから全体を判定する。
回収の要求では、`agent-toolkit:atk-mode/playbooks/managed-temp.md`「作業用ディレクトリ」の残存一覧と保持の根拠を使い、途中で選んだ保護の条件も元の要求と現物で評価し直す。

run-skillのスキル名、モード、自由記述、`accepted_scopes`、同一実行識別子、報告先は、SessionStartが渡す起動入力から取得する。
短い目的文の主語は子セッション自身で、子が担当する工程（分析、保存、finishなど）を終えた時点で目的を達成する。
run-skill起動元による報告の同期検収は、子セッションの終了後に行われるため、子自身の完了条件へ含めない。
子の未完了の工程は未完了として報告し、起動元の検収は起動元に任せる。
回復できる残工程は続け、外部の前提の不足で終える場合は、未達、理由、再開の条件を同じ実行の報告へ保存する。

完了済みの作業を再開する根拠は、新しい要求、結果に影響する変更、新しい失敗、未検証の完成条件に限る。
報告の不足を補う検証は、`agent-basics:check-execution/references/verification-scope.md`「実行条件と再実行」に従う。

独立したWI投入は、`agent-toolkit:atk-mode/playbooks/wi-submission.md`の受付票で、所有を移した要求と移していない要求を区別する。
投入元は製品のwriter、公開とCIの検収、本回の報告と結果の保存を終えたうえで、所有を移した投入を待機の対象から外して終了できる。
報告には受付済みで投入結果待ちであることと、要求ごとの照会先を残す。

## 工程

入力が揃った手順から進め、前の出力に依存しない手順は並行してよい。
中断から再開する時は、要求ごとに実際の成果、送達の結果、未完了の工程を確かめ、最初の未完了の工程から続ける。
進捗、判断、報告の原本は引き継ぎ記録とする。
この原本を使い、協調モードでは作業完了報告、自律モードでは報告用UWIにだけ報告本文を書く。
送達の前に、主張と根拠、確認した版、未確認の範囲、判断の時点を原本へ残す（`agent-basics:writing-standards/references/quality-principles-facts.md`「報告の選別と判断記録」）。

1. 常時稼働サーバーを持つプロジェクトでは、本書「常時稼働サーバーへの反映」を行う。
2. 自分が作った作業用領域を、`agent-toolkit:atk-mode/playbooks/managed-temp.md`の寿命の区分に従って回収する。
   `agent-toolkit:atk-mode/references/operations.md`「プロセス終了の安全規定」に従う。
   process-wiの登録済みレーンの資源は、追加作業や公開後の工程に備えてこの時点では保持する。
   通常終了では`atk agents-exit-session`が回収し、統括は登録外資源の統合先worktreeが1つに確定していれば`--repo <統合先worktree>`を渡す。
   巡回の回収は`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`「レーンworktreeの扱い」に従う。
   未回収の対象は保持の理由を調べ、認可の範囲で解消できるものは同じ回収操作で再判定する。
   別セッションが現に使う資源は保護し、使用主体、保持理由、次回の再判定を報告する。
   個別UWIが必要な資源には、対象・保持理由・必要な判断か操作・再開条件を書く。
3. このセッションでユーザーが確定した判断のうち未保存のもの（`agent-toolkit/skills/atk-mode/SKILL.md`「硬い制約」の保存対象）を保存するか、AWIを投入する。
   保存は`agent-toolkit:atk-mode/playbooks/user-utterance.md`「原文の保持と取り込み」に従う。
4. 協調モードでcommitを作り、確認済みの公開範囲がcommitより先へ及ぶ場合は、`agent-toolkit:atk-mode/playbooks/publish.md`でその範囲の公開を終える。
   `agent-toolkit:atk-mode`の作業`process-wi`と`agent-toolkit:atk-mode`の作業`process-wi single-lane`はそれぞれの工程の中で公開する。
5. 元の要求が求めた結果を各工程の成果から確かめる。
   是正を受けた場合は、作業契約の完遂事項と既存の変更・検証記録を見て、原因・対策・確認の根拠がそろっているかを確かめる。
   `agent-basics:bugfix/references/prevention.md`「案を比べる」に従う。
   確認できない分は未達として、何を確かめれば再開できるかを示す。
   協調モードの本文は `agent-toolkit:atk-mode/references/report-formats.md`「作業完了報告のフォーマット」で確定する。
   公開・反映・回収は、依頼の結果や次の行動に関わる到達点と、未実施なら必要だった操作・理由を報告する。
   依頼外の非実施や予定どおり自動回収を待つだけの一時領域など、判断を変えない説明は省く。
   引き継ぎや手作業が残る資源は、所在と残る操作を示す。
   本人の判断・認可・入力が必要な事項は確認の手順へ分け、報告には結果と判断に使った根拠を残す。
   確定の前に、作業記録に残る、途中報告でAWI化ステータスを`予定`とした改善点を回収する。
   今回のAWI投入、再利用した既存項目、不要の判断と比べて、各対象について`済`とWIファイル名か、`不要`と理由を同じ作業記録へ追記する。
   確定できない対象は、不足する判断・認可・入力か具体的な競合と、再開の条件を作業記録に残し、報告の未達へ書く。
   会話圧縮や担当交代の後も同じ作業記録から復元し、`済`の対象へAWIを重ねて投入しない。
   改善点の調査と是正は`agent-toolkit:atk-mode/playbooks/main-reporting.md`「作業中の改善点の報告」が定める。
6. 自律モードでは、作業結果を`agent-toolkit:atk-mode/references/report-formats.md`「報告用UWIのフォーマット」「報告用UWIの確認と保存」に従って保存する。
   常駐のprocess-loopでは前回報告以降に公開・反映・必要なリリースまで届いた成果を、dotfilesはリリースごと、リリース周期の無いリポジトリは公開ごとに1件へまとめる。
   判断が必要な事項と未達は随時UWIで届ける。
   それ以外はセッションにつき1件とする。
   `completion_report: true`は、process-loopの`--once`、`abort`、ユーザーの中断、会話を替える交代で終了する完了報告にだけ置く。
   継続中の成果の報告や公開後の案内には付けず、報告の保存・送達後は[巡回する](process-loop.md#巡回する)の手順2へ戻る。
   更新のための本人再開では完了報告を作らず、予約後に終了する。
   個別UWIのファイル名を報告の未達・持ち越しに書く。
   `atk run-skill`による実行では、`--source run-skill --scope "<スキル名とモードの組>"`とfrontmatterの`run_skill_report: true`を保つ。
   scopeは自由記述を含まないスキル名とモードの組とし、起動入力の`accepted_scopes`の先頭を使う。
   例えばbatchのscopeは`agent-toolkit:review-session batch`である。
   保存CLIが付ける`run_skill_run_id`は再開と更新でも保つ。
   報告先は起動元が渡したリポジトリとし、実行のcwdと区別する。
   起動元は本文、commit、upstreamがある場合のpushを検収する。
   batchの振り返り結果は、その実行の1件の報告へ集約し、対象セッションの作業完了報告とは別に保存する。
   保存に失敗した時は、同じUWIの保存を回復する。
7. 協調モードは確定した本文を、自律モードは保存を検収した報告用UWIのファイル名を、`agent-toolkit:atk-mode/references/delivery.md`「手段の選択」に従って1回送達する。
   - Claude Codeで最終報告を`send_to_user`で送る時は`kind: "completion"`を指定し、表示の成功を確かめる。
     送信側が末尾をそろえるため、本文に固定文は書かなくてよい。
   - 協調モードの `add-awi-by-user`、`implement`、`process-user-comments` では、送達成功を確かめてから最後のツール呼び出しとして `atk agents-exit-session --after-completion` を実行する。
     REPL内のツール結果はスクリプトが終わってからセッション記録に書かれるため、送信結果の`ok: true`を確認し、別のツール呼び出しで実行する。
     ユーザーの回答・承認・本人作業を待つ場合は呼ばない。
     環境による対象判定はコマンドに任せ、自動終了しない理由が返った場合も通常どおりターンを終える。
     Claude Codeの終え方は`agent-toolkit:atk-mode/playbooks/host-claude-code.md`「ターンの終え方」に従う。
   - Codexの送達順は`agent-toolkit:atk-mode/playbooks/host-codex.md`「ユーザー確認と終了」に従う。
   - ホスト本体を停止する場合は、報告の保存検収と送達を終え、停止操作を最後のツール呼び出しにする。
   - process-loopの`atk agents-exit-session`による終了は、[中断と再開](process-loop.md#中断と再開)の条件に限る。

   Codexなどで最終報告を通常の応答本文で届ける時は、その末尾にだけ次の固定文を置く。

   ```text
   以上で、このセッションの作業は全て終わった。
   ```

   回答や委譲結果の待機、中止・置換、技術的不成立は通常の完了と分け、残工程と再開の条件を報告し、`agent-toolkit:atk-mode/references/termination-evidence.md`に従って記録する。
   送達の失敗、実害を防ぐStop hookの遮断、新しいユーザーの指示は、それぞれの回復・続行の手順へ進む。
   送達後に実際の新しい問題が分かった場合は、その問題を調べて処置し、再検証は`agent-basics:check-execution/references/verification-scope.md`「実行条件と再実行」に従う。
   自律モードでは報告用UWIを更新し、協調モードではユーザーの判断や次の操作を変える訂正だけを届ける。
   完了の工程の後に初めて分かった独立した問題は、`agent-toolkit:atk-mode/playbooks/wi-drafting.md`「起票目的別の調査出口」の軽い引き継ぎで登録できる。

`agent-toolkit:atk-mode/playbooks/review-session.md`の単発の振り返りは必要時に行い、process-loopでは[前区間の振り返り](process-loop.md#前区間の振り返り)に従う。
振り返りは本書の工程と送達の条件とは分ける。
非同期の`review-session batch`はその場の`bugfix`と単発の振り返りで未解決の問題を後から扱う。

## 常時稼働サーバーへの反映

対象プロジェクトが開発機で常時稼働するサーバーを持ち、プロジェクト規範かタスクランナーの定義が反映の手順と稼働確認の手順の両方を定める場合に行う。

1. 起動元の工程が先に反映した結果（反映したHEADの一意な短縮OID、反映コマンドの終了ステータス、稼働確認の結果）があり、報告時点の主作業ツリーでその短縮OIDがHEADへ解決し、両方の処理が成功していれば、再反映を省く。
2. それ以外は現在のHEADを反映し、稼働を確かめる。
3. 報告には再反映の有無と稼働確認の結果を書く。
   反映しない場合は理由と未反映の状態を書き、ユーザーの対応が必要な残作業は`agent-toolkit:atk-mode/references/delivery.md`「手段の選択」で個別UWIの要否を判定する。

起動元の工程が反映を先に始めてよいのは、反映が対象リポジトリへ書き込まず、公開向けの入力を作らず、公開操作・成果物・排他資源と競合しない場合に限る。
委譲先が反映した場合は、上の結果を委譲元へ返す。
本節を読むことは、報告や終了をその担当へ加える認可にはならない。

返す報告: 協調モードでは作業完了報告（`agent-toolkit:atk-mode/references/report-formats.md`の書式）、自律モードでは保存した報告用UWIのファイル名。
各モードの送達と終了の順は本書「工程」に従う。
