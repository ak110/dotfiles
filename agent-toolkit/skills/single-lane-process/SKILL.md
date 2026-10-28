---
name: single-lane-process
description: >
  対象リポジトリのAWIを、レーンへ分けずメインが主作業ツリーでまとめて実装して終了するときに起動する。
disable-model-invocation: true
---

# AWIの単一レーン処理

メインエージェントがAWIを取得し、計画の作成、実装、実行レビュー、および終了処理までを単一の実行フローの中でまとめて行う。別レーンへ委譲して並列処理する場合は`../process-wi/SKILL.md`を使用する。実行中は自律モードで動作し、WIの共通契約は`../wi-standards/SKILL.md`、工程ごとの責務と受け渡しは`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。

本スキルの処理は、キューや対象worktreeを読み書きできるファイル操作環境、Gitおよび`atk`のCLI、独立したレビュー担当の起動・結果受領手段、および参照資料がすべてそろっている環境で実施する。これらの操作機能が備わっていない環境では、提示されたWIの実施内容と計画原稿を整理し、未実施の実装・レビュー・公開タスクを明示する。実際の終了処理はClaude CodeまたはCodexに委ね、原稿の記述には[文章の品質基準](references/quality-standards.md)を用いる。

## 名指し参照の読込方法

本文に記載された各参照の適用条件を確認し、選んだ資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断へ進む。起動時の行に記載された資料は、起動本文とともに配送される。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文の参照基準を適用する前 | `../delegation/references/waiting-claude-code.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-claude-code.md` |
| 工程の責務と受け渡しを確認する前 | `../../share/workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` |
| 操作機能がなく計画原稿を整理する前 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/single-lane-process/references/quality-standards.md` |
| 複数レーンへ処理を切り替える前、または既存不良の即時対応を判断する前 | `../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| ユーザー確認の要否と手段を選ぶ前 | `../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 書き込みを委譲先へ分担させる前 | `../delegation/references/mandatory-rules.md#「作業ツリーの保護」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/mandatory-rules.md#「作業ツリーの保護」` |
| 不良の類似見直しと再発防止を判断する前 | `agent-basics:bugfix/references/root-cause-remediation.md#「類似見直し」「類似見直しと再発防止の完了」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-remediation.md#「類似見直し」「類似見直しと再発防止の完了」` |
| 不良を発見し対処を判断する前 | `../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| WIの共通契約を適用する前 | `../wi-standards/SKILL.md#冒頭` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#冒頭` |
| 対象WIの選定と処理開始を判断する前 | `../process-wi/references/selection-procedure.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure.md` |
| 既存不良の正式対応を登録する前 | `../../share/add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 回答済みUWIの処理またはWIを終了する前 | `../wi-standards/SKILL.md#「状態と依存」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「状態と依存」` |
| 取得時の版が変わった記録を再利用する前 | `../review-standards/references/exec-review-recording.md#「根拠の書き方」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「根拠の書き方」` |
| WI実装commitの対応を記録する前 | `../commit/SKILL.md#「WI実装commitの対応」` | 判断 | `section:agent-toolkit:*/skills/commit/SKILL.md#「WI実装commitの対応」` |
| 公開時の常時稼働サーバー反映または終了処理の前 | `../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 既存不良の受領、計画または直接実装に着手する前 | `${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md` | 判断 | `read:agent-toolkit:*/share/single-lane-execution.md` | |
| 計画の要否を判定する前 | `agent-toolkit:plan-mode`のSKILL.md。計画対象がある場合は、計画の起草前（実行順3）に同書の初版起草の行が指定する範囲も読む | 判断 | `skill:plan-mode` | |
| 対象worktreeごとに公開する前（実行順8） | `agent-toolkit:commit`を起動し、同スキルの`references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` | |
| Claude Codeでpush後のCI監視の待機へ入る前（実行順8） | `agent-toolkit:delegation`の`references/waiting-and-monitoring.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md` | |

## agent-toolkit:process-wiの規定の読み替え

`agent-toolkit:process-wi`を名指ししている条文は、本スキルの実行中にも適用する。ただし、レーン担当、公開担当、専用worktreeを前提とする契約は直接適用せず、本書で示す同等の工程をメインエージェント自身が担う。直接適用しない文書は次のとおりである。

- `agent-toolkit/skills/process-wi/`配下
- `${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`、`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`（所在参照）
- `${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md`、`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`（所在参照）

`agent-toolkit:plan-mode`を起動した場合は、ユーザー確認の要否と手段を`agent-toolkit:user-confirmation-and-report`で選択し、計画の起草後は本書の実行順に戻って主作業ツリーで実装を進める。計画のstemは`dd-HHmm_single-lane-process`とし、実行レビューのレビューイーはメインエージェントとする。Codex環境では、`../plan-mode/references/codex-runtime.md`が専用処理に対して定めているUWI記録と暫定判断のルールを本スキルにも適用する。
バージョン番号は、対象リポジトリの版数管理規範における単一worktree向けの一般原則に従い、実装段階でメインエージェントが更新する。

## 処理の振り分け

計画ファイルの作成要否を`agent-toolkit:plan-mode`「計画ファイルの作成要否」に従って判定し、作成が必要な項目を「計画対象」、省略できる項目を「直接実装対象」として分類する。
初期選定されたすべてのWIを記録した保存済み対応表をもとに、対象worktreeの全計画対象を1つの計画ファイルへまとめ、計画段階でのレビューは省略して実装後の実行レビューのみで検証する。専用のworktreeは新設せず、対応表に記載されたworktreeで実装する。

## 不変条件

- メインエージェントが作業ツリーの書き込み権限を所有し、担当範囲の一部の書き込みを委譲先へ分担させる場合は、`agent-toolkit:delegation`の`references/mandatory-rules.md`「作業ツリーの保護」に従う
- 実行レビューは、実装を担当していない独立した担当者が実施する
- 人間由来の変更で不採用とする範囲がある場合は、ユーザーの確認を得た後に終了する
- 処理対象のWIは手順1の時点で固定し、処理途中での追加はユーザーから明示的に指示された項目に限る
- 保存済み対応表において同一の対象worktreeに属する全計画対象と全直接実装対象の和を1件の実行レビューにまとめ、異なる対象worktreeの項目を同一の計画や実行レビューに混在させない

## 見つけた既存不良の扱い

実行レビューから指摘として返された、今回の変更差分外にある既存不良は、`${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md`「継続する工程」にある既存不良の受領契約に従って専任のWI投入担当へ渡し、本節の手順や今回の処理対象への追加からは除外する。ただし、その受領契約で例外とされている、残りの工程を停止・遮断してしまう不良やその他の不良については、次のように取り扱う。
今回の実行の中で修正するかどうかは、`agent-toolkit:process-wi`の`SKILL.md`「即時対応」に従って判定する。メインエージェントは処理の進行に必要な範囲に限定して修正し、安全に利用できることを確認した上で、正式対応のためのAWIを次回セッション用として登録する。この正式対応AWIは、今回の処理対象や対応表には追加しない。
公開工程の開始前には、発見した不良が今回の作業で混入したものなのか、それとも残る工程へ具体的に影響するものなのかをあらかじめ判定する。即時対応に該当する不良を、「工程が完全に停止しているわけではない」という理由や不良の種別だけを根拠にして次回へ先送りしない。

- 公開工程（実行順8）の開始前に見つけた不良は、観測事象、進行への影響度、および直接修正すべき範囲を確認する。必要な修正を実施した後、観測結果、差分、検証結果、未完了の正式対応内容を`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`の要求事項として渡し、`source: process-wi`のAWIとして保存する
- 今回の作業で混入させた不良や、初期選定された要求に属する類似箇所については、元の要求の範囲内で是正する。独立した既存不良の正式対応を勝手に追加して、当初の作業範囲を拡大しない
- 公開工程の開始後に見つけた不良は、CI失敗のように公開を直接阻害するもののみを公開手順の中で修正し、それ以外の不良は同様に委譲して次回用のAWIとして登録する

## 実行順

計画作成、実装、独立した実行レビューの進行管理、起動と返却後の検収、および`検証結果`の記録は、`${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md`の共通契約に従う。以下では、本スキル固有の対象取得、worktreeの紐付け、公開の手順を定める。

1. 処理可能なWI（processable）の一覧と各WIの本文を取得する。同時に`atk wi list --type=uwi --answered=yes --status=processable --source=!run-skill --target-repo=<対象リポジトリの絶対パス>`を実行して回答済みUWIを取得する。
   対象worktreeごとに公開対象とベースブランチを明示し、`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象の決定」に定める開始時のCI点検を実施する。実装に着手する前に実際のCI状況を照会し、失敗ログの取得と残工程への影響判定を行う。既存の失敗は「見つけた既存不良の扱い」に従って処理し、ユーザーのみが判断できる事項は確認手順へと回す。公開時のベースライン確認とCI検収も実行順8で実施する。
   なお、`source`が`run-skill`であるUWIは次回の`atk run-skill`実行時に処理する。回答済みUWIは実装前に`agent-toolkit:wi-standards`「状態と依存」に従って終了するか処理対象へ加える。依存関係が未解決の項目は、依存元のWIがprocessableであり同一の`target_repo`を対象としている場合に限り、今回の実行に含めて報告する。
   依存元の状態変更や集合からの除外が必要な場合は、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」に従って確認の要否を判断する。各WIについて、ファイル名、保存されている`target_repo`、Git操作に使用するworktreeの絶対パス、そのworktreeの処理開始時のHEADの短縮OID、開始前の状態、計画対象か直接実装対象かの区分、完了済み工程と残工程を対応表にまとめる。処理を開始する前に、全件の内容を既存の引き継ぎ記録へ保存し、後続工程における比較の基準とする。
   同資料「集合確定直後の処理開始」に従い、`atk wi show`を用いて保存された`target_repo`と対応worktreeのoriginが一致していることを確認する。不一致がある場合は、同資料「処理対象の決定」の移転判定・編集ルールに従う。移転の根拠が得られない場合は、両方の識別子と不足している根拠を報告する。移転が確定した場合は、処理開始前に保存値と本文中の操作先を揃える。
   初回の`atk wi start-processing <ファイル名>... --target-repo=<対応worktreeの絶対パス>`に渡す引数は、保存済み対応表の中で開始前にinbox状態であった全件からworktreeごとに抽出する。すでに処理開始済みの項目を重複して状態遷移させない。途中で処理が失敗した場合は、CLIの診断メッセージと残存状態を同じ対応表に記録し、未完了の工程から再開する。ユーザーからの明示的な追加指示は対応表へ反映し、後続工程の比較基準を更新する。集合固定後にユーザーから指示された回答済みUWIやAWIの追加は、`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象WIの追加」の判断基準に従って扱う。
2. `atk wi`の`adopt`や`reject`に`--commit`を渡す場合は、対象worktreeの絶対パスを`--target-repo`に指定し、それ以外の操作では保存されている`target_repo`を使用する。Gitの起点比較、実装、検証、コミット、レビューには対応表のworktreeと開始時のHEADを使用し、別のworktreeや複製元のHEADで代用しない。
3. 計画対象が存在する場合は、対象worktreeごとに計画ファイルを起草する。計画メタ情報の対象リポジトリ、および計画構造の自動チェックに渡す`--work-dir`にはそのworktreeを指定し、対応表にあるすべての計画対象を`--expected-wi <WIファイル名>`で繰り返し指定して、実装前に過不足のない一致を確認する。全件が直接実装対象である場合は計画ファイルを新設せず、対応表とレビュー対象集合の和によって網羅性を検収する。
4. 対応表のworktreeにおいて、計画対象は`## 要件・外部仕様`に従い、直接実装対象はWIの要求事項と完成条件に従って実装する。受入シナリオの具体化、検証単位の選定、検証結果の保存、独立文脈レビューへの受渡しは`${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md`「継続する工程」に従う。Git管理外のファイルを変更する場合は、最初の変更前に変更前のバックアップ複製を作成し、複製の対応関係は計画ファイルの`## 進捗ログ`、または直接実装の引き継ぎ記録に残す。
   コミット履歴の整理によって取得時の版が変わった記録の再利用については、`agent-toolkit:review-standards`の`references/exec-review-recording.md`「根拠の書き方」に従う。互いの成果に依存しないworktree間では作業を並行して進めてよい。
   コミットの作成は`agent-toolkit:commit`に従い、WI実装コミットごとに同スキルの`SKILL.md`「WI実装commitの対応」に定める手段を用いて、計画（直接実装の場合は引き継ぎ記録）と同一stemの対応記録ファイルへ記録する。レビュー指摘修正、CI修正、コミット履歴の変更後も同様の手順に従い、実行順8の処理へ進む前に最新の対応関係を取得してadoptコマンドへ渡す。
5. 保存済み対応表から、対象worktreeに属する全計画対象と全直接実装対象の和を導出し、1件の実行レビューを起動する。すべての対応表の所在と区分は、同一の引き継ぎ記録から辿れるように保持する。計画対象がある場合は計画ファイルの絶対パスを、直接実装対象がある場合はWIの記録を委譲プロンプトへ渡し、計画対象が存在しない場合はそのworktreeの「開始時のHEAD」を渡す。`cwd`、`開始時のHEAD`、レビュー指摘管理表、およびレビュー基準には、同一worktreeの値のみを使用する。
6. worktreeごとに`${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md`「継続する工程」に従ってレビュー指摘を収束させる。指摘に対する修正作業はメインエージェント自身が行い、同一の「開始時のHEAD」とレビュー指摘管理表を引き続き使用する。
7. 各計画について`atk run-script plan-progress --`を実行して完了判定を`## 進捗ログ`へ記録し、計画構造の自動チェックが成功することを確認した上で計画バンドルを保存する。
8. 各WIを`adopt`または`reject`し（`adopt`時は`agent-toolkit:wi-standards`「状態と依存」に定めるコミット対応付けを用いる）、worktreeごとに`references/publish.md`に従って公開する。複数のリポジトリを対象とする場合は成果物間の依存順序を守り、互いに独立したリポジトリのpushを先にすべて完了させてからCI監視を並行して開始し、すべての実行識別子の終了を待って結果を個別に回収する。CIの成功を前提とする公開後操作はその対象の成功が確認されてから行い、CI成功を前提とせず排他リソースも占有しない操作は待機時間中に進めてよい。
   開発マシン上で動作する常時稼働サーバーへの反映は、`agent-toolkit:completion-report`の`SKILL.md`「工程」内の「常時稼働サーバーへの反映」段落に記載された条件を満たす場合に、CI待機と並行して開始する。反映したHEADの一意な短縮OID、反映コマンドの終了ステータス、および稼働確認の結果を完了報告へ渡す。
   Claude Code環境では、独立したpush群の監視や並行するサーバー反映処理などを開始した後に、CI待機に伴う会話圧縮を予約する。中断や会話圧縮の後は、注入された要約を起点に計画、WI、資源登録、CI監視出力、担当別引き継ぎ記録から状態を復元し、要約文に書かれた作業順などで選定集合を勝手に置き換えない。予約と通知後の復元処理は、`agent-toolkit:delegation`の`references/waiting-claude-code.md`「長時間待機前の会話圧縮」に従う。起動済みのwait_ci.pyバックグラウンドタスクを維持し、Monitorツール等で前景を占有することなくターンを終え、完了通知を受信した後に同一の入力と保存出力からCI結果を回収する。CIが定義されていない対象にはこの圧縮境界を設けず、Codexや会話圧縮ツールが存在しない環境では既存の待機契約に従う。
9. 一時的に作成したレビュー指摘管理表を正式な保存または回収契約に従って処理し、成果・検証・公開結果を`agent-toolkit:completion-report`の作業完了報告へ一度集約して送達する。公開結果の受領だけで終了せず、同スキルが定める終了処理まで行う。同期の振り返りは終了工程の対象外とする。
