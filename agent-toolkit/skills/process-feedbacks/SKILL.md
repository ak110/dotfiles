---
name: process-feedbacks
description: >
  対象リポジトリごとに蓄積された未処理フィードバックとTBD回答済み項目を、
  `atk fb`で取得し、`agent-toolkit:apply-feedback`を呼び出して処理する。
  `atk fb adopt`・`reject`・`tbd-adopt`で採否確定後の履歴保持まで一貫して扱う。
# 連携: 対象リポジトリの未処理フィードバックとTBD回答済み項目の全件を
#   まとめて`agent-toolkit:apply-feedback`へ引き渡す。
---

# フィードバック消化

`atk fb`の全サブコマンドは内部で`git pull --ff-only`を実行する。
対象は`add`・`list`・`show`・`adopt`・`reject`・`rm`・`edit`・`commit`および
`tbd-adopt`・`tbd-edit`等のtbd系サブコマンドを含む。
手動での`git pull`実行は不要とする。
`adopt`・`reject`は採否確定を管理側へ反映する（管理側リポジトリの操作は`atk fb`が内部で完結する）。
対象リポジトリ側のcommit/pushは別途必要とする。
ユーザーから特段の指示が無い場合も`agent-toolkit:autopilot`スキルを併用し、
ユーザー確認事項は`TBD.md`へ記録して処理を続行するものとする。

## ステップ1: 全件取得

`/process-feedbacks <repo-path>`の形式で対象リポジトリパスを引数として受け取った場合は当該パスを対象リポジトリとして扱う。
引数なしの場合は`git rev-parse --show-toplevel`で取得した現リポジトリパスを対象リポジトリとして扱う（既定）。

`atk fb show --all --status=answered --target-repo=<対象リポジトリパスまたは正規化リモートURL>`を実行し
feedback全件とTBD回答済みの本文を取得する。
出力は`# feedback`・`# tbd`種別ヘッダで区分けされる。
`--status=answered`はTBD側のフィルターとして働き、feedback側の出力には影響しない。
正規化リモートURLは`host/owner/repo`形式とする。
出力が空（`### <filename>`見出しが1件も存在しない）の場合は「処理対象なし」と示して終了する。
1件以上の場合は`### <filename>`見出しの件数を1文でユーザーに提示する。
取得した全件を本セッションの処理対象とする（件数・規模による選定は行わない）。
`atk fb show`が非ゼロ終了する場合（feedback-inbox無効化などが該当する）は、
標準エラー出力のエラーメッセージをユーザーへ提示して終了する。
本ステップで取得したスナップショット以降にinboxへ追加されたファイルは、次回起動時の処理対象とする。

## ステップ2: 取得した全件をapply-feedbackへ一括で引き渡す

1. 対象リポジトリのディレクトリへカレントを移す
2. ステップ1で取得した`### <filename>`ブロック全件を
   `agent-toolkit:apply-feedback`スキルへそのまま渡して起動する
   - `atk fb show --all`の出力は既に`### <filename>`見出しで区切られた結合形式であり、
     追加の結合・整形は不要とする
   - フィードバック管理repo配下への直接アクセス（`Read`・`cat`・`ls`等）は禁止する。
     管理側の抽象化を破り、Windows等の環境依存で表示が破損する可能性がある。
     管理repoのrootは`AGENT_TOOLKIT_PRIVATE_NOTES`環境変数で指定する
     （詳細は`agent-toolkit:agent-standards`スキル「識別子と環境変数」節）
   - frontmatterは出力に保持されており`source: session-review`などの
     投入元情報をapply-feedback側で参照できる
   - `apply-feedback`は批判的検討・採否判定・計画作成・実装までを担い、
     コミット以降の後続工程は`agent-toolkit:apply-feedback-finish`へ一元的に委ねる
   - 全件を1度の`apply-feedback`呼び出しで1計画へ統合して処理する。
     計画ファイルの分割はしない（1コミット・1レビューで完遂する）
   - 本スキル経由で`apply-feedback`→`plan-mode`とネスト起動される場合、
     `plan-mode`スキルはplan mode外で実行する。メイン側で`EnterPlanMode`を発行しない。
     ネスト起動下でも`plan-mode`工程2〜8（工程2は2.5・2.6・2.7を含む）を遵守する。
     auto mode下・単独foreground委譲下でも同様とする
3. 起動時の追加指示として、apply-feedbackが作成する計画ファイルの`## 実行方法`末尾へ
   `agent-toolkit:apply-feedback-finish`スキルに従い後続工程を実施する旨を含めるよう明示する

## ステップ3: サマリー提示と後続工程

apply-feedback完了後、採用N件・不採用N件・保留N件のサマリーをユーザーに提示する。
サマリー提示後、`agent-toolkit:apply-feedback-finish`スキルへ進み後続工程を完遂する
（振り返り工程の実行主体規定は同スキル「工程5: 振り返り」節に従う）。
振り返り工程完遂前に完了を示す応答は発行しない。
