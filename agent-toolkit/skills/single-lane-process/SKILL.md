---
name: single-lane-process
description: >
  対象リポジトリのAWIを、レーンへ分けずメインが主作業ツリーでまとめて実装して終端するときに起動する。
disable-model-invocation: true
---

# AWIの単一レーン処理

メインがAWIを取得し、計画、実装、実行レビュー、終端を同じ実行の中で行う。選定と実装を委譲して並列化する場合は`../process-wi/SKILL.md`を使う。本スキルの実行中は自律モードとし、WIの共通契約は`../wi-standards/SKILL.md`が定める。
WI作成、計画、実行および実行レビューの責務と受渡しは`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 計画対象がある場合に計画を起草する前（実行順3） | `agent-toolkit:plan-mode`のSKILL.mdと同スキルの`references/plan-file-standards.md` |
| 計画あり・なしのどちらでも、メインが自ら実装に着手する直前（実行順4） | `${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`冒頭の`未判定検証記録`の段落と「起動」の`Git管理外の変更`の段落 |
| 対象worktreeごとに公開する前（実行順8） | `agent-toolkit:commit`を起動し、同スキルの`references/publish.md` |

## agent-toolkit:process-wiの規定の読み替え

`agent-toolkit:process-wi`を名指しする条文は、picker、レーン担当、終端担当または専用worktreeに依存する場合を除き、本スキルの実行中にも適用する。これらの役割・資源へ依存する契約は直接適用せず、本節が明示する同等の契約だけをメインの工程として適用する。直接適用しない文書は次のとおりとする。

- スキル側: `agent-toolkit/skills/process-wi/`配下
- picker: `${CLAUDE_PLUGIN_ROOT}/share/pick-wi.parent.md`
- レーン実行: `${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`、`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`
- 終端担当: `${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md`、`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`

本スキルから`agent-toolkit:plan-mode`を起動する場合は、確認要否と手段を`agent-toolkit:user-confirmation-and-report`に従って選び、計画の起草後は本スキルの実行順へ戻って主作業ツリーで実装する。計画stemは`dd-HHmm_single-lane-process`とする。実行レビューのレビューイーはメインとする。Codexでは`../plan-mode/references/codex-runtime.md`が専用処理へ定めるUWI記録と暫定判断を、本スキルにも適用する。

本スキルでは、レーン担当と終端担当を使わないメインが、対象リポジトリの版数規範の単一worktree向け一般則に従い、実装段階で版数を更新する。

## 処理の振り分け

処理対象の計画ファイル作成要否は`agent-toolkit:plan-mode`「計画ファイルの作成要否」で判定する。作成する項目を計画対象、省く項目を直接実装対象とする。

計画を要する項目は、対象worktreeごとの部分集合を1つの計画ファイルへまとめる。メインが各対象worktreeで`agent-toolkit:plan-mode`の計画起草手順に従って作成し、レビューは実装後の実行レビューだけで行う。専用worktreeを作成せず、対応表が示すworktreeで実装する。

## 不変条件

- 作業ツリーへ書き込む主体はメインだけとする
- 実行レビューは、実装を担当しない独立した担当が行う
- 人間由来の不採用範囲は、ユーザーの確認を得てから終端する
- 手順で固定した処理対象WIは、実行の終わりまでそのまま使う。`## 見つけた既存不良の扱い`で投入したAWIと、手順1のユーザーが処理中に告げた項目は例外として加える
- 同じ対象worktreeの計画対象と直接実装対象は1件の実行レビューへまとめ、異なる対象worktreeの項目は同じ計画または実行レビューへ混在させない

## 見つけた既存不良の扱い

既存不良を同じ実行で直すかの判定基準は`agent-toolkit:process-wi`の`SKILL.md`「即時対応」の判定基準の段落に従う。同スキルの即時対応の手順はレーンと是正レーンを前提とするため直接適用せず、委譲先を使わずメインが直す本節の手順を適用する。元の作業と文脈を分けるのは調査だけとし、修正はメインが行う。
公開の開始前は、共通基準に従って導入した不良と残工程への具体的作用を先に判定する。即時対応に当たる不良のうち、第2項で直せるものは同項で直し、それ以外は第1項の追加処理を行う。延期する不良は第1項のAWI投入で次回へ回す。共通基準の即時対応に当たる不良を、工程が止まらないことや不良の種類だけで次回へ回さない。

- 公開（実行順8）の開始前に見つけた既存不良は、`agent-toolkit:bugfix`で初動判定する。拡張原因分析、類似見直しまたは再発防止策を要し、前段の判定基準に当たるものは、メインが不良ごとに調査、AWIの起草および投入を委譲する。委譲は`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`に従う。投入されたAWIは実行順1と同じく`atk wi start-processing <ファイル名> --target-repo=<対応付けたtarget_repo>`で`processing`へ移す。その後、処理対象WIと対応表へ加え、自ら実装し、是正分はそのworktreeの実行レビューへ含める。追加の是正で見つかった不良も同じ基準で扱う。該当しない不良は同じ委譲でAWIを`source: process-wi`で投入し、次の実行へ回す。現在の実行を妨げる不良と本作業で導入した不良は同じ実行で扱う
- 共通基準の即時対応に当たり、拡張原因分析を要さず1箇所で是正が完結する軽微な不良は、`agent-toolkit:bugfix`の`SKILL.md`「問題を見つけたときの対処」に従ってメインがその場で直す
- 公開の開始後に見つけた不良のうち、公開を妨げるもの（CI失敗など）は公開の手順の中で直す。公開を妨げないものは、同じ委譲で不良ごとにAWIを`source: process-wi`で投入し、次の実行へ回す

## 実行順

実行レビューは`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って起動し、名前付き入力`未判定検証記録`は同書冒頭の規定に従って渡す。

1. processable一覧と各WI本文を取得する。同じ時点で`atk wi list --type=uwi --answered=yes --status=processable --source=!run-skill --target-repo=<対象リポジトリの絶対パス>`を実行し、回答済みUWIを取得する。`source`が`run-skill`のUWIは同じスキルの次回の`atk run-skill`の実行が扱うため、処理対象から外す。取得した回答済みUWIは、実装へ着手する前に`agent-toolkit:wi-standards`「状態と依存」の回答済みUWIの取り込みに従って終端するか処理対象WIへ加える。残る全項目を直接実装または計画へ分ける。処理対象に依存が未達の項目が含まれる場合は、依存元の状態を確かめる。依存元がprocessableで同じ`target_repo`なら、依存元を同じ実行の処理対象WIへ加えて報告する。依存元の状態を変える必要がある場合は、保留・cooldownの理由と依存を調べ、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」を適用する。技術的待機・既存認可内の状態調整は自ら確定し、未回答のユーザー判断や目的側の不採用に必要な認可が残る部分だけを確認する。依存未達の項目を集合から外す場合も、原要求と既存認可を調べて必要な確認を判定する。各WIについて、WIのファイル名、保存済みの`target_repo`、Git操作に使うworktreeの絶対パスおよびそのworktreeで解決した処理開始時のHEADの7文字以上の一意な短縮OIDを対応付ける。対象を`atk wi start-processing <ファイル名>... --target-repo=<対応付けたtarget_repo>`で`processing`へ移し、対応表と集合を固定する。固定した後も、ユーザーが処理中に告げた回答済みUWIとAWIは同じ実行の処理対象WIと対応表へ加え、回答済みUWIは同じ取り込みに従って終端するか元項目とともに加える。告げた発話を明示指示とする判断には、`${CLAUDE_PLUGIN_ROOT}/share/pick-wi.parent.md`「処理対象WIの追加」の判断基準だけを適用する。
2. `atk wi`操作のうち`adopt`または`reject`へ`--commit`を渡す場合は、対応表の対象worktreeの絶対パスを`--target-repo`へ渡す。commit検証を伴わない操作は対応表の保存済み`target_repo`を使う。共通前提は`agent-toolkit:wi-standards`「状態と依存」に従う。Gitの起点比較、実装、検証、commitおよびレビューは対応表のworktreeと開始時のHEADを使う。別のworktreeまたは複製元のHEADを代用しない。
3. 計画対象がある場合は読込表の行の資料に従い、`## 処理の振り分け`の部分集合ごとに計画ファイルを起草する。計画メタ情報の対象リポジトリと計画構造の自動チェックの`--work-dir`にはその部分集合のworktreeを使う。作成と計画構造の自動チェックは同基準が定める手順で行う。
4. 対応表が示すworktreeで、計画対象は`## 要件・外部仕様`、直接実装対象はWIの要求と完成条件に従って実装する。実装前の資料は読込表の実行順4の行に従う。Git管理外のファイルを変更する場合は最初の変更より前に変更前複製を作成し、証拠要求ありでは実装の完了前に未判定検証記録JSONを作成する。複製の対とJSONの絶対パスは、計画対象では計画の`## 進捗ログ`、直接実装対象では直接実装の引き継ぎ記録へ残す。計画対象は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`の受入シナリオ検証で検証し、シナリオ別のテスト名と合否を記録する。直接実装対象もWIの消費主体と呼び出し手段から同じテストを選ぶ。テスト出力と手動観測の保存は`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「操作の直前に判定する前提」に従う。履歴の整理で取得版が変わった記録の再利用は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「根拠の書き方」に従う。`agent-toolkit:commit`に従ってcommitする。互いに依存しない対象worktreeの部分集合は並行してよい（書き込む主体は`## 不変条件`のとおりメインだけ）。
   WI実装commitの完了ごとに、`agent-toolkit:commit`の`SKILL.md`「WI実装commitの対応」の記録の手段で、短縮OIDと対応AWI集合を計画と同じstemの対応記録ファイルへ残す。直接実装では引き継ぎ記録を用意し、同じ手段でその引き継ぎ記録と同じstemの対応記録ファイルへ記録する。レビュー修正・CI修正と履歴変更後も同節に従って対応を継続し、実行順8の終端前に同節の取得の手段で対象worktreeの現在の対応を取得してadoptへ渡す。
5. 対象worktreeごとに1件の実行レビューを`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って起動する。そのworktreeに計画対象がある場合は対応する計画ファイルの絶対パスを渡し、直接実装対象がある場合は対応するWIの記録を渡す。両方がある場合は同じ委譲プロンプトへ渡す。計画対象が無い場合は、そのworktreeで解決した`開始時のHEAD`を渡す。レビュー起動時の`cwd`、`開始時のHEAD`、レビュー指摘管理表およびレビュー基準には同じ部分集合の値だけを使う。
6. 対象worktreeごとに`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`に従って指摘を収束させる。修正はメインが行い、同じ`開始時のHEAD`とレビュー指摘管理表を継続する。
7. 各計画について`atk run-script plan-progress --`で完了判定を`## 進捗ログ`へ記録し、計画構造の自動チェックの成功を確認してから計画バンドルを保存する。
8. 各WIを採否に応じて`adopt`または`reject`し、対象worktreeごとに`agent-toolkit:commit`を起動し、読込表の行の`references/publish.md`に従って公開する。`adopt`では`agent-toolkit:wi-standards`「状態と依存」のcommit対応付けを使い、`--target-repo`は実行順2に従う。複数の対象リポジトリでは成果依存を保ち、独立した対象のpushを先に全件終えてからCI監視を並行開始する。対象リポジトリ、ref、baselineおよび監視識別子を対応付け、全識別子の終端を待って結果を個別に回収する。CI成功を入力にするプロジェクト固有の公開後の操作は、その対象の成功後に行う。待機中は結果を入力とせず同じ書込資源を占有しないプロジェクト固有の公開後の操作を進めてよい。
   開発マシン上の常時稼働サーバーへの反映は、`agent-toolkit:completion-report`の`SKILL.md`「工程」の常時稼働サーバーへの反映の段落が定める反映手段と稼働確認手段を持つ対象で、実装・レビューが収束したHEADを使える場合にCI待機と並行して始める。並行して始められる条件は同段落に従う。反映したHEADの完全OID、反映コマンドの終了状態と稼働確認の結果を保持して完了報告へ渡す。後続のcommitによる差分は完了報告の同段落で判定する。
   Claude Codeでは、全ての未終端識別子を条件とする1つの`Monitor`のuntil-loopで終端を待つ。待機はこの`Monitor`に任せ、固定時間の`sleep`と状態変化の無い空の`ReadNotifications`の反復を省く（努力目標。状態変化の無い照会の往復を避ける）。
9. 一時的なレビュー指摘管理表を正式な保存または回収契約に従って処理し、`agent-toolkit:completion-report`で報告する。
