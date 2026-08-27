# Claude Code auto modeのカスタムルール

auto modeはユーザー環境の`~/.claude/settings.json`の`autoMode.allow`配列に自然言語の許可指示を追加できる。
`allow`・`soft_deny`・`hard_deny`・`environment`の各配列は、設定すると当該区分のデフォルト一覧を置き換える。
デフォルトを維持したまま追加するには、配列へリテラル文字列`"$defaults"`を含める。
`allow`の許可指示はデフォルトの`soft_deny`判定を上書きする。
区分外のキー（`allowMode`等）は無効であり、実在は公式スキーマ（`$schema`のURL）で確認する。

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

## 権限設定による拒否の確認手順

権限設定によりツール呼び出しが拒否された場合、迂回を試みる前に有効な設定ファイルを`Read`して
拒否・許可ルールを確認する。対象は`/etc/claude-code/managed-settings.json`・
`~/.claude/settings.json`・リポジトリ直下の`.claude/settings.json`・`.claude/settings.local.json`とする。
評価はdeny・ask・allowの順で最初の一致が結果を決めるため、
拒否ルールに該当する対象は許可・参照範囲の追加では解消しない（努力目標）。
権限評価はpermissionsルール（deny→ask→allowの順で最初の一致が確定）→作業ディレクトリ内編集等の自動承認→
auto mode classifierの順で行われる。
PreToolUseフックの`permissionDecision: "allow"`はpermissions評価を迂回しない。
auto modeの拒否ではなく従来の確認ダイアログが対象の場合は本節の対象外であり、
`agent-toolkit:agent-standards`のHook実装ガイドラインにある`PermissionRequest`節で自動許可を扱う。

## カスタムルール追加のワークフロー

1. 拒否事象に遭遇したら`claude auto-mode defaults`でデフォルトルールを確認する
2. 該当ルールの`clears when ...`例外条件を読み、誤拒否されている領域を特定する
3. 設定ファイルの`autoMode.allow`配列に自然言語の許可指示を追加する
   - 配布側の設定ファイル（chezmoi等のsource）を使う場合は当該sourceを編集する
     - デプロイ手順でユーザー環境の`~/.claude/settings.json`へ反映する
   - 配布側を持たない場合はユーザー環境の`~/.claude/settings.json`を直接編集する
4. 追加文面はデフォルトの`soft_deny`判定を狭く上書きする位置付けとし、対象操作・適用条件・許容範囲を明示する
5. 設定ファイル編集後、Claude Codeに設定を再ロードする
   - 再ロード後の状態で`claude auto-mode config`を実行し、当該項目が有効設定として表示されることを確認する
   - `"$defaults"`を含む区分では、デフォルトルールが展開されて件数が維持されていることも確認する
6. 配布元がある場合は、配布元が所有する契約を既存の対象固有テストで確認する
7. 設定調整の完了後は毎回`claude auto-mode critique`を実行して結果を確認する。
   指摘は過剰に厳しい傾向があるため全件採用を必須とせず、`01-agent.md`の判断指針
   （必要十分な最小限・問題と手段の比例性）に従い採否を確定する
   - 変更前後の指摘を安定して対応付けられる場合は、変更前から存在する不変の指摘を除外し、
     変更後の新規・悪化指摘をラベルにかかわらず優先して検討する
   - 対応付けられない場合は、`critique`を参考情報として扱う
8. auto mode classifierの挙動を合否条件にする場合は、安全に反復できる具体的な検証操作と期待結果を先に確定する

## 既知の誤拒否パターンと対応

次の事例を実機で観測した。
分類名は`claude auto-mode defaults`の出力で確認できる区分を指す。

| 拒否される操作 | 分類名 | 対応 |
| --- | --- | --- |
| 自身が作成したHEADへの`git commit --amend` | Git Destructive | 該当範囲を狭く許容するルールを追加する |
| `exit-session`からの`kill -TERM $PPID` | Interfere With Workloads | transcriptの直前のツール呼び出しを条件とするルールを追加する |
| リリースワークフローの起動 | `Production Deploy`が有力候補（拒否本文では未取得） | 設定に`Release Workflow Dispatch`が存在する場合、個人リポジトリの`release.yaml`起動に限定して同ルールを使う |
| 承認ゲート緩和・規範改訂・設定原本変更を含むコミット | Self Modification | フィードバック処理由来に限定するルールを追加する |
| MR/PRのマージ（`glab mr merge`・`gh pr merge`等） | Merge Without Review | マージ操作を無条件に許可するルールを追加する（必須レビュー・チェックの迂回形態とhard_deny領域は対象外のまま） |
| ユーザーの指示を反映しない拒否後の再発行 | Auto-Mode Bypass等 | `Reconsidered Retry Approval`により拒否本文とユーザーメッセージを照合し、同一のコマンド・引数・ツールを1回だけ再発行する |

- `git commit --amend`はデフォルトの`soft_deny`が自身の作成したHEADへのamendを`clears`するが、
  別判断軸（`autonomous post-review cleanup`など）で拒否される場合がある
- Self Modificationの許可ルールは、正規のフィードバック処理フロー由来・ユーザー投入フィードバック限定
  （自己生成起点を除外）・計画レビュー工程経由を条件とする
- マージ許可ルールは承認条件を付けない無条件許可とする（ユーザー意向、2026-08）。
  フィードバック本文・TBD回答による承認はtranscript外の実体でありclassifierが参照できないため、
  承認条件付きのルールでは承認済みマージの再拒否が残る。
  必須レビュー・チェックの迂回形態（`--admin`・`--force`等）はCI Bypass領域として対象外を維持する
