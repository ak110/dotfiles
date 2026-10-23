---
name: single-lane-process
description: >
  対象リポジトリのAWIを、レーンへ分けずメインが主作業ツリーでまとめて実装して終端するときに起動する。
disable-model-invocation: true
---

# AWIの単一レーン処理

メインがAWIを取得し、計画、実装、実行レビュー、終端を同じ実行の中で行う。レーンへ委譲して並列化する場合は`../process-wi/SKILL.md`を使う。実行中は自律モードとし、WIの共通契約は`../wi-standards/SKILL.md`、工程の責務と受渡しは`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。

本スキルの処理は、キューと対象worktreeを読み書きするファイル操作、Gitと`atk`のCLI、独立したレビュー担当の起動・受領と参照資料がそろう環境で行う。操作機能が無い場合は、提示されたWIの実施内容と計画の原稿を整理し、未実施の実装・レビュー・公開を明示する。実際の終端はClaude CodeまたはCodexへ渡し、原稿の文には[文章の品質基準](references/quality-standards.md)を使う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/single-lane-process/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/exec.subagent.md` | 判断 | `read:agent-toolkit:*/share/exec.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/session-termination.parent.md` | 判断 | `read:agent-toolkit:*/share/session-termination.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/session-termination.subagent.md` | 判断 | `read:agent-toolkit:*/share/session-termination.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../plan-mode/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../plan-mode/SKILL.md#「計画ファイルの作成要否」` | 判断 | `section:agent-toolkit:*/skills/plan-mode/SKILL.md#「計画ファイルの作成要否」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../delegation/references/mandatory-rules.md#「作業ツリーの保護」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/mandatory-rules.md#「作業ツリーの保護」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi/SKILL.md#「即時対応」` | 判断 | `section:agent-toolkit:*/skills/process-wi/SKILL.md#「即時対応」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../bugfix/references/root-cause-analysis.md#「類似見直し」「類似見直しと再発防止の完了」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「類似見直し」「類似見直しと再発防止の完了」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md#冒頭` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#冒頭` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../bugfix/SKILL.md#「問題を見つけたときの対処」` | 判断 | `section:agent-toolkit:*/skills/bugfix/SKILL.md#「問題を見つけたときの対処」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md#「状態と依存」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「状態と依存」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../writing-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/testing.md#「検証単位の選定」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「検証単位の選定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/exec.subagent.md#「操作の直前に判定する前提」` | 判断 | `section:agent-toolkit:*/share/exec.subagent.md#「操作の直前に判定する前提」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../review-standards/references/exec-review-recording.md#「根拠の書き方」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「根拠の書き方」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../commit/SKILL.md#「WI実装commitの対応」` | 判断 | `section:agent-toolkit:*/skills/commit/SKILL.md#「WI実装commitの対応」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/review-loop-coordination.md` | 判断 | `read:agent-toolkit:*/share/review-loop-coordination.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../completion-report/SKILL.md#「工程」` | 判断 | `section:agent-toolkit:*/skills/completion-report/SKILL.md#「工程」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 計画または直接実装に着手する前 | `${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md` | 判断 | `read:agent-toolkit:*/share/single-lane-execution.md` | |
| 計画対象がある場合に計画を起草する前（実行順3） | `agent-toolkit:plan-mode`のSKILL.mdと同スキルの`references/plan-file-standards.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/references/plan-file-standards.md` | |
| 計画あり・なしのどちらでも、メインが自ら実装に着手する直前（実行順4） | `${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`冒頭の`未判定検証記録`の段落と「起動」の`Git管理外の変更`の段落 | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#冒頭` | |
| 対象worktreeごとに公開する前（実行順8） | `agent-toolkit:commit`を起動し、同スキルの`references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` | |
| Claude Codeでpush後のCI監視の待機へ入る前（実行順8） | `agent-toolkit:delegation`の`references/waiting-and-monitoring.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md` | |

## agent-toolkit:process-wiの規定の読み替え

