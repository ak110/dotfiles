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
- 選定時に固定した集合へ通常の新着項目を混ぜず、ready一覧を再取得しない。処理中に同一セッション修正が必須となった完結済みAWIだけは`references/run-lanes.md`の是正レーンとして追加する
- 作業対象リポジトリへpushする主体は公開工程の終端担当だけとする
- 公開工程の終端担当の起動は1セッションにつき1回とし、当該終端担当の終端後に当該セッションで生じた成果はローカルのベースbranchへ保持して次のセッションの公開工程で公開する
- 計画、レビュー表、worktree及び管理対象一時領域は、それを使う全工程の完了後にだけ回収する

## 局所変更の即時公開

ユーザーが、局所変更を現在のセッションで即時に公開することと、次回の`agent-toolkit:process-wi`で正式に対応することの両方を同じ指示で明示した場合だけ、本節を適用する。単なる迅速化の要求、通常のAWI処理又はエージェント判断で本節へ切り替えない。

即時公開する変更に対応する近接検査を成功させ、同じ要求を正式な計画、実装、実行レビュー、全体検査及びCI成功確認へ送るAWIを登録する。即時公開済みであることを当該AWIの充足又は不採用の根拠にしない。公開工程では`references/finish-session.md`へ`検証・CI方針: 即時対応`、近接検査の結果及び正式対応AWIのファイル名を渡す。

この条件を満たさない公開工程は`検証・CI方針: 通常`とし、全体検査とCI成功確認を維持する。

## 実行順

1. 対象リポジトリが個人プロジェクトに該当するかの判定手段は`ak110-projects-operations`が定める。該当する場合は同スキルを起動し、同期と依存更新の要否を確定する。
2. `${CLAUDE_PLUGIN_ROOT}/share/pick-wi.parent.md`を全文読み、pickerによる対象選定、処理開始及び監査を開始する。
3. `references/run-lanes.md`を全文読み、選定結果から専用worktreeとレーンを作成し、レーン担当を起動する。
4. 各レーンから`計画作成完了`を受領し、計画の`## 概要`と`## 実施内容`だけから由来、不採用範囲及び実装有無を確認して、`実装開始`又は`実装なし`を返す。
5. `実装完了`と検証結果を受領したレーンごとに、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`と`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`へ従って実行レビューを収束させる。
6. 同じレーン担当threadへ`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.parent.md`に従って統合を指示し、計画最終化とAWI終端までを完了させる。
7. 全レーンの終端及び監査の処置確定後、`references/finish-session.md`を全文読み、`検証・CI方針`を明示して公開とセッション終端を完遂する。

## 読み分け

- 選定と出力契約: `${CLAUDE_PLUGIN_ROOT}/share/pick-wi.parent.md`
- レーン作成、採否及び中断再開: `references/run-lanes.md`
- レーン担当の起動と工程境界: `${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`
- 実行レビュー: `${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`
- 統合: `${CLAUDE_PLUGIN_ROOT}/share/lane-integration.parent.md`
- 公開と終了: `references/finish-session.md`

## 終端

選定、レーン又は公開工程が確認待ちとなる場合は、依存しない工程を継続する。回答を得られない確認は`agent-toolkit:wi-standards`に従ってUWIへ記録し、当該回答を得るまで進められないAWIを`atk wi hold`で保留する。
通常の完了報告は`agent-toolkit:completion-report`に従う。本スキルの工程で生じたcommitをローカルだけに残したまま完了を報告しない。
