---
name: single-lane-process
description: >
  対象リポジトリのAWIを、レーンへ分けずメインが主作業ツリーでまとめて実装して終端するときに起動する。
disable-model-invocation: true
---

# AWIの単一レーン処理

メインがAWIを取得し、計画、実装、実行レビュー、終端を同じ実行の中で行う。レーンへ委譲して並列化する場合は`../process-wi/SKILL.md`を使う。実行中は自律モードとし、WIの共通契約は`../wi-standards/SKILL.md`、工程の責務と受渡しは`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。

本スキルの処理は、キューと対象worktreeを読み書きするファイル操作、Gitと`atk`のCLI、独立したレビュー担当の起動・受領と参照資料がそろう環境で行う。操作機能が無い場合は、提示されたWIの実施内容と計画の原稿を整理し、未実施の実装・レビュー・公開を明示する。実際の終端はClaude CodeまたはCodexへ渡し、原稿の文には[文章の品質基準](references/quality-standards.md)を使う。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 計画または直接実装に着手する前 | `${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md` |
| 計画対象がある場合に計画を起草する前（実行順3） | `agent-toolkit:plan-mode`のSKILL.mdと同スキルの`references/plan-file-standards.md` |
| 計画あり・なしのどちらでも、メインが自ら実装に着手する直前（実行順4） | `${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`冒頭の`未判定検証記録`の段落と「起動」の`Git管理外の変更`の段落 |
| 対象worktreeごとに公開する前（実行順8） | `agent-toolkit:commit`を起動し、同スキルの`references/publish.md` |
| Claude Codeでpush後のCI監視の待機へ入る前（実行順8） | `agent-toolkit:delegation`の`references/waiting-and-monitoring.md` |

## agent-toolkit:process-wiの規定の読み替え

`agent-toolkit:process-wi`を名指しする条文は本スキルの実行中にも適用する。ただしレーン担当、終端担当、専用worktreeに依存する契約は直接適用せず、本書が示す同等の工程をメインが行う。直接適用しない文書は次のとおり。

- `agent-toolkit/skills/process-wi/`配下
- `${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`、`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`
- `${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md`、`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`

`agent-toolkit:plan-mode`を起動した場合は、確認要否と手段を`agent-toolkit:user-confirmation-and-report`で選び、起草後は本書の実行順へ戻って主作業ツリーで実装する。計画stemは`dd-HHmm_single-lane-process`、実行レビューのレビューイーはメインとする。Codexでは`../plan-mode/references/codex-runtime.md`が専用処理へ定めるUWI記録と暫定判断を本スキルにも適用する。
版数はメインが対象リポジトリの版数規範の単一worktree向け一般則に従って実装段階で更新する。

## 処理の振り分け

計画ファイルの作成要否を`agent-toolkit:plan-mode`「計画ファイルの作成要否」で判定し、作成する項目を計画対象、省く項目を直接実装対象とする。
計画対象は対象worktreeごとに1つの計画ファイルへまとめ、計画のレビューは行わず実装後の実行レビューだけで検証する。専用worktreeは作成せず、対応表のworktreeで実装する。

## 不変条件

- メインが作業ツリーの書込を所有し、担当範囲の一部の書込を委譲先へ分ける場合は、`agent-toolkit:delegation`の`references/mandatory-rules.md`「作業ツリーの保護」に従う
- 実行レビューは実装を担当しない独立した担当が行う
- 人間由来の不採用範囲は、ユーザーの確認を得てから終端する
- 処理対象WIは手順1で固定し、追加は`## 見つけた既存不良の扱い`で投入したAWIと、ユーザーが処理中に告げた項目に限る
- 同じ対象worktreeの計画対象と直接実装対象は1件の実行レビューへまとめ、異なる対象worktreeの項目を同じ計画や実行レビューへ混在させない

## 見つけた既存不良の扱い

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
   対象worktreeごとに公開対象とベースbranchを明示し、`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象の決定」の開始時CI点検を適用する。実際の照会、失敗ログの取得と残工程への作用の判定を実装前に行い、既存失敗は「見つけた既存不良の扱い」、ユーザー依存事項は確認手順へ渡す。公開時のbaselineとCI検収も実行順8で行う。
   `source`が`run-skill`のUWIは次回の`atk run-skill`が扱う。回答済みUWIは実装前に`agent-toolkit:wi-standards`「状態と依存」に従って終端するか処理対象へ加える。依存が未達の項目は、依存元がprocessableで同じ`target_repo`なら同じ実行へ加えて報告する。
   依存元の状態変更や集合からの除外が要る場合は、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」で確認の要否を決める。各WIについてファイル名、保存済みの`target_repo`、Git操作に使うworktreeの絶対パス、そのworktreeの処理開始時のHEADの短縮OIDを対応表にする。
   同資料「集合確定直後の処理開始」の`resolve_repo_identifier`による解決手順で、保存済み`target_repo`と対応worktreeの有効な識別子の一致を確認し、同じworktree指定で取得できることを確かめる。不一致は同資料「処理対象の決定」の移転判定・編集に従い、移転の根拠が無ければ両識別子と不足する根拠を返す。移転が確定した場合は開始前に保存値と本文中の操作先を揃える。
   `atk wi start-processing <ファイル名>... --target-repo=<対応worktreeの絶対パス>`で`processing`へ移して集合を固定する。固定後にユーザーが告げた回答済みUWIとAWIの追加は、`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象WIの追加」の判断基準で扱う。
2. `atk wi`の`adopt`・`reject`へ`--commit`を渡す場合は対象worktreeの絶対パスを`--target-repo`へ渡し、それ以外の操作は保存済み`target_repo`を使う。Gitの起点比較、実装、検証、commit、レビューは対応表のworktreeと開始時のHEADを使い、別のworktreeや複製元のHEADで代用しない。
3. 計画対象がある場合は、対象worktreeごとに計画ファイルを起草する。計画メタ情報の対象リポジトリと計画構造の自動チェックの`--work-dir`にはそのworktreeを使う。
4. 対応表のworktreeで、計画対象は`## 要件・外部仕様`、直接実装対象はWIの要求と完成条件に従って実装する。Git管理外のファイルを変更する場合は最初の変更より前に変更前複製を作成し、証拠要求ありでは実装完了前に未判定検証記録JSONを作成する。複製の対とJSONの絶対パスは計画の`## 進捗ログ`か直接実装の引き継ぎ記録へ残す。検証は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`の受入シナリオ検証に従い、シナリオ別のテスト名と合否を記録する。
   直接実装対象もWIの消費主体と呼び出し手段から同じ基準でテストを選ぶ。テスト出力と手動観測の保存は`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「操作の直前に判定する前提」に従う。
   履歴整理で取得版が変わった記録の再利用は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「根拠の書き方」に従う。互いに依存しないworktreeは並行してよい。
   commitは`agent-toolkit:commit`に従い、WI実装commitごとに同スキル`SKILL.md`「WI実装commitの対応」の手段で、計画（直接実装では引き継ぎ記録）と同じstemの対応記録ファイルへ記録する。レビュー修正・CI修正・履歴変更後も同節に従い、実行順8の前に現在の対応を取得してadoptへ渡す。
5. 対象worktreeごとに1件の実行レビューを起動する。計画対象があれば計画ファイルの絶対パス、直接実装対象があればWIの記録を同じ委譲プロンプトへ渡し、計画対象が無ければそのworktreeの`開始時のHEAD`を渡す。`cwd`、`開始時のHEAD`、レビュー指摘管理表、レビュー基準には同じworktreeの値だけを使う。
6. worktreeごとに`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`に従って指摘を収束させる。修正はメインが行い、同じ`開始時のHEAD`とレビュー指摘管理表を継続する。
7. 各計画について`atk run-script plan-progress --`で完了判定を`## 進捗ログ`へ記録し、計画構造の自動チェックの成功を確認してから計画バンドルを保存する。
8. 各WIを`adopt`か`reject`し（`adopt`は`agent-toolkit:wi-standards`「状態と依存」のcommit対応付けを使う）、worktreeごとに`references/publish.md`に従って公開する。複数の対象リポジトリでは成果の依存順を保ち、独立した対象のpushを先に全て終えてからCI監視を並行して始め、全識別子の終端を待って結果を個別に回収する。CI成功を入力にする公開後の操作はその対象の成功後に行い、成功を入力とせず書込資源を占有しない操作は待機中に進めてよい。
   開発マシン上の常時稼働サーバーへの反映は、`agent-toolkit:completion-report`の`SKILL.md`「工程」の常時稼働サーバーへの反映の段落の条件を満たす場合にCI待機と並行して始め、反映したHEADの一意な短縮OID、反映コマンドの終了状態、稼働確認の結果を完了報告へ渡す。
   Claude Codeでは、独立したpush群の監視と並行するサーバー反映などを開始してから、CI待機で会話圧縮を予約する。予約前にセッションのmanaged-temp直下の`parent-handoff.md`へ再開状態を保存する。固定したWIと対象worktree・開始HEADの対応表、計画集合と保存状態、WIと実装commitの対応と採否の到達点を含める。対象repo・forge・ref・source ref・短縮OID・baselineと監視識別子・出力先の対応、未回収集合、並行反映のHEADと到達点、CI後の操作と依存順も含める。予約と通知後の復元は`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`「長時間待機前の会話圧縮」に従う。起動済みのwait_ci.pyのバックグラウンドタスクを保ち、Monitorで前景を占有せずにターンを終え、通知後は同じ入力と保存出力からCI結果を回収する。CI定義の無い対象にはこの圧縮境界を設けず、Codexと圧縮ツールが無い環境では既存の待機契約を使う。
9. 一時的なレビュー指摘管理表を正式な保存または回収契約に従って処理し、`agent-toolkit:completion-report`で報告する。
