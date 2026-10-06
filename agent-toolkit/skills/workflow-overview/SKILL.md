---
name: workflow-overview
description: >
  WI処理の工程や運用に関わる問題の原因分析と対策、運用工程や自動化の変更計画、
  ユーザーへの運用説明で起動する。キュー、`atk wi process-loop`、process-wiの1回の実行、確認と回答、振り返りで得た要求を投入する方法を扱う。
user-invocable: false
---

# WI処理の運用概要

本スキルは複数のセッションとユーザーの操作にまたがるWI処理の全体像を提供する知識スキルである。
個々の工程の実行契約は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`と各工程のスキルが定める。スキルには`agent-toolkit:plan-mode`、`agent-toolkit:process-wi`、`agent-toolkit:single-lane-process`などがある。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 工程の責務、受渡しまたは出口を対策や説明の根拠にする前 | `${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md` |

## 利用形態と起動主体

- 対話型: ユーザーがエージェントへ直接依頼する。メインは`agent-toolkit:plan-mode`「計画ファイルの作成要否」で作成を判定し、作成する場合だけ同スキルを起動する。作成を省く変更も協調モードで要件と公開範囲を確認する。
- 自律型: ユーザーが`atk wi process-loop`を起動する。process-loopが反復ごとに開始前更新と専用worktreeを準備し、子セッションで`agent-toolkit:process-wi`を起動する。子セッションへ渡すプロンプトはユーザーの発話ではない。子セッションは`AGENT_TOOLKIT_PROCESS_LOOP_SESSION`でprocess-loopから起動されたことを判別する。
- まとめ処理型: ユーザーが`agent-toolkit:single-lane-process`を手動起動し、たまったWIを1回の実行の中で扱う。

ユーザーは`atk wi process-loop abort`で停止を、`atk wi process-loop instruct`で次の1セッションだけへ渡す指示を、`atk wi process-loop status`で状態を確認する。各コマンドの受理形式は`atk wi process-loop --help`と各サブコマンドのヘルプで確認する。

## 登録、回答、振り返り

ユーザーは`atk wi add`、`agent-toolkit:add-awi-by-user`または`atk serve`のWI画面から要求を登録する。処理中のエージェントと`agent-toolkit:session-review`もWIを投入する。本文、由来、状態と依存は`agent-toolkit:wi-standards`が定める。登録済みの未終端WIを更新・修復するか採否を見直す場合の保留は`agent-toolkit:wi-standards`「状態と依存」に従う。

自律モードの確認手段と、回答を得られない場合のUWIへの切替は`agent-toolkit:user-confirmation-and-report`「手段の選択」に従う。UWIへ退避した確認には、ユーザーが`atk wi answer`または`atk serve`で回答し、process-wiの次の実行のpickerが回答済みUWIと保留中の元項目を取り込む。取り込みと終端は`agent-toolkit:wi-standards`「状態と依存」と`${CLAUDE_PLUGIN_ROOT}/share/pick-wi.subagent.md`が定める。

作業完了後は`agent-toolkit:completion-report`から`agent-toolkit:session-review`が起動する。振り返りが投入したAWIは次のprocess-loopセッションで処理される。

## 運用変更を検討する観点

process-loopが起動したセッション以外では、別セッションのprocess-loopが並行して稼働している前提で対象と状態遷移を調べる。対策を検討するときは、process-loop、子セッションのメイン、レーン担当、まとめ処理型のセッションのどれに作用するかを特定する。各利用形態から対策へ到達できるか、ユーザーの操作が増えるかを比べる。

WI作成、計画、実行、実行レビューの責務と出口は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`が定める。