`agent-toolkit:process-wi`を名指しする条文は本スキルの実行中にも適用する。ただしレーン担当、終端担当、専用worktreeを前提とする契約は直接適用せず、本書が示す同等の工程をメインが行う。直接適用しない文書は次のとおり。

- `agent-toolkit/skills/process-wi/`配下
- `${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`、`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`
- `${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md`、`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`

`agent-toolkit:plan-mode`を起動した場合は、確認要否と手段を`agent-toolkit:user-confirmation-and-report`で選び、起草後は本書の実行順へ戻って主作業ツリーで実装する。計画stemは`dd-HHmm_single-lane-process`、実行レビューのレビューイーはメインとする。Codexでは`../plan-mode/references/codex-runtime.md`が専用処理へ定めるUWI記録と暫定判断を本スキルにも適用する。
版数はメインが対象リポジトリの版数規範の単一worktree向け一般則に従って実装段階で更新する。

## 処理の振り分け

計画ファイルの作成要否を`agent-toolkit:plan-mode`「計画ファイルの作成要否」で判定し、作成する項目を計画対象、省く項目を直接実装対象とする。
初期選定の全WIを保存した対応表から、対象worktreeの全計画対象を1つの計画ファイルへまとめ、計画のレビューは行わず実装後の実行レビューだけで検証する。専用worktreeは作成せず、対応表のworktreeで実装する。

## 不変条件

- メインが作業ツリーの書込を所有し、担当範囲の一部の書込を委譲先へ分ける場合は、`agent-toolkit:delegation`の`references/mandatory-rules.md`「作業ツリーの保護」に従う
- 実行レビューは実装を担当しない独立した担当が行う
- 人間由来の不採用範囲は、ユーザーの確認を得てから終端する
- 処理対象WIは手順1で固定し、追加は`## 見つけた既存不良の扱い`で投入したAWIと、ユーザーが処理中に告げた項目に限る
- 保存済み対応表の同じ対象worktreeの全計画対象と全直接実装対象の和を1件の実行レビューへまとめ、異なる対象worktreeの項目を同じ計画や実行レビューへ混在させない

## 見つけた既存不良の扱い

実行レビューが返した差分外の既存不良は`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「返却に含まれる既存不良の受領」で専念型のWI投入担当へ渡し、本節の手順と処理対象への追加の対象から除く。同節の例外とする残りの工程を停止・遮断する不良と、その他の不良は次のとおり扱う。
同じ実行で直すかは`agent-toolkit:process-wi`の`SKILL.md`「即時対応」の判定基準の段落で判定する。同節の手順はレーンを前提とするため、本スキルでは調査だけを委譲し、修正はメインが行う。扱った問題と根本原因を同じくする不良の範囲と完了は`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「類似見直し」「類似見直しと再発防止の完了」に従う。
公開の開始前は、不良が本作業で導入したものか、残る工程へ具体的に作用するかを先に判定する。即時対応に当たる不良を、工程が止まらないことや不良の種類だけで次回へ回さない。

- 公開（実行順8）の開始前に見つけた不良は`agent-toolkit:bugfix`で初動判定する。
  拡張原因分析、類似見直しまたは再発防止策を要し即時対応に当たるものは、`agent-toolkit:wi-standards`冒頭のAWIの粒度で分けた要求ごとに、調査とAWIの起草・投入を`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`に従って委譲する。
  投入されたAWIは`atk wi start-processing <ファイル名> --target-repo=<対応付けたtarget_repo>`で`processing`へ移して処理対象と対応表へ加え、自ら実装してそのworktreeの実行レビューへ含める。
  即時対応に当たらない不良は同じ委譲で`source: process-wi`のAWIとして投入し次回へ回す
- 根本原因と類似見直しの母集団を自ら確定でき、その全対象と再発防止策をその場で処置できる不良は、`agent-toolkit:bugfix`の`SKILL.md`「問題を見つけたときの対処」に従ってその場で直す
- 公開の開始後に見つけた不良は、公開を妨げるもの（CI失敗など）だけ公開の手順の中で直し、それ以外は同じ委譲でAWIとして投入する