- リリースワークフロー起動の拒否本文には分類名が含まれないため、`Production Deploy`は既定の
  `soft_deny`との対応から見た有力候補にとどまり、確認済みの分類名として扱わない
- `Release Workflow Dispatch`は`autoMode.environment`の信頼境界と一致する個人リポジトリに限定し、
  `hard_deny`を上書きしない
- 分類名を取得できない場合は、拒否メッセージ本文を根拠として後掲のユーザー確認へ進む。
  `claude auto-mode defaults`自体が拒否されて分類名を確認できない場合も同じ扱いとする
- `kill -TERM $PPID`のルールは、transcriptの直前のツール呼び出しがこのエージェントによる
  `agent-toolkit:exit-session`のSkill呼び出しである場合だけ適用する。
  チェーン演算子や他の対象を含まない単独実行に限定し、他プロセスへのシグナル送出には適用しない
- `Reconsidered Retry Approval`による1回の再発行後も拒否が続く場合は`AskUserQuestion`で当該判定が偽陽性かを明示的に問い、
  偽陽性である旨の回答を得てから再試行する（進行への同意のみでは`clears`されない）
- `Reconsidered Retry Approval`は、拒否理由がtranscript内のユーザーによる当該操作の明示指示または承認を
  反映していない場合に限り、拒否メッセージ本文と該当するユーザーメッセージを照合して適用する

### 偽陽性と判断できる拒否への対応

auto mode classifierによる拒否は対象操作の実行自体を妨げる技術的ブロックであり、TBD記録では解消できないため、
自律モード・協調モードのいずれでも本フローの`AskUserQuestion`を発行する。
本フローは`Reconsidered Retry Approval`で許可された同一操作の1回の再発行後も拒否が残る場合に適用する。

メインエージェント・サブエージェントいずれが発行した操作にも適用する。
対象操作は`git commit`・`git commit --amend`・`Write`／`Edit`／`MultiEdit`によるファイル編集、
GitHubリポジトリ設定変更等の外部サービスの設定変更コマンドを含む（列挙は代表例であり網羅列挙ではない）。
これらがauto mode classifierに拒否される場合がある。
拒否理由が「クロスセッションのteammate messageのみに基づく」等、
有効な指示に対する偽陽性判定である可能性が高い場合の対応フロー。
適用条件は、ユーザーまたは処理中のフィードバックで当該操作が承認済みであると実体確認でき、
拒否理由がその承認を反映していないと判断できる場合に限る。hard_deny領域には適用しない。

1. 拒否理由をメッセージ本文で確認する。
   拒否分類名（`claude auto-mode defaults`出力で確認可能な分類名）を取得できる場合は
   分類名とメッセージ本文を記録する。
   `claude auto-mode defaults`自体が拒否され分類名を取得できない場合は、
   拒否メッセージ本文を根拠として次のステップ2のユーザー確認へ進む
2. 偽陽性判定である可能性が高い場合、`AskUserQuestion`で次の選択肢を提示する。
   拒否経緯の説明・選択肢の説明文は要点のみとし、判断材料の詳細はターン内のテキスト出力側へ置く
   （auto mode classifierの拒否はほとんどが偽陽性であり、確認自体を簡潔に保つ）
   - 偽陽性として実行を承認する
   - 該当操作を撤回する
   - サブエージェント側でレビューを完了してから判断する
3. ユーザー選択に応じて次の後続動作を実行する
   - 承認を選んだ場合はメイン側で該当操作を直接実行する
   - 撤回を選んだ場合は該当操作を破棄する。委譲経路で発行された操作の場合はサブエージェントへ
     撤回した旨と再開時の条件を返却する
   - レビュー完了待ちを選んだ場合はサブエージェント側のレビューを起動し、
     完了報告の受領後に再度本フローの1へ戻り拒否再現の有無を確認する
4. `AskUserQuestion`が無応答のまま期限を迎えた場合は、対象操作を保留して再試行しない。
   回答を待つためのTBDを記録し、解除条件を満たすまで代替経路だけを継続する
5. 無応答後にTBDへ回答が保存されても、それだけではclassifierが観測する承認にならない。
   transcript内で同じ操作への再確認を発行し、ユーザーの回答を受領したことを確認してから、
   本フローの1へ戻って拒否の有無を再確認する。
   `Reconsidered Retry Approval`による1回の再発行を終えた後は、再確認前の再試行を禁止する

## カスタムルール記述の注意点

- 適用対象（対象操作・対象コミットの性質・対象パスなど）を文面に明示する
- 適用対象を限定する条件をルール文面に組み込み、デフォルトの安全境界を維持する
  - 例:「同一セッション内でエージェントが作成したコミット限定」「`origin`への通常pushに限定」など、対象を狭める条件を含める
  - ユーザー意図に依存する条件（「計画・指示で承認された場合」など）はUser Intent Ruleに任せる領域と重複する
  - ルール側では対象を限定する条件を優先する
- デフォルトの`hard_deny`領域を上書きするカスタムルールは追加しない（auto modeの安全境界を逸脱するため）
- 自作allowルールが参照する信頼対象（個人リポジトリ・信頼ドメイン等）は`autoMode.environment`の
  信頼境界宣言と整合させる。allowルールだけで表現すると内容系判定が未設定既定へフォールバックする
- `claude auto-mode critique`の出力は非決定的で、末尾が欠落する場合を実測している（exit 0のまま文中切断）。
  出力はファイルへ保存して欠落の有無を確認し、欠落時は再実行で補完する
- `claude auto-mode critique`の指摘の採否は、`カスタムルール追加のワークフロー`の手順7に従う
