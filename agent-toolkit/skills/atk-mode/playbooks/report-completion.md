# 作業完了報告とセッションの完了処理

成果物を確定した作業を終える時に、反映、回収、判断の保存、公開、報告の確定、報告用UWI、送達と終了を一続きに行う手順書である。
この手順書を全文読むことが終了工程の開始であり、Stop hookはその読取を完了報告の起動証拠として扱う（`agent-toolkit:atk-mode/references/termination-evidence.md`「受理する証拠」）。
結果は不都合なものも含めて報告し、観測した事実、推論、未確認を分ける（`agent-basics:writing-standards/references/quality-principles-facts.md`）。

## 適用の判定

- 適用する: 実装、計画、WIの投入などの成果物を確定した作業を、メインが完了した時。
  中断、担当の交代、後続担当の新しい成果物の有無によらず、元の作業が適用対象なら未完了の完了処理を引き継ぐ。
- 適用しない: 成果物の確定を伴わない単発の調査回答、単純な事実回答、確認質問、途中報告、待機と失敗の報告、委譲先が委譲元へ返す報告。
- 常駐が起動した統括は、その実行の報告を所有する。
  統括から明示的に委譲されたリポジトリ担当は、成果と残工程を統括へ返す。
  この担当除外は役割文書で判断し、`omit-completion`の記録の対象外とする。
- プロジェクト規範が起動を省くと定める場合は、本書を読まずに`agent-toolkit:atk-mode/references/termination-evidence.md`を読み、省略を記録して通常の報告をする。
- 反映、公開、UWIの保存、セッションの終了を操作できない環境では、得た成果と確認した証拠で結果を説明し、未実施の反映・公開・保存を区別して書く。
  残る操作は、それを実行できるClaude CodeかCodexへ引き継ぐ。

最後の会話圧縮の後に本書を読んでいれば、次の作業でも読み直さずに必要な工程を行う。
圧縮の後に残る工程で本書が必要なら、本書の全文を読み直す。

## 条件と行動

| 条件 | 行動 |
| --- | --- |
| 振り返り結果報告、AWI投入結果報告を書く | `agent-toolkit:atk-mode/references/report-formats-retrospective.md` |
| 自律モードで報告用UWIを書く | `agent-toolkit:atk-mode/references/report-formats.md`「報告用UWIのフォーマット」と`agent-toolkit:atk-mode/references/uwi-format.md` |
| 独立したWI投入を受け付けて投入元の終了を判定する | `agent-toolkit:atk-mode/playbooks/wi-submission.md` |
| Stop hookが起動不足を示した、中止・置換・待機・技術的不成立を記録する | `agent-toolkit:atk-mode/references/termination-evidence.md` |

## 完了の到達水準

「完了」は、対象全体と既知の問題への対応が済み、ユーザーの操作が要る事項だけが残る状態を指す。
代表箇所だけの変更は途中経過として報告する。
既知の不具合と未達の明示要件は、未達事項として区別して書く。
環境の制約で確かめられない範囲は、実施できる検証を終えたうえで、未確認の範囲と理由を報告する。
個別のUWIとユーザーへの操作依頼は、未確認の範囲とは別の理由（ユーザーにしか決められない判断、ユーザーにしかできない操作）がある時に使う。

現行の作業契約（`agent-toolkit:atk-mode/references/work-contract.md`）の各要求と残る完成条件を、「証拠」「明示的な取り下げや置換」「ユーザーの対応が要る未完了事項」のいずれかへ対応付ける。
未処理の追加入力があれば統合し、残る元の工程を終えてから全体を判定する。
回収の要求では、`agent-toolkit:atk-mode/playbooks/managed-temp.md`「作業用ディレクトリ」の残存一覧と保持の根拠を使い、途中で選んだ保護の条件も元の要求と現物で評価し直す。

run-skillのスキル名、モード、自由記述、scope、同一実行識別子、報告先は、SessionStartが渡す起動入力から取得する。
短い目的文の主語は子セッション自身で、子が担当する工程（分析、保存、finishなど）を終えた時点で目的を達成する。
run-skill起動元による報告の同期検収は、子セッションの終了後に行われるため、子自身の完了条件へ含めない。
子の未完了の工程は未完了として報告し、起動元の検収は起動元に任せる。
回復できる残工程は続け、外部の前提の不足で終える場合は、未達、理由、再開の条件を同じ実行の報告へ保存する。

