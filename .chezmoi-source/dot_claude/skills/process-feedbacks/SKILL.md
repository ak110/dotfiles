---
name: process-feedbacks
description: >
  対象リポジトリごとに蓄積された未処理フィードバックとTBD回答済み項目を、
  `dotfiles-fb`で取得して`agent-toolkit:apply-feedback`へ委譲する。
  `dotfiles-fb adopt`・`reject`・`tbd-adopt`で採否確定後の履歴保持まで一貫して扱う。
# 連携: 対象リポジトリの未処理フィードバックとTBD回答済み項目のうちステップ1で選定した対象を
#   まとめて agent-toolkit:apply-feedback へ委譲する。
---

# フィードバック消化

`dotfiles-fb`の全サブコマンドは内部で`git pull --ff-only`を実行する。
対象は`add`・`list`・`show`・`adopt`・`reject`・`rm`・`edit`・`commit`および
`tbd-adopt`・`tbd-edit`等のtbd系サブコマンドを含む。
手動での`git pull`実行は不要とする。
`adopt`・`reject`は採否確定を管理側へ反映する（管理側リポジトリの操作は`dotfiles-fb`が内部で完結する）。
対象リポジトリ（dotfiles等）側のcommit/pushは別途必要とする。

## ステップ1: 全件取得

`/process-feedbacks <repo-path>`の形式で対象リポジトリパスを引数として受け取った場合は当該パスを対象リポジトリとして扱う。
引数なしの場合は`git rev-parse --show-toplevel`で取得した現リポジトリパスを対象リポジトリとして扱う（既定）。

`dotfiles-fb show --all --status=answered --target-repo=<対象リポジトリパスまたは正規化リモートURL>`を実行し
feedback全件とTBD回答済みの本文を取得する。
出力は`# feedback`・`# tbd`種別ヘッダで区分けされる。
`--status=answered`はTBD側のフィルターとして働き、feedback側の出力には影響しない。
正規化リモートURLは`host/owner/repo`形式とする。
出力が空（`### <filename>`見出しが1件も存在しない）の場合は「処理対象なし」と示して終了する。
1件以上の場合は`### <filename>`見出しの件数を1文でユーザーに提示する。
`dotfiles-fb show`が非ゼロ終了する場合（feedback-inbox無効化などが該当する）は、
標準エラー出力のエラーメッセージをユーザーへ提示して終了する。

### 選定対象の確定

ステップ1で取得した一覧を基に本セッションの処理対象を選定する。

- 4件以下の場合は全件を処理対象として固定する
- 5件以上の場合は次の手順で選定する
  1. 関連グループを形成する。グループ化基準は次のとおり
     - 同一ファイルを対象とする
     - 同一スキル・同一エージェント定義を対象とする
     - 同一規範（節・概念）を対象とする
     - 同一根本原因への複数対応を含む
     - 1件のフィードバックが複数の基準に該当する場合は、優先順位
       （同一根本原因・同一規範・同一スキル／エージェント定義・同一ファイルの順）で
       最上位のグループに帰属させ、重複所属を回避する
  2. 関連度が強い順（グループ内フィードバック数が多い順）に評価する
  3. 最初の関連グループが5件以上に達した時点で選定を確定する
  4. どの関連グループも5件未満の場合は、関連度が強い順にグループを合算し、
     5件以上に達した時点で選定を確定する
  5. 6件以上を処理対象とすることも妨げない（上限規定は設けない）
- 選定の確定タイミングは本ステップ（着手前）に限定する。
  選定確定後は対象を減らさず、残件はinboxへ残置して次回起動時の処理対象とする。
  この選定は`dotfiles-fb process-loop`の設計上の分割（inbox残存を引き継ぎ媒体とする自動継続）であり縮退表明に該当しない。
  いかなる理由（例: 作業量・コンテキスト消費の自己推定）があっても、
  固定済み対象の途中放棄・選定済み件数の事後削減は
  `agent-toolkit/rules/01-agent.md`「セッション分割・別計画化は禁止する」節の縮退表明として禁止対象とする

起動以降にinboxへ追加されたファイルは本セッションでは扱わず、次回起動時の処理対象とする。

## ステップ2: 選定対象をapply-feedbackへ一括委譲