## 実行順

計画・実装・独立した実行レビューの調整は`${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md`の共通契約に従う。以下は本スキル固有の対象取得、worktree対応付けと公開の手順を定める。実行レビューは`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って起動し、`未判定検証記録`は同書冒頭の規定に従って渡す。

1. processable一覧と各WI本文を取得し、同時に`atk wi list --type=uwi --answered=yes --status=processable --source=!run-skill --target-repo=<対象リポジトリの絶対パス>`で回答済みUWIを取得する。
   対象worktreeごとに公開対象とベースbranchを明示し、`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象の決定」の開始時CI点検を適用する。実際の照会、失敗ログの取得と残工程への作用の判定を実装前に行い、既存失敗は「見つけた既存不良の扱い」、ユーザーだけが決められる事項は確認手順へ渡す。公開時のbaselineとCI検収も実行順8で行う。
   `source`が`run-skill`のUWIは次回の`atk run-skill`が扱う。回答済みUWIは実装前に`agent-toolkit:wi-standards`「状態と依存」に従って終端するか処理対象へ加える。依存が未達の項目は、依存元がprocessableで同じ`target_repo`なら同じ実行へ加えて報告する。
   依存元の状態変更や集合からの除外が要る場合は、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」で確認の要否を決める。各WIについてファイル名、保存済みの`target_repo`、Git操作に使うworktreeの絶対パス、そのworktreeの処理開始時のHEADの短縮OID、開始前の状態、計画対象か直接実装対象か、完了済みと残工程を対応表にする。処理開始前に全件を既存の引き継ぎ記録へ保存し、後工程の比較元にする。
   同資料「集合確定直後の処理開始」の`resolve_repo_identifier`による解決手順で、保存済み`target_repo`と対応worktreeの有効な識別子の一致を確認し、同じworktree指定で取得できることを確かめる。不一致は同資料「処理対象の決定」の移転判定・編集に従い、移転の根拠が無ければ両識別子と不足する根拠を返す。移転が確定した場合は開始前に保存値と本文中の操作先を揃える。
   初回の`atk wi start-processing <ファイル名>... --target-repo=<対応worktreeの絶対パス>`の引数は保存済み対応表のうち開始前にinboxだった全件からworktreeごとに導く。開始済み項目は重複遷移させない。途中失敗はCLIの診断と残存状態を同じ対応表へ記録し、未完了工程から再開する。ユーザーの明示追加と即時対応の追加は同じ対応表へ反映し、後工程の比較元を更新する。固定後にユーザーが告げた回答済みUWIとAWIの追加は、`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象WIの追加」の判断基準で扱う。
2. `atk wi`の`adopt`・`reject`へ`--commit`を渡す場合は対象worktreeの絶対パスを`--target-repo`へ渡し、それ以外の操作は保存済み`target_repo`を使う。Gitの起点比較、実装、検証、commit、レビューは対応表のworktreeと開始時のHEADを使い、別のworktreeや複製元のHEADで代用しない。
3. 計画対象がある場合は、対象worktreeごとに計画ファイルを起草する。計画メタ情報の対象リポジトリと計画構造の自動チェックの`--work-dir`にはそのworktreeを使い、対応表の全計画対象を`--expected-wi <WIファイル名>`で全件反復指定して実装前に一致を確かめる。全件が直接実装対象なら計画を新設せず、対応表とレビュー集合の和で被覆を検収する。
4. 対応表のworktreeで、計画対象は`## 要件・外部仕様`、直接実装対象はWIの要求と完成条件に従って実装する。Git管理外のファイルを変更する場合は最初の変更より前に変更前複製を作成し、証拠要求ありでは実装完了前に未判定検証記録JSONを作成する。複製の対とJSONの絶対パスは計画の`## 進捗ログ`か直接実装の引き継ぎ記録へ残す。検証は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`の受入シナリオ検証に従い、シナリオ別のテスト名と合否を記録する。
   計画なしとレビュー修正でも、実装前に公開された呼び出し手段・入力と初期状態・有効な期待結果・検出する失敗を確定する。試験は`agent-basics:writing-standards/references/testing.md`「検証単位の選定」で選ぶ。公開された呼び出し手段の実結果を自己検収し、既存の未判定検証記録へ対応付ける。未実施と条件変更の判断、メインが所有する残工程の担当と再開先を分けて独立した実行レビューへ渡す。テスト出力と手動観測の保存は`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「操作の直前に判定する前提」に従う。
   履歴整理で取得版が変わった記録の再利用は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「根拠の書き方」に従う。互いの成果を使わないworktreeは並行してよい。
   commitは`agent-toolkit:commit`に従い、WI実装commitごとに同スキル`SKILL.md`「WI実装commitの対応」の手段で、計画（直接実装では引き継ぎ記録）と同じstemの対応記録ファイルへ記録する。レビュー修正・CI修正・履歴変更後も同節に従い、実行順8の前に現在の対応を取得してadoptへ渡す。
5. 保存済み対応表から対象worktreeの全計画対象と全直接実装対象の和を導き、1件の実行レビューを起動する。全対応表の所在と区分を同じ引き継ぎからたどれるようにする。計画対象があれば計画ファイルの絶対パス、直接実装対象があればWIの記録を同じ委譲プロンプトへ渡し、計画対象が無ければそのworktreeの`開始時のHEAD`を渡す。`cwd`、`開始時のHEAD`、レビュー指摘管理表、レビュー基準には同じworktreeの値だけを使う。
6. worktreeごとに`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`に従って指摘を収束させる。修正はメインが行い、同じ`開始時のHEAD`とレビュー指摘管理表を継続する。
7. 各計画について`atk run-script plan-progress --`で完了判定を`## 進捗ログ`へ記録し、計画構造の自動チェックの成功を確認してから計画バンドルを保存する。
8. 各WIを`adopt`か`reject`し（`adopt`は`agent-toolkit:wi-standards`「状態と依存」のcommit対応付けを使う）、worktreeごとに`references/publish.md`に従って公開する。複数の対象リポジトリでは成果の依存順を保ち、独立した対象のpushを先に全て終えてからCI監視を並行して始め、全識別子の終端を待って結果を個別に回収する。CI成功を入力にする公開後の操作はその対象の成功後に行い、成功を入力とせず書込資源を占有しない操作は待機中に進めてよい。
   開発マシン上の常時稼働サーバーへの反映は、`agent-toolkit:completion-report`の`SKILL.md`「工程」の常時稼働サーバーへの反映の段落の条件を満たす場合にCI待機と並行して始め、反映したHEADの一意な短縮OID、反映コマンドの終了状態、稼働確認の結果を完了報告へ渡す。
   Claude Codeでは、独立したpush群の監視と並行するサーバー反映などを開始してから、CI待機で会話圧縮を予約する。予約前にセッションのmanaged-temp直下の`parent-handoff.md`へ再開状態を保存する。処理開始前に保存した対応表の全体と完了済み・残工程を保持する。中断・圧縮後は同じ表から復元し、要約にある作業順で選定集合を置き換えない。固定したWIと対象worktree・開始HEADの対応表、計画集合と保存状態、WIと実装commitの対応と採否の到達点を含める。対象repo・forge・ref・source ref・短縮OID・baselineと監視識別子・出力先の対応、未回収集合、並行反映のHEADと到達点、CI後の操作と依存順も含める。予約と通知後の復元は`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`「長時間待機前の会話圧縮」に従う。起動済みのwait_ci.pyのバックグラウンドタスクを保ち、Monitorで前景を占有せずにターンを終え、通知後は同じ入力と保存出力からCI結果を回収する。CI定義の無い対象にはこの圧縮境界を設けず、Codexと圧縮ツールが無い環境では既存の待機契約を使う。
9. 一時的なレビュー指摘管理表を正式な保存または回収契約に従って処理し、`agent-toolkit:completion-report`で報告する。
