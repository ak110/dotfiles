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

process-wiでは、メインが候補本文から分類と配分を判断し、取得済み候補と意味判断を`pick-wi-check`へ渡して定型欄の生成・確認・保存を1回で行う。保存済み選定YAMLから`atk lane create`へレーンを渡し、準備後に同じ担当threadが計画から統合までを担う。意味判断、レーン数上限、単一writer、独立した実行レビュー、直列統合、計画保存とWI終端は各工程に残す。
専用worktreeとbranchは統合後も追加に備えて保持し、通常終了の`atk agents-exit-session`またはprocess-loopの子終了後に`atk lane delete`と同じ安全条件で回収する。次回の開始時は`atk lane delete --list`から残存登録と前回結果を読み、未統合・dirty・稼働中・不明の成果を保って再判定する。セッションのmanaged-tempと証拠は専用資源の回収対象から外す。

## 登録、回答、振り返り

要求はユーザーが`atk wi add`、`agent-toolkit:add-awi-by-user`、`atk serve`のWI画面から登録し、処理中のエージェントと`agent-toolkit:session-review`も投入する。本文、由来、状態と依存は`agent-toolkit:wi-standards`が定める。

自律モードの確認手段とUWIへの切替は`agent-toolkit:user-confirmation-and-report`「手段の選択」に従う。ユーザーは`atk wi answer`か`atk serve`で回答する。回答済みUWIは、`source`が`run-skill`なら同じ対象リポジトリと同じスキルの次回の`atk run-skill`が、それ以外はprocess-wiの次の実行のメインが元項目とともに取り込む。処理中のprocess-wiのセッションへユーザーが回答を告げた場合は同じ実行の処理対象へ加える（`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象WIの追加」）。

作業完了後は`agent-toolkit:completion-report`から`agent-toolkit:session-review`が起動し、振り返りが投入したAWIは次のprocess-loopセッションで処理される。

## 運用変更を検討する観点

別セッションのprocess-loopが並行して稼働している前提で対象と状態遷移を調べる。対策がprocess-loop、子セッションのメイン、レーン担当、まとめ処理型のセッションのどれに作用するか、各利用形態から到達できるか、ユーザーの操作が増えるかを比べる。