完了済みの作業を再開する根拠は、新しい要求、結果に影響する変更、新しい失敗、未検証の完成条件に限る。
報告の不足は保存済みの証拠で補い、検証済みの作業はその証拠を再利用する。

独立したWI投入は、`agent-toolkit:atk-mode/playbooks/wi-submission.md`の受付票で、所有を移した要求と移していない要求を区別する。
投入元は製品のwriter、公開とCIの検収、本回の報告と結果の保存を終えたうえで、所有を移した投入を待機の対象から外して終了できる。
報告には受付済みで投入結果待ちであることと、要求ごとの照会先を残す。

## 工程

入力が揃った手順から進め、前の出力に依存しない手順は並行してよい。
中断から再開する時は、要求ごとに実際の成果、送達の結果、未完了の工程を確かめ、最初の未完了の工程から続ける。
進捗、判断、報告の原本は1つの作業記録とする（計画の`## 進捗ログ`、計画が無ければ引き継ぎ記録）。
作業完了報告はこの原本から作り、報告用UWIは作業完了報告から`agent-toolkit:atk-mode/references/report-formats.md`「報告用UWIのフォーマット」の対応表で写す。
送達の前に、主張と根拠、確認した版、未確認の範囲、判断の時点を原本へ残す（`agent-basics:writing-standards/references/quality-principles-facts.md`「報告の選別と判断記録」）。

1. 常時稼働サーバーを持つプロジェクトでは、本書「常時稼働サーバーへの反映」を行う。
2. 自分が作った作業用領域を、`agent-toolkit:atk-mode/playbooks/managed-temp.md`の寿命の区分に従って回収する。
   過去のエージェントが残した資源と誤変更は、`agent-toolkit/skills/atk-mode/references/operations.md`「プロセス終了の安全規定」で調べ、不要な範囲を回収・是正する。
   process-wiの登録済みレーンの資源は、追加作業や公開後の工程に備えてこの時点では保持する。
   全工程と外部プロセスの終了後に、通常終了では`atk agents-exit-session`が、process-loopでは子プロセスの終了後処理が、`atk lane delete`と同じ条件で回収する。
   未回収の対象は保持の理由を調べ、認可の範囲で解消できるものは同じ回収操作で再判定する。
   別セッションが現に使う資源は保護し、使用主体、保持理由、次回の再判定を報告する。
   それ以外で本人の判断・認可・操作が残る資源は、対象・保持理由・必要な判断か操作・再開条件を個別のUWIにする。
   報告用UWIへの記載だけで終えない。
3. このセッションでユーザーが確定した判断のうち未保存のもの（`agent-toolkit/skills/atk-mode/SKILL.md`「硬い制約」の保存対象）を保存するか、AWIを投入する。
   保存では`agent-toolkit:atk-mode/playbooks/user-utterance.md`「原文の保持と取り込み」の、程度の限定と確度を保つ規定を適用する。
4. 協調モードでcommitを作り、確認済みの公開範囲がcommitより先へ及ぶ場合は、`agent-toolkit:atk-mode/playbooks/publish.md`でその範囲の公開を終える。
   `agent-toolkit:atk-mode`の作業`process-wi`と`agent-toolkit:atk-mode`の作業`process-wi single-lane`は、それぞれの工程の中で公開する。
5. 元の要求が求めた結果を各工程の成果から確かめ、`agent-toolkit:atk-mode/references/report-formats.md`「作業完了報告のフォーマット」の本文を確定する。
   公開・反映・回収を実施した場合は、その到達点も報告する。
   確定の前に、作業記録に残る、途中報告でAWI化ステータスを`予定`とした改善点を回収する。
   今回のAWI投入、再利用した既存項目、不要の判断と比べて、各対象を`済`とWIファイル名、または`不要`と理由へ確定し、同じ作業記録へ追記する。
   確定できない対象は、不足する判断・認可・入力か具体的な競合と、再開の条件を作業記録に残し、報告の未達へ書く。
   会話圧縮や担当交代の後も同じ作業記録から復元し、`済`の対象へAWIを重ねて投入しない。
   改善点の調査と是正は`agent-toolkit:atk-mode/playbooks/main-reporting.md`「作業中の改善点の報告」が定める。
