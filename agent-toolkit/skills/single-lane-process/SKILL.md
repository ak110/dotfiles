---
name: single-lane-process
description: >
  対象リポジトリのAWIを、レーンへ分けずメインが主作業ツリーでまとめて実装して終端するときに起動する。
disable-model-invocation: true
---

# AWIの単一レーン処理

メインがAWIを取得し、同じ処理回で計画、実装、実行レビュー及び終端を行う。選定と実装を委譲して並列化する場合は`../process-wi/SKILL.md`を使う。本スキルの実行中は自律モードとし、WIの共通契約は`../wi-standards/SKILL.md`を正本とする。

## process-wi契約の読み替え

`agent-toolkit:process-wi`を名指しする条文は、picker、レーン担当、終端担当又は専用worktreeに依存する場合を除き、本スキルの実行中にも適用する。これらの役割・資源へ依存する契約は直接適用せず、本節が明示する同等の契約だけをメインの工程として適用する。

直接適用しないスキル側の文書は`agent-toolkit/skills/process-wi/`配下とする。
pickerの文書は`${CLAUDE_PLUGIN_ROOT}/share/pick-wi.parent.md`とする。
レーン実行の文書は`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`と`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`とする。
終端担当の文書は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md`及び`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`とする。

本スキルから`agent-toolkit:plan-mode`を起動する場合は、確認事項をUWIへ登録する専用処理経路として扱い、計画の起草後は本スキルの実行順へ戻って主作業ツリーで実装する。計画stemは`dd-HHmm_single-lane-process`とする。実行レビューのレビューイーはメインとする。Codexでは`../plan-mode/references/codex-runtime.md`が専用処理経路へ定めるUWI記録と暫定判断を、本スキルにも適用する。

## 経路

処理対象を次の2経路へ分ける。

- 直接実装: 既存方針と実装から変更を一意に導ける項目
- 計画: 設計判断、既存契約の変更又はバグ対応を要する項目

計画を要する項目は、対象worktreeごとの部分集合に分け、同じ対象worktreeの項目だけを1つの計画ファイルへまとめる。メインが各対象worktreeで`agent-toolkit:plan-mode`の計画起草手順に従って作成し、レビューは実装後の実行レビューだけで行う。単一worktreeだけの処理回では計画ファイルを1つに保つ。専用worktreeを作成せず、対応表が示すworktreeで実装する。

## 不変条件

- 作業ツリーへ書き込む主体はメインだけとする
- 実行レビューは、実装を担当しない独立した担当が行う
- 人間由来の不採用範囲は、ユーザーの確認を得てから終端する
- 手順で固定した集合は、処理回の終わりまでそのまま使う
- 同じ対象worktreeの計画経路と直接実装経路は1件の実行レビューへまとめ、異なる対象worktreeの項目は同じ計画又は実行レビューへ混在させない

## 実行順

1. processable一覧と各WI本文を取得する。同じ時点で`atk wi list --type=uwi --answered=yes --status=processable --target-repo=<repo>`を実行し、回答済みUWIを取得する。回答が作業を求めないUWIは、実装へ着手する前に`atk wi adopt`で終端する。回答が是正や保留中の元項目での作業を求めるUWIは、回答を作業要求として処理回の固定集合へ加える。UWI本文が保留中の元項目のファイル名を示す場合は、元項目を`atk wi unhold`で`inbox`へ戻して同じ固定集合へ加える。残る全項目を直接実装又は計画へ分ける。処理対象に依存が未達の項目が含まれる場合は、依存元を同じ処理回の集合へ加えるかを「確認を要する事項」の経路へ送る。確認を経ずに依存元を加えることと、依存未達の項目を集合から黙って外すことのいずれも選ばない。各WIについて、正本ファイル名、保存済みの`target_repo`、Git操作に使うworktreeの絶対パス及びそのworktreeで解決した処理開始時のHEADの7文字以上の一意な短縮OIDを対応付ける。対応付けた`target_repo`を使って対象を`processing`へ移し、対応表と集合を固定する。
2. 以降の`atk wi`操作は対応表の`target_repo`を使い、Gitの起点比較、実装、検証、commit及びレビューは対応表のworktreeと処理開始OIDを使う。別のworktree又は複製元のHEADを代用しない。
3. 計画対象がある場合は`agent-toolkit:plan-mode`のSKILL.mdと計画ファイル基準を全文読み、対象worktreeごとの部分集合を各1つの計画ファイルへ起草する。計画メタ情報の対象リポジトリと構造検査の`--work-dir`には、その部分集合のworktreeを使う。作成と構造検査は同基準が定める経路で行う。
4. 対応表が示すworktreeで、計画対象は`## 要件・外部仕様`、直接実装対象はWIの要求と完成条件に従って実装する。近接検証を実行し、`agent-toolkit:commit`に従ってcommitする。互いに依存しない対象worktreeの部分集合は並行してよいが、各worktreeへ書き込む主体はメイン1つのまま保つ。
5. 対象worktreeごとに1件の実行レビューを`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って起動する。そのworktreeに計画対象がある場合は対応する計画ファイルの絶対パスを渡し、直接実装対象がある場合は対応するWIの記録を渡す。両方がある場合は同じ起動文へ渡す。計画対象が無い場合は、そのworktreeで解決した処理開始OIDを渡す。レビュー起動時の`cwd`、処理開始OID、レビュー表及びレビュー基準には同じ部分集合の値だけを使う。
6. 対象worktreeごとに`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`に従って指摘を収束させる。修正はメインが行い、同じ起点OIDとレビュー表を継続する。
7. 各計画について`atk run-script plan-progress --`で完了判定を`## 進捗ログ`へ記録し、構造検査の成功を確認してから計画バンドルを保存する。
8. 各WIを採否に応じて`adopt`又は`reject`し、対象リポジトリの開発手順が定める公開前検査とpushを実行する。ローカルで検査する範囲は、その開発手順が定めるローカルとCIの分担に従う。公開前の全体検査をCIへ委ねると定める開発手順では、その開発手順が挙げるローカル検査だけを実行する。分担を定めていない対象リポジトリでは全体検査まで実行する。成果依存が無い複数の対象リポジトリでは全push後にCI監視を全件開始し、対象リポジトリ、ref、baseline及び監視識別子を対応付ける。Claude Codeでは、全ての未終端識別子を条件にした1つの`Monitor`のuntil-loopを開始して終端を待つ。固定時間の`sleep`と、状態変化を伴わない空の`ReadNotifications`の反復はこの待機経路へ置かない。成果依存がある対象とCI成功を入力にする手動workflowは、先行対象の成功後に開始する。対象リポジトリが1件の場合は、push後にその1件の監視を開始する。
   adoptではWIごとに要求を反映した実装commitを`--commit`へ指定する。複数commitに分かれる場合は全OIDと要求単位をメモへ残す。実装差分の無い充足済みWIでは`--commit`を省き、充足の根拠をメモへ残す。
9. 前項の条件待機が全識別子の終端を返した後、開始済みのCI監視結果を識別子ごとに全件回収する。各結果は個別回収し、対象との対応を維持する。待機中は、CIの結果を入力に持たず、かつ待機対象と同じ資源を占有しない対象リポジトリ固有の終端工程を実行する。作業ツリーへ書き込む工程は「不変条件」の書込主体の定めを保つため、この重ね合わせの対象から外す。
10. 一時的なレビュー表を正式な保存又は回収契約に従って処理し、`agent-toolkit:completion-report`で報告する。
