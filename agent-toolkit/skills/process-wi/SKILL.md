---
name: process-wi
description: >
  対象リポジトリのAWIを取得し、レーンへ分けて計画、実装、レビュー、統合及び公開を完遂するときに起動する。
---

# AWIのレーン処理

選定時に固定したAWIをレーンへ分け、各レーンの同じ担当threadが計画の起草から統合までを担う。メインは選定、計画境界の確認、実行レビューの調整、公開工程及びセッション終端を担う。
AWIとUWIの共通契約は`../wi-standards/SKILL.md`を正本とする。本スキルの実行中は自律モードとする。

## 不変条件

- 全ての実装要求を計画工程へ送り、各レーンのレーン担当が実装前に1つの計画ファイルを起草する
- 計画のレビュー工程を置かず、実装後に要件・外部仕様水準の実行レビューを計画ごとに1件行う
- 1つの専用worktreeへ書き込む主体は、計画、実装、レビュー修正及び統合を担う同じレーン担当threadだけとする
- メインはレーン担当の稼働中に専用worktreeへ書き込まず、レーン担当は主作業ツリーへ統合するとき以外に書き込まない
- 人間由来の要求を全部又は一部不採用にする場合は、ユーザーの確認又はUWIの回答を得るまで当該項目を終端しない
- 選定時に固定した集合へ処理中の追加項目を混ぜず、ready一覧を再取得しない
- 作業対象リポジトリへpushする主体は公開工程の終端担当だけとする
- 計画、レビュー表、worktree及び管理対象一時領域は、それを使う全工程の完了後にだけ回収する

## 実行順

1. 個人プロジェクトでは`sync-cross-project`を起動し、同期と依存更新の要否を確定する。
2. `${CLAUDE_PLUGIN_ROOT}/share/pick-wi.parent.md`を全文読み、pickerによる対象選定、処理開始、前セッションの振り返り及び監査を開始する。
3. `references/run-lanes.md`を全文読み、選定結果から専用worktreeとレーンを作成し、レーン担当を起動する。
4. 各レーンから`計画作成完了`を受領し、計画の`## 概要`と`## 実施内容`だけから由来、不採用範囲及び実装有無を確認して、`実装開始`又は`実装なし`を返す。
5. `実装完了`と検証結果を受領したレーンごとに、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`と`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`へ従って実行レビューを収束させる。
6. 同じレーン担当threadへ`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.parent.md`に従って統合を指示し、計画最終化とAWI終端までを完了させる。
7. 全レーンの終端、振り返り及び監査の処置確定後、`references/finish-session.md`を全文読み、公開とセッション終端を完遂する。

## 読み分け

- 選定と出力契約: `${CLAUDE_PLUGIN_ROOT}/share/pick-wi.parent.md`
- レーン作成、採否及び中断再開: `references/run-lanes.md`
- レーン担当の起動と工程境界: `${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`
- 実行レビュー: `${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`
- 統合: `${CLAUDE_PLUGIN_ROOT}/share/lane-integration.parent.md`
- 公開と終了: `references/finish-session.md`

## 終端

選定、レーン又は公開工程が確認待ちとなる場合は、依存しない工程を継続する。回答を得られない確認は`agent-toolkit:wi-standards`に従ってUWIへ記録し、依存するAWIを`inbox`かつ`blocked`で保持する。
通常の完了報告は`agent-toolkit:completion-report`に従う。本スキルの工程で生じたcommitをローカルだけに残したまま完了を報告しない。