1. 対象リポジトリのディレクトリへカレントを移す
2. ステップ1で選定した対象ファイル分の`### <filename>`ブロックのみを
   `agent-toolkit:apply-feedback`スキルへそのまま渡して委譲する。
   除外分の`### <filename>`ブロックは委譲対象から除去する
   - `dotfiles-fb show --all`の出力は既に`### <filename>`見出しで区切られた結合形式であり、
     選定対象ブロックの抽出以外の追加の結合・整形は不要とする
   - `~/private-notes/feedback/inbox/`配下への直接アクセス（`Read`・`cat`・`ls`等）は禁止する
     （管理側の抽象化を破り、Windows等の環境依存で表示が壊れる可能性もあるため）
   - frontmatterは出力に保持されており`source: session-review`などの投入元情報を
     apply-feedback側で参照できる
   - `apply-feedback`は批判的検討・採否判定・計画作成・実装・コミット・後始末（adopt/reject）まで担う
   - 後始末（adopt/reject）では、`dotfiles-fb`が採否確定ファイルを履歴として保持する
   - 選定した対象を1度の`apply-feedback`セッションで処理する（1件ずつ委譲しない）
     - ただし単一フィードバックが対象50ファイル以上の大規模な一括処理を要求する場合は、
       `agent-toolkit:apply-feedback`配下`references/plan-split.md`の分離処理規定に従い、
       当該フィードバックを他フィードバックから分離した独立計画として扱う
   - 本スキル経由で`apply-feedback`→`plan-mode`とネスト起動される場合、
     `plan-mode`スキルはplan mode外で実行する。メイン側で`EnterPlanMode`を発行しない
3. 委譲時の追加指示として、apply-feedbackが作成する計画ファイルの`## 実行方法`へ
   採否確定後に該当する後始末手順を含めるよう明示する。
   後始末はapply-feedbackのplan-mode実装工程内で実施される
   - `dotfiles-fb adopt`・`dotfiles-fb reject`・`dotfiles-fb tbd-adopt`は
     対象リポジトリのレビュー完遂・`git push`完了後に実行する。
     いずれも採否確定を管理側へ即時反映するため、
     対象リポジトリ側がレビュー指摘で巻き戻った場合に
     管理側だけが先行公開され整合性が崩れることを避ける
   - feedback側の採用ファイルがある場合: `dotfiles-fb adopt <filename1> <filename2> ... --note <概要> --commit <sha>`を実行する
   - feedback側の不採用ファイルがある場合:
     `dotfiles-fb reject <filename1> <filename2> ... --note <不採用理由> --commit <sha>`を実行する
   - TBD側の回答済み採用ファイルがある場合:
     `dotfiles-fb tbd-adopt <filename1> <filename2> ... --note <概要> --commit <sha>`を実行する。
     TBD側の不採用フローは本スキルでは扱わない（保留・削除は既存`tbd-edit`で対応する）
   - `--note`・`--commit`の詳細規定は
     `agent-toolkit:apply-feedback`スキル配下`references/decision-format.md`「後始末コマンドの引数」節に従う
   - 保留ファイルがある場合: 後始末コマンドは実行しない
     （`dotfiles-fb`は次回`show`で自動的に再評価対象として提示する）
   - push後は`agent-toolkit:commit`スキル「push後のCI通過確認」節に従いCI通過を確認してから後始末コマンドを実行する
   - apply-feedback配下のplan-mode委譲では全工程を遵守すること
     （工程2〜8。工程2は2.5・2.6・2.7を含む）。
     auto mode下・単独foreground委譲下でも省略しない

## ステップ3: サマリー提示

apply-feedback完了後、採用N件・不採用N件・保留N件のサマリーに
残件数と選定基準（関連グループ・規模）を加えてユーザーに提示する。
残件が0件の場合はその旨も明記する。

## ステップ4: 振り返り工程

本ステップの実行主体は本スキルを起動したエージェント自身とする。
メインエージェント／サブエージェントの起動形態を問わず、
サブエージェントから起動された場合も親エージェントへ委譲・返却せず、
当該サブエージェント内で本ステップまで完遂する。

サマリー提示後、`agent-toolkit:session-review`スキルと
`session-review-dotfiles`スキルの両方を起動して振り返り工程を完遂する。
振り返り工程完遂前に完了を示す応答を発行しない。
