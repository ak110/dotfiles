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

- 対象: `exit-session`スキルからの`kill -TERM $PPID`
- ルール本文: 「`agent-toolkit:exit-session`スキル経由の`kill -TERM $PPID`は、自律実行系CLIからの間接起動セッションの
  完遂条件を満たしたときの正当な自律終了操作であり許可する」

## カスタムルール記述の注意点

- 適用対象（対象操作・対象コミットの性質・対象パスなど）を文面に明示する
- 適用対象を限定する条件をルール文面に組み込み、デフォルトの安全境界を維持する
  - 例:「同一セッション内でエージェントが作成したコミット限定」「`origin`への通常pushに限定」など、対象を狭める条件を含める
  - ユーザー意図に依存する条件（「計画・指示で承認された場合」など）はUser Intent Ruleに任せる領域と重複する
  - ルール側では対象を限定する条件を優先する
- デフォルトの`hard_deny`領域を上書きするカスタムルールは追加しない（auto modeの安全境界を逸脱するため）
- `claude auto-mode critique`の指摘内容を採否判定の根拠として参照する
