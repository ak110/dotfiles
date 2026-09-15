---
name: fast-process-wi
description: >
  対象リポジトリの少数のAWIを、レーンへ分けずメインが主作業ツリーでまとめて実装して終端するときに起動する。
disable-model-invocation: true
---

# AWIのまとめ処理

メインがAWIを取得し、同じ処理回で計画、実装、実行レビュー及び終端を行う。選定と実装を委譲して並列化する場合は`../process-wi/SKILL.md`を使う。本スキルの実行中は自律モードとし、WIの共通契約は`../wi-standards/SKILL.md`を正本とする。

## 経路

処理対象を次の2経路へ分ける。

- 直接実装: 既存方針と実装から変更を一意に導ける項目
- 計画: 設計判断、既存契約の変更又はバグ対応を要する項目

計画を要する項目は、件数にかかわらず1つの計画ファイルへまとめる。メインが`agent-toolkit:plan-mode`の計画起草手順に従って作成し、計画のレビュー工程を置かない。専用worktreeを作成せず、主作業ツリーで実装する。

## 不変条件

- 作業ツリーへ書き込む主体はメインだけとする
- 実行レビューは独立した担当が行い、メインが兼ねない
- 人間由来の不採用範囲は、確認を得るまで終端しない
- 手順で固定した集合を再取得せず、処理回へ追加しない
- 計画経路と直接実装経路の両方の対象がある処理回の実行レビューは1件へまとめる

## 実行順

1. processable一覧と各WI本文を取得し、全項目を直接実装又は計画へ分ける。
2. 対象を`processing`へ移し、集合を固定する。
3. 処理開始時のHEADを`git rev-parse --short=7 HEAD`で取得し、起点OIDとして保持する。
4. 計画対象がある場合は`agent-toolkit:plan-mode`のSKILL.mdと計画ファイル基準を全文読み、全項目を1つの計画ファイルへ起草する。`create_plan_files.py`で作成し、`check_plan_file.py --reject-migration-warnings`を単独実行する。
5. 主作業ツリーで、計画対象は`## 要件・外部仕様`、直接実装対象はWIの要求と完成条件に従って実装する。近接検証を実行し、`agent-toolkit:commit`に従ってcommitする。
6. 処理回全体で1件の実行レビューを`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って起動する。計画対象がある処理回では計画ファイルの絶対パスを渡し、直接実装対象がある処理回では当該WIの記録を渡す。両方がある処理回では両方を同じ起動文へ渡す。
7. `${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`に従って指摘を収束させる。修正はメインが行い、同じ起点OIDとレビュー表を継続する。
8. 計画の`## 進捗ログ`へ完了判定を記録し、`check_plan_file.py --reject-migration-warnings`の成功後に`atk plans commit <計画ファイル名>`で保存する。
9. 各WIを採否に応じて`adopt`又は`reject`し、対象リポジトリのタスクランナーが定める全体検査、push、CI及び固有の終端工程を実行する。
10. 一時的なレビュー表を正式な保存又は回収契約に従って処理し、`agent-toolkit:completion-report`で報告する。