6. 自律モードでは、ユーザーが結果を確かめて是正を指示できるよう、事後承認型の報告用UWIをセッションにつき1件保存する。
   本文と保存は`agent-toolkit:atk-mode/references/report-formats.md`「報告用UWIのフォーマット」「報告用UWIの確認と保存」に従う。
   `atk run-skill`による実行では、`--source run-skill --scope "<スキル名とモードの組>"`とfrontmatterの`run_skill_report: true`を保つ。
   scopeは、通常の起動ではスキル名だけ（自由記述は含めない）、batchでは`agent-toolkit:review-session batch`とする。
   保存CLIが付ける`run_skill_run_id`は、再開と更新でも保つ。
   報告先は起動元が渡したリポジトリとし、実行のcwdと区別する。
   起動元は本文、commit、upstreamがある場合のpushを検収する。
   batchの振り返り結果は、その実行の1件の報告へ集約し、対象セッションの作業完了報告とは別に保存する。
   保存に失敗した時は、同じUWIの保存を回復する。
7. 確定した本文を、`agent-toolkit:atk-mode/references/delivery.md`「手段の選択」の手段で1回送達して終了する。
   - Claude Codeで`send_to_user`を使う時は表示の成功を確かめ、起動元が求める報告後の終了操作を同じ応答で行う。
     送達後のターンの終え方は`agent-toolkit/skills/atk-mode/playbooks/host-claude-code.md`「ターンの終え方」に従う。
   - Codexの送達順は`agent-toolkit:atk-mode/playbooks/host-codex.md`「ユーザー確認と終了」に従う。
   - `atk agents-exit-session`などでホスト本体を停止する場合は、報告用UWIの保存の検収と必要な可視の報告（Codexでは`commentary`）を先に終え、停止操作を最後のツール呼び出しにする。

   最終報告の末尾にだけ、次の固定文を置く。

   ```text
   以上で、このセッションの作業は全て終わった。
   ```

   回答や委譲結果の待機、中止・置換、技術的不成立は通常の完了と分け、残工程と再開の条件を報告し、`agent-toolkit:atk-mode/references/termination-evidence.md`に従って記録する。
   送達の失敗、実害を防ぐStop hookの遮断、新しいユーザーの指示は、それぞれの回復・続行の手順へ進む。
   確認の記録は`docs/development/audit-records-completion.md`の「agent-toolkit/skills/atk-mode/playbooks/report-completion.md：最終報告送達後のターン終了：2026年10月9日」にある。
   送達後に実際の新しい問題が分かった場合は、その問題を調べて処置し、既存の報告用UWIと報告を更新する（再検証はその問題に要る範囲に限る）。
   完了の工程の後に初めて分かった独立した問題は、`agent-toolkit:atk-mode/playbooks/wi-drafting.md`「起票目的別の調査出口」の軽い引き継ぎで登録できる。

区切りやセッション終了時の単発の振り返りは、`agent-toolkit:atk-mode/playbooks/review-session.md`に従い、本書の工程と送達の条件とは分ける。
batchによる未処理セッションの回収は別に行う。

## 常時稼働サーバーへの反映

対象プロジェクトが開発機で常時稼働するサーバーを持ち、プロジェクト規範かタスクランナーの定義が反映の手順と稼働確認の手順の両方を定める場合に行う。

1. 起動元の工程が先に反映した結果（反映したHEADの一意な短縮OID、反映コマンドの終了ステータス、稼働確認の結果）があり、報告時点の主作業ツリーでその短縮OIDがHEADへ解決し、両方の処理が成功していれば、再反映を省く。
2. それ以外は現在のHEADを反映し、稼働を確かめる。
3. 報告には再反映の有無と稼働確認の結果を書く。
   反映しない場合は理由と未反映の状態を書き、ユーザーの対応が要る残作業は`agent-toolkit:atk-mode/references/delivery.md`「手段の選択」で個別UWIの要否を判定する。

起動元の工程が反映を先に始めてよいのは、反映が対象リポジトリへ書き込まず、公開向けの入力を作らず、公開操作・成果物・排他資源と競合しない場合に限る。
委譲先が反映した場合は、上の結果を委譲元へ返す。
本節を読むことは、報告や終了をその担当へ加える認可にはならない。

返す報告に含める項目: 作業完了報告の本文（`agent-toolkit:atk-mode/references/report-formats.md`の書式）と、送達・保存・終了操作の結果。
