# Claude Code auto modeのカスタムルール

auto modeは利用者環境の`~/.claude/settings.json`の`autoMode.allow`配列に自然言語の許可指示を追加できる。
追加した指示はデフォルトの`soft_deny`判定を上書きする。

## ルール区分

auto modeは次の4区分でルールを判定する。

- `allow`: 明示的に許可する操作
- `soft_deny`: 既定では拒否するが、ユーザー指示や文脈で`clears`される操作
- `hard_deny`: いかなる場合も拒否する操作
- `environment`: 信頼境界（リポジトリ・ドメイン・バケット・サービス）の定義

## CLIサブコマンド

- `claude auto-mode defaults`: デフォルトの全ルール（`allow`・`soft_deny`・`hard_deny`・`environment`）をJSON形式で出力する
- `claude auto-mode config`: 現在有効な設定（デフォルト＋カスタム）を表示する
- `claude auto-mode critique`: カスタムルールをAIがレビューし、曖昧・冗長・偽陽性のリスクを指摘する

## カスタムルール追加のワークフロー

1. 拒否事象に遭遇したら`claude auto-mode defaults`でデフォルトルールを確認する
2. 該当ルールの`clears when ...`例外条件を読み、誤拒否されている領域を特定する
3. 設定ファイルの`autoMode.allow`配列に自然言語の許可指示を追加する
   - 配布側の設定ファイル（chezmoi等のsource）を使う場合は当該sourceを編集する
     - デプロイ手順で利用者環境の`~/.claude/settings.json`へ反映する
   - 配布側を持たない場合は利用者環境の`~/.claude/settings.json`を直接編集する
4. 追加文面はデフォルトの`soft_deny`判定を狭く上書きする位置付けとし、対象操作・適用条件・許容範囲を明示する
5. 設定ファイル編集後、Claude Codeに設定を再ロードする
   - 再ロード後の状態で`claude auto-mode config`を実行し、当該項目が有効設定として表示されることを確認する
6. `claude auto-mode critique`で文面のAIレビューを通し、重大な指摘があれば文面を修正する

## 既知の誤拒否パターンと対応

### `git commit --amend`の`autonomous post-review cleanup`拒否

デフォルト`soft_deny`「Git Destructive」ルールは、エージェント自身がセッション内で作成したHEADへのamendを`clears`する。
しかし別判断軸（例:`autonomous post-review cleanup`）でamend統合フローのamendが拒否されるケースがある。
レビュー指摘修正後にamendで統合する運用を採用する場合、該当範囲を狭く許容するカスタムルールを`autoMode.allow`に追加する。

### exit-session `kill -TERM $PPID` の Interfere With Workloads 拒否

auto mode classifierは`kill -TERM $PPID`を「エージェントが作成していない親プロセスの終了」とみなし拒否する。
`agent-toolkit:exit-session`スキルは起動条件を厳密に限定する設計だが、既定classifierでは拒否対象となる。

対応: `share/claude_settings_json_managed.json`の`autoMode.allow`配列へ以下の自然言語ルールを追加する。

- 対象: `exit-session`スキルからの`kill -TERM $PPID`。
- ルール本文: 「`agent-toolkit:exit-session`スキル経由の`kill -TERM $PPID`は、
  同スキルの起動条件を満たした状態でのシグナル送出であり許可する」とする。
  起動条件の内訳は`agent-toolkit:exit-session`スキル本文「起動条件」節を参照する。

### サブエージェント委譲経路のcommit/edit拒否

本節はharnessレベルの技術的ブロック（classifier拒否）への対応フローであり、
`agent-toolkit/rules/02-collaboration.md`「自律実行モード」節のオーバーライド対象外とする。
自律実行モード下でも本フローの`AskUserQuestion`はTBD記録へ置換せず通常発行する。

`plan-impl-executor`等のサブエージェント委譲経路で発行される操作を対象とする。
対象は`git commit`・`git commit --amend`・`Write`／`Edit`／`MultiEdit`によるファイル編集である。
これらがauto mode classifierに拒否される場合がある。
拒否理由が「クロスセッションのteammate messageのみに基づく」等、
サブエージェント側で有効な指示に対するfalse positive判定である可能性が高い場合の対応フロー。

1. 拒否理由をメッセージ本文で確認する。
   拒否分類名（`claude auto-mode defaults`出力で確認可能な分類名）を必ず含める
2. false positive判定である可能性が高い場合、`AskUserQuestion`で次の選択肢を提示する
   - false positiveとして実行を承認する
   - 該当操作を撤回する
   - サブエージェント側でレビューを完了してから判断する
3. ユーザー選択に応じて次の後続動作を実行する
   - 承認を選んだ場合はメイン側で該当操作を直接実行する
   - 撤回を選んだ場合は該当操作を破棄し、サブエージェントへ撤回した旨と再開時の条件を返却する
   - レビュー完了待ちを選んだ場合はサブエージェント側のレビューを起動し、
     完了報告の受領後に再度本フローの1へ戻り拒否再現の有無を確認する
4. classifierに再度拒否された場合は該当パターン・該当内容（拒否対象コマンド・拒否理由文）を
   明示してユーザーへ再確認する。同一操作の反復試行はしない
5. 該当パターンが確定次第、
   本ファイル冒頭の`## カスタムルール追加のワークフロー`に従い`autoMode.allow`へ追加し恒久対応する

### 承認ゲート緩和コミットの`Self Modification`拒否

自律実行系フローは次の操作をコミットする場合がある。
承認ゲートの緩和（`pretooluse.py`の完了判定フラグ削減・計画ゲートスクリプトの検査を警告型へ格下げする変更等）。
規範文書の完遂挙動規定の改訂（完遂原則・縮退表明規定の変更等）。
エージェント設定の配布原本の変更（`share/claude_settings_json_managed.json`のpermission許可リスト拡大等）。
これらの操作はデフォルトの`soft_deny`区分「Self Modification」ルールにより拒否される。
計画ファイルの`## 背景`へユーザー発話原文を転記済みでも、
起動元がクロスセッションのメッセージであることを理由に拒否される場合があることを実機で観測した。

対応: 次の2段で対処する。

- `share/claude_settings_json_managed.json`の`autoMode.allow`配列へカスタムルールを追加する。
  条件は正規のフィードバック処理フロー由来・ユーザー投入フィードバック限定
  （`source: session-review`等の自己生成起点を除外）・計画レビュー工程経由とする
- ルール適用外で拒否が続く場合は`AskUserQuestion`で「この判定はfalse positiveか」を明示的に問い、
  false positiveである旨の回答を得てから再試行する（進行への同意のみでは`clears`されない）

## カスタムルール記述の注意点

- 適用対象（対象操作・対象コミットの性質・対象パスなど）を文面に明示する
- 適用対象を限定する条件をルール文面に組み込み、デフォルトの安全境界を維持する
  - 例:「同一セッション内でエージェントが作成したコミット限定」「`origin`への通常pushに限定」など、対象を狭める条件を含める
  - ユーザー意図に依存する条件（「計画・指示で承認された場合」など）はUser Intent Ruleに任せる領域と重複する
  - ルール側では対象を限定する条件を優先する
- デフォルトの`hard_deny`領域を上書きするカスタムルールは追加しない（auto modeの安全境界を逸脱するため）
- `claude auto-mode critique`の指摘内容を採否判定の根拠として参照する
