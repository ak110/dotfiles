---
name: workflow-overview
description: >
  WI処理の工程や運用に関わる問題の原因分析と対策、運用工程や自動化の変更計画、
  ユーザーへの運用説明で起動する。キュー、`atk wi process-loop`、process-wiの1回の実行、確認と回答、振り返りで得た要求を投入する方法を扱う。
user-invocable: false
---

# WI処理の運用概要

複数のセッションとユーザーの操作にまたがるWI処理の全体像を示す。
個々の工程の実行契約は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`と各工程のスキル（`agent-toolkit:plan-mode`、`agent-toolkit:process-wi`など）が定める。

運用の説明は提示された資料の範囲でも行える。キュー・process-loop・レーンの現物の照会と操作には、参照資料、対象へのアクセスとCLIが必要であり、機能が無い環境では説明と実際の設定・状態変更を区別する。運用操作はそれらの機能を使えるClaude CodeまたはCodexで行い、説明の文には[文章の品質基準](references/quality-standards.md)を使う。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 工程の責務、受渡しまたは出口を対策や説明の根拠にする前 | `${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md` |

## 利用形態と起動主体

- 対話型: ユーザーがエージェントへ直接依頼する。計画の作成要否は`agent-toolkit:plan-mode`「計画ファイルの作成要否」、ユーザー確認の要否は`agent-toolkit:user-confirmation-and-report`「確認要否の判定」で決める。
- 自律型: ユーザーが`atk wi process-loop`を起動する。process-loopが反復ごとに開始前更新と専用worktreeを準備し、子セッションで`agent-toolkit:process-wi`を起動する。子セッションへ渡すプロンプトはユーザーの発話ではなく、子セッションは`AGENT_TOOLKIT_PROCESS_LOOP_SESSION`で起動元を判別する。停止は`atk wi process-loop abort`、次の1セッションだけへの指示は`instruct`、状態は`status`で行う。
- まとめ処理型: ユーザーが`agent-toolkit:single-lane-process`を手動起動し、たまったWIを1回の実行で扱う。
- 定期実行型: ユーザーがcronなどへ`atk run-skill`を登録し、WIキューを経由せずにスキル1件を自律モードで1回ずつ実行する。結果は報告用UWI、判断が要る対応は事前承認型UWIで届く。これらのUWIは`source`が`run-skill`、`scope`がスキル名であり、process-wiの選定とprocess-loopの起動件数から除く。

登録した複数リポジトリのprocess-loopは、ホームから`atk wi process-loop all-repos`で起動する。
登録は`process_loop_repositories`のJSON配列、登録集合の一覧は`atk wi list --configured-repos`で扱う。
登録が空の場合は待機し、登録外のリポジトリへのWI投入は処理対象の登録と独立して使える。
取得済み候補は対象リポジトリをcwdとする通常のdelegateへ渡し、オーケストレーターは担当とユーザーの中継だけを担う。
選定・規範・prepare・監査・実装・Git・公開の判断は対象担当が持つ。
レーン上限は対象リポジトリ別の候補数から求め、処理の所有は同じoriginを対象とする通常起動と共有する。
詳細は`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/all-repos-orchestration.md`が定める。

process-loopは子セッションの開始前に、別セッションの活動による猶予と未回答UWIによる抑止を同時に評価する。
活動猶予は処理可能WIの検出から10分で、共有private-notesへの新規WI到着の検出ごとに10分へ延長する。
当該ループ自身とその子孫、終了済みのセッション、稼働中の子エージェントを持たずにユーザーの応答を待つセッションは、活動の理由から除く。
活動猶予は活動がなくなった時点か期限の到来で解除し、既存WIの編集・状態移動は延長に数えない。
対象集合に未回答UWIがあれば、保存済みUWIの最終更新から600秒が経過するか全件が回答されるまで開始を抑止する。
`hold`のUWIと`run-skill`由来のUWIも抑止の判定に含め、回答後に`adopted`へ移ったUWIの更新も最終更新へ反映する。
両条件が解除された後に候補を再評価し、待機理由・期限・延長・開始は端末とログで確認できる。

process-wiでは、対象のメインが候補本文から分類と配分を判断し、取得済み候補と意味判断を`pick-wi-check`へ渡して定型欄の生成・確認・保存を1回で行う。
保存済み選定YAMLから`atk lane create`へレーンを渡し、準備後に同じ担当threadが計画から統合までを担う。
1レーンでも`atk lane integrate`を使い、統合と計画保存・WI終端・統合済み記録を同じ操作で完了する。
操作の入力と失敗後の再開は`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/run-lanes.md`が定める。
意味判断、レーン数上限、単一writer、独立した実行レビュー、直列統合は各工程に残す。
レーンの専用worktreeは対象リポジトリの主作業ツリーの`.claude/worktrees/process-wi-<レーン識別子>`に固定され、対象リポジトリごとに1つだけ起動するprocess-wiの反復でもパスが増えない。レーンのworktreeとbranchは統合後も追加に備えて保持し、通常終了では`atk agents-exit-session`が、process-loopでは子セッションの終了後の処理が、`atk lane delete`と同じ安全条件で回収する。次回の開始時は`atk lane delete --list`から残存登録と前回結果を読み、未統合・dirty・稼働中・不明の成果を保って再判定する。セッションのmanaged-tempと証拠は専用資源の回収対象から外す。

## 登録、回答、振り返り

要求はユーザーが`atk wi add`、`agent-toolkit:add-awi-by-user`、`atk serve`のWI画面から登録し、処理中のエージェントと`agent-toolkit:session-review`も投入する。本文、由来、状態と依存は`agent-toolkit:wi-standards`が定める。

ターンやセッションを終えた後もユーザーに残る判断と行動は、モードによらず個別UWIとして保存し、報告はその所在を示す。会話中の確認で回答を得た事項はUWIに重ねず、回答を得ないまま終わった確認はUWIへ移す。保存の条件、自律モードの確認手段とUWIへの切替は`agent-toolkit:user-confirmation-and-report`「手段の選択」に従う。ユーザーは`atk wi answer`か`atk serve`で回答する。回答済みUWIは、`source`が`run-skill`なら同じ対象リポジトリと同じスキルの次回の`atk run-skill`が、それ以外はprocess-wiの次の実行のメインが元項目とともに取り込む。処理中のprocess-wiのセッションへユーザーが回答を告げた場合は同じ実行の処理対象へ加える（`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象WIの追加」）。

作業完了後は`agent-toolkit:completion-report`から`agent-toolkit:session-review`が起動し、振り返りが投入したAWIは次のprocess-loopセッションで処理される。

## 運用変更を検討する観点

別セッションのprocess-loopが並行して稼働している前提で対象と状態遷移を調べる。対策がprocess-loop、子セッションのメイン、レーン担当、まとめ処理型のセッションのどれに作用するか、各利用形態から到達できるか、ユーザーの操作が増えるかを比べる。
